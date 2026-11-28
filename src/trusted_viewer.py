# SPDX-License-Identifier: Apache-2.0
"""Caller-pinned embeds, applied only after ordinary report sanitation.

No evaluator supplies executable content. Restore requires the current caller pin,
not a trust decision based on a manifest recovered from an old Pages artifact.
"""

import hashlib
import html
import json
import re
from urllib.parse import urlencode

from reports_common import SiteError
from reports_render import checked_report_html

DIRECTORY = "trusted-viewer"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_file(path):
    if path.is_symlink() or not path.is_file():
        raise SiteError(f"Expected regular file: {path.name}")
    return path.read_bytes()


def binding(folder, pin):
    for key in ("viewer_sha256", "report_sha256"):
        if not re.fullmatch(r"[a-f0-9]{64}", pin[key]):
            raise SiteError("Caller must pin exact viewer and report SHA-256")
    if not re.fullmatch(r"[a-f0-9]{40}", pin["viewer_commit"]) or not pin["viewer_tag"].startswith(
        "v"
    ):
        raise SiteError("Caller must identify immutable viewer source")
    report_bytes = read_file(folder / "report.json")
    if digest(report_bytes) != pin["report_sha256"]:
        raise SiteError("Report differs from caller-selected report")
    report = json.loads(report_bytes)
    results = [r for r in report["source_bundle"]["results"] if r["id"] == pin["result_id"]]
    if len(results) != 1:
        raise SiteError("Expected one selected report result")
    result = results[0]
    if result["provenance"]["run_id"] != pin["run"] or result["system"]["id"] != pin["system"]:
        raise SiteError("Selected run/system mismatch")
    if pin["system"].rsplit("/", 1)[-1] != pin["configuration"]:
        raise SiteError("Selected configuration mismatch")
    found = []

    def visit(node):
        if isinstance(node, dict):
            if (
                isinstance(node.get("evaluator"), dict)
                and node["evaluator"].get("id") == pin["evaluator_id"]
                and "advisory" in node
            ):
                found.append(node["advisory"])
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(report)
    unique = {json.dumps(a, sort_keys=True): a for a in found}
    if len(unique) != 1:
        raise SiteError("Expected one unambiguous advisory")
    advisory = next(iter(unique.values()))
    trials = [t for t in advisory["trials"] if t["index"] == pin["trial"]]
    if len(trials) != 1 or trials[0]["observation_sha256"] not in {
        e["sha256"] for e in result["evidence"]
    }:
        raise SiteError("Advisory trial is not bound to the selected report evidence")
    details = advisory["details_file"]
    uri = details["uri"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.json", uri):
        raise SiteError("Expected a local JSON basename")
    raw = read_file(folder / uri)
    if digest(raw) != details["sha256"]:
        raise SiteError("Details differ from report-published digest")
    params = urlencode(
        {
            "file": "data/recording.json",
            "sha256": details["sha256"],
            "run": pin["run"],
            "configuration": pin["configuration"],
            "trial": pin["trial"],
        }
    )
    frame = f'<iframe title="Recorded replay" sandbox="allow-scripts allow-same-origin" loading="lazy" src="{DIRECTORY}/viewer.html#{html.escape(params, quote=True)}" style="display:block;width:100%;height:660px;border:1px solid #bcc;max-width:100%"></iframe>'  # noqa: E501
    return uri, raw, frame


def install(folder, pin, viewer_bytes):
    """Apply once to a copied, retained run; never alter report JSON or raw data."""
    if digest(viewer_bytes) != pin["viewer_sha256"]:
        raise SiteError("Viewer differs from trusted caller pin")
    uri, raw, frame = binding(folder, pin)
    original = read_file(folder / "index.html").decode("utf-8")
    clean = checked_report_html(original, "incoming report")  # Arbitrary iframe still fails.
    anchor = f"<a href='{html.escape(uri, quote=True)}'>Recorded data</a>"
    if clean.count(anchor) != 1:
        raise SiteError("Expected one existing raw-data link; ambiguous cards refused")
    target = folder / DIRECTORY
    target.mkdir()  # Refuse overwrite or existing symlink/directory.
    (target / "data").mkdir()
    (target / "viewer.html").write_bytes(viewer_bytes)
    (target / "data/recording.json").write_bytes(raw)
    (target / "sanitized-source.txt").write_text(clean, encoding="utf-8")
    (folder / "index.html").write_text(clean.replace(anchor, anchor + frame), encoding="utf-8")
    revalidate(folder, pin)


def revalidate(folder, pin):
    """Validate an existing embed using a freshly supplied caller pin on restore."""
    uri, raw, frame = binding(folder, pin)
    target = folder / DIRECTORY
    if target.is_symlink() or (target / "data").is_symlink():
        raise SiteError("Viewer directories cannot be symlinks")
    if digest(read_file(target / "viewer.html")) != pin["viewer_sha256"]:
        raise SiteError("Restored viewer differs from caller pin")
    if read_file(target / "data/recording.json") != raw:
        raise SiteError("Restored viewer data differs from the report")
    page = read_file(folder / "index.html").decode("utf-8")
    if page.count(frame) != 1:
        raise SiteError("Restored iframe differs from site-generated binding")
    source = page.replace(frame, "")
    if source != read_file(target / "sanitized-source.txt").decode("utf-8"):
        raise SiteError("Restored page changed outside generated embed")
    if checked_report_html(source, "restored source") != source:
        raise SiteError("Restored source contains unrecognized script")
    if any(p.is_symlink() for p in target.rglob("*")):
        raise SiteError("Symlink in trusted viewer installation")
    allowed = {"viewer.html", "sanitized-source.txt", "data/recording.json"}
    if {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()} != allowed:
        raise SiteError("Unexpected file in trusted viewer installation")
    return source


def load_viewers(filename):
    """Executable bytes and evaluator identities come only from the caller."""
    from pathlib import Path

    if not filename:
        return []
    location = Path(filename)
    entries = json.loads(read_file(location))
    seen = set()
    for entry in entries:
        identity = entry["evaluator"]
        if (
            set(identity) != {"id", "version", "publisher", "kind"}
            or identity["kind"] != "advisory"
        ):
            raise SiteError("Expected exact advisory evaluator identity")
        if identity["id"] in seen:
            raise SiteError("Duplicate viewer evaluator declaration")
        seen.add(identity["id"])
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.html", entry["viewer_file"]):
            raise SiteError("Viewer must be adjacent to caller registry")
        raw = read_file(location.parent / entry["viewer_file"])
        if digest(raw) != entry["viewer_sha256"]:
            raise SiteError("Viewer differs from caller pin")
        entry["bytes"] = raw
    return entries


def derive_declarations(root, viewers):
    """Select only one evidence-bound result/trial from each actual report."""
    declarations = {}
    for folder in root.glob("*/*/runs/*"):
        raw = read_file(folder / "report.json")
        report = json.loads(raw)
        for viewer in viewers:
            identity = viewer["evaluator"]
            found = []

            def visit(node):
                if isinstance(node, dict):
                    evaluator = node.get("evaluator")
                    if (
                        isinstance(evaluator, dict)
                        and evaluator.get("id") == identity["id"]
                        and "advisory" in node
                    ):
                        if any(evaluator.get(k) != v for k, v in identity.items()):
                            raise SiteError("Advisory evaluator identity differs from caller")
                        found.append(node["advisory"])
                    for child in node.values():
                        visit(child)
                elif isinstance(node, list):
                    for child in node:
                        visit(child)

            visit(report)
            unique = {json.dumps(a, sort_keys=True): a for a in found}
            if not unique:
                continue
            if len(unique) != 1:
                raise SiteError("Ambiguous advisory data")
            advisory = next(iter(unique.values()))
            trials = advisory.get("trials", [])
            if len(trials) != 1:
                raise SiteError("Viewer requires one unambiguous trial")
            trial = trials[0]
            matches = [
                r
                for r in report["source_bundle"]["results"]
                if trial["observation_sha256"] in {e["sha256"] for e in r["evidence"]}
            ]
            if len(matches) != 1:
                raise SiteError("Viewer requires one evidence-bound report result")
            result = matches[0]
            system = result["system"]["id"]
            pin = {k: viewer[k] for k in ("viewer_sha256", "viewer_tag", "viewer_commit")}
            pin.update(
                report_sha256=digest(raw),
                evaluator_id=identity["id"],
                result_id=result["id"],
                run=result["provenance"]["run_id"],
                system=system,
                configuration=system.rsplit("/", 1)[-1],
                trial=trial["index"],
            )
            binding(folder, pin)  # Validate the published details digest before modification.
            key = folder.relative_to(root).as_posix()
            if key in declarations:
                raise SiteError("Multiple viewers for one report are not supported")
            declarations[key] = (pin, viewer["bytes"])
    return declarations


def prepare_history(root, declarations):
    """Validate then remove generated embeds before ordinary restore/build validation."""
    import shutil

    from reports_site import write_published_checksums

    pending = []
    for target in root.glob("*/*/runs/*/trusted-viewer"):
        folder = target.parent
        key = folder.relative_to(root).as_posix()
        if key not in declarations:
            raise SiteError("Restored viewer has no current caller declaration")
        verify_manifest(folder)
        pin, _ = declarations[key]
        pending.append((folder, target, revalidate(folder, pin)))
    # Validate every incoming run before modifying any of them.
    for folder, target, source in pending:
        (folder / "index.html").write_text(source, encoding="utf-8")
        shutil.rmtree(target)
        write_published_checksums(folder)


def apply_declarations(root, declarations):
    """Decorate only sanitized published pages, then regenerate their checksums."""
    from reports_site import write_published_checksums

    for relative, (pin, viewer) in declarations.items():
        folder = root / relative
        if not folder.exists():
            continue  # Optional contribution for a run not in retained history.
        if any(part.is_symlink() for part in [folder, *folder.parents]):
            raise SiteError("Declared viewer path cannot contain a symlink")
        install(folder, pin, viewer)
        write_published_checksums(folder)


def verify_manifest(folder):
    """Verify the complete incoming file set before regenerating any checksums."""
    from pathlib import PurePosixPath

    if any(p.is_symlink() for p in [folder, *folder.parents, *folder.rglob("*")]):
        raise SiteError("Symlink in retained history")
    manifest = read_file(folder / "SHA256SUMS")
    names = set()
    for line in manifest.decode("utf-8").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  (.+)", line)
        if not match:
            raise SiteError("Malformed published manifest")
        expected, name = match.groups()
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or path.as_posix() != name
            or name in names
            or name == "SHA256SUMS"
        ):
            raise SiteError("Unsafe published manifest path")
        names.add(name)
        if digest(read_file(folder / name)) != expected:
            raise SiteError("Published file digest mismatch")
    actual = {
        p.relative_to(folder).as_posix()
        for p in folder.rglob("*")
        if p.is_file() and p != folder / "SHA256SUMS"
    }
    if actual != names:
        raise SiteError("Published file set differs from manifest")
    return manifest
