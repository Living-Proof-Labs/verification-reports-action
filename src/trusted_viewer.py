# SPDX-License-Identifier: Apache-2.0
"""Stage caller-pinned inputs and invoke Reporter; this module renders no HTML."""

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

from reports_common import SiteError

DIRECTORY = "trusted-viewer"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_file(path):
    if path.is_symlink() or not path.is_file():
        raise SiteError(f"Expected regular file: {path.name}")
    return path.read_bytes()


def load_viewers(filename, declarations=(), *, historical=False, publication=False):
    """Executable bytes and evaluator identities come only from the caller."""
    from pathlib import Path

    from viewer_release import fetch_release_viewer

    if not filename:
        return []
    location = Path(filename)
    entries = json.loads(read_file(location))
    seen = set()
    selected = []
    for entry in entries:
        if not isinstance(entry.get("recording_format"), str) or not entry["recording_format"]:
            raise SiteError("Caller must declare recording format")
        identity = entry["evaluator"]
        expected = {"id", "publisher", "kind"} | ({"version"} if historical else set())
        if set(identity) != expected or identity["kind"] != "advisory":
            raise SiteError("Expected exact advisory evaluator identity")
        if identity["id"] in seen:
            raise SiteError("Duplicate viewer evaluator declaration")
        seen.add(identity["id"])
        if not historical and not publication:
            repository = entry.get("evaluator_repository")
            descriptor = entry.get("evaluator_descriptor")
            if not isinstance(repository, str) or not isinstance(descriptor, str):
                raise SiteError("Fresh viewer requires an evaluator repository and descriptor path")
            targeted = [
                line
                for line in declarations
                if line.startswith(repository + "@") and line.endswith("/" + descriptor)
            ]
            if not targeted:
                continue  # This evaluator is optional and not selected by the caller.
            matches = []
            for line in targeted:
                match = re.fullmatch(r"([^@]+)@v([0-9]+\.[0-9]+\.[0-9]+)/(.+)", line)
                if match and match[1] == repository and match[3] == descriptor:
                    matches.append((line, match[2]))
            if len(matches) != 1:
                raise SiteError(
                    "Fresh viewer requires one stable version-pinned evaluator declaration"
                )
            line, version = matches[0]
            entry["evaluator"] = dict(identity, version=version)
            entry["declaration_line"] = line
        raw = fetch_release_viewer(entry)
        entry["bytes"] = raw
        selected.append(entry)
    return selected


def load_history_viewers(filename):
    """Independent caller-reviewed allowlist, never recovered from published data."""
    entries = load_viewers(filename, historical=True)
    for entry in entries:
        trials = entry.get("approved_trials")
        if not isinstance(trials, list) or not trials:
            raise SiteError("Historical viewer approval requires explicit trial identities")
        for trial in trials:
            if (
                not isinstance(trial, dict)
                or set(trial) != {"run_id", "configuration", "index", "observation_sha256"}
                or type(trial.get("index")) is not int
                or trial["index"] <= 0
                or not all(
                    isinstance(trial.get(k), str) and trial[k]
                    for k in ("run_id", "configuration", "observation_sha256")
                )
                or not re.fullmatch(r"[a-f0-9]{64}", trial["observation_sha256"])
            ):
                raise SiteError("Invalid historical trial approval")
    return entries


def render_fresh(folder, viewers, *, command=None, existing=False):
    """Render a new run through the installed pinned Reporter, then move output.

    No mutation occurs until Reporter has validated all selected inputs.
    The published portable report and raw recording are never rewritten here.
    """
    if not viewers:
        return False
    verify_manifest(folder)
    report = json.loads(read_file(folder / "report.json"))
    documents = report.get("source_bundle", {}).get("assessment", {}).get("advisory_results", {})
    if not isinstance(documents, dict):
        raise SiteError("Expected advisory result mapping")
    matched = []
    for entry in viewers:
        identity = entry["evaluator"]
        docs = [
            d
            for d in documents.values()
            if isinstance(d, dict)
            and isinstance(d.get("evaluator"), dict)
            and d["evaluator"].get("id") == identity["id"]
        ]
        if not docs:
            continue
        if (
            existing
            and len(docs) == 1
            and any(docs[0]["evaluator"].get(k) != v for k, v in identity.items())
        ):
            continue  # History can contain several independently approved versions.
        if len(docs) != 1 or any(docs[0]["evaluator"].get(k) != v for k, v in identity.items()):
            raise SiteError("Viewer requires exact unambiguous evaluator identity")
        matched.append((entry, docs[0]))
    if not matched:
        return False
    if len(matched) != 1:
        raise SiteError("Only one viewer per report is supported")
    entry, document = matched[0]
    if not existing:
        line = entry.get("declaration_line")
        provenance = (
            report.get("source_bundle", {})
            .get("assessment", {})
            .get("advisory_provenance", {})
            .get(entry["evaluator"]["id"], {})
        )
        if (
            not line
            or provenance.get("line") != line
            or provenance.get("repo") != entry.get("evaluator_repository")
            or provenance.get("ref") != "v" + entry["evaluator"]["version"]
            or provenance.get("installed_version") != entry["evaluator"]["version"]
            or provenance.get("outcome") != "result"
            or provenance.get("exit_code") != 0
            or not re.fullmatch(r"[a-f0-9]{40}", provenance.get("commit", ""))
            or not re.fullmatch(r"[a-f0-9]{64}", provenance.get("descriptor_sha256", ""))
        ):
            raise SiteError("Fresh result provenance differs from the caller evaluator declaration")
    identity = entry["evaluator"]
    details = document["advisory"]["details_file"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.json", details["uri"]):
        raise SiteError("Expected local recording JSON basename")
    raw = read_file(folder / details["uri"])
    if digest(raw) != details["sha256"] or digest(entry["bytes"]) != entry["viewer_sha256"]:
        raise SiteError("Viewer or recording digest mismatch")
    if not existing and ((folder / DIRECTORY).exists() or (folder / DIRECTORY).is_symlink()):
        raise SiteError("Fresh viewer installation refuses existing assets")
    pin = dict(
        evaluator_id=identity["id"],
        evaluator_version=identity["version"],
        evaluator_publisher=identity["publisher"],
        viewer_href=f"{DIRECTORY}/viewer.html",
        viewer_sha256=entry["viewer_sha256"],
        recording_format=entry["recording_format"],
    )
    with tempfile.TemporaryDirectory(prefix="vra-viewer-") as directory:
        staging = Path(directory)
        shutil.copyfile(folder / "report.json", staging / "report.json")
        (staging / details["uri"]).write_bytes(raw)
        target = staging / DIRECTORY
        (target / "data").mkdir(parents=True)
        (target / "viewer.html").write_bytes(entry["bytes"])
        (target / "data/recording.json").write_bytes(raw)
        from livingproof_capability_report import TrustedViewer
        from livingproof_capability_report.viewer_page import compose_viewer_page

        from reports_render import checked_report_html

        page = compose_viewer_page(
            read_file(folder / "index.html").decode("utf-8"),
            report,
            trusted_viewers=[TrustedViewer(**pin)],
            asset_root=staging,
            validate_shell=lambda value: checked_report_html(value, "existing shell"),
        )
        (staging / "index.html").write_text(page, encoding="utf-8")
        # Recheck all incoming bytes after the subprocess, before publishing output.
        verify_manifest(folder)
        if not existing:
            shutil.copytree(target, folder / DIRECTORY)
        shutil.copyfile(staging / "index.html", folder / "index.html")
    from reports_site import write_published_checksums

    write_published_checksums(folder)
    return True


def validate_restored(folder, viewers):
    """Admit only exact Reporter-generated content, with no file writes."""
    from livingproof_capability_report import TrustedViewer
    from livingproof_capability_report.viewer_page import validate_viewer_page

    from reports_render import checked_report_html

    verify_manifest(folder)
    target = folder / DIRECTORY
    if {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()} != {
        "viewer.html",
        "data/recording.json",
    }:
        raise SiteError("Unexpected file in trusted viewer assets")
    if not viewers:
        raise SiteError("Restored viewer requires current trusted caller pins")
    report = json.loads(read_file(folder / "report.json"))
    documents = report.get("source_bundle", {}).get("assessment", {}).get("advisory_results", {})
    recording = json.loads(read_file(target / "data/recording.json"))
    trials = recording.get("trials") if isinstance(recording, dict) else None
    if not isinstance(trials, list) or len(trials) != 1 or not isinstance(trials[0], dict):
        raise SiteError("Restored viewer requires one recorded trial")
    trial = trials[0].get("trial")
    if not isinstance(trial, dict) or type(trial.get("index")) is not int:
        raise SiteError("Invalid restored trial identity")
    asset_digest = digest(read_file(target / "viewer.html"))
    approved = []
    for v in viewers:
        identity = v["evaluator"]
        docs = [
            d
            for d in documents.values()
            if isinstance(d, dict)
            and isinstance(d.get("evaluator"), dict)
            and all(d["evaluator"].get(k) == value for k, value in identity.items())
        ]
        if (
            len(docs) == 1
            and v["viewer_sha256"] == asset_digest
            and trial in v.get("approved_trials", [])
        ):
            approved.append(v)
    if len(approved) != 1:
        raise SiteError("Restored executable lacks one exact evaluator/trial/asset approval")
    v = approved[0]
    pins = [
        TrustedViewer(
            evaluator_id=v["evaluator"]["id"],
            evaluator_version=v["evaluator"]["version"],
            evaluator_publisher=v["evaluator"]["publisher"],
            viewer_href=f"{DIRECTORY}/viewer.html",
            viewer_sha256=v["viewer_sha256"],
            recording_format=v["recording_format"],
        )
    ]
    # An absent evaluator yields no fragment in Reporter's optional API. Restoration
    # must require a real matched fragment, not treat a clean plain shell as approval.
    from livingproof_capability_report.trusted_viewer import viewer_panels

    if set(viewer_panels(pins, report, folder)) != {v["evaluator"]["id"]}:
        raise SiteError("Restored executable has no corresponding native panel")
    validate_viewer_page(
        read_file(folder / "index.html").decode("utf-8"),
        report,
        trusted_viewers=pins,
        asset_root=folder,
        validate_shell=lambda value: checked_report_html(value, "restored shell"),
    )


def discover_viewers(root):
    """Only standalone reports and the production g1 subtree are supported.

    Find all named asset directories first, so an unexpected prefix cannot evade
    authentication by falling outside a positive glob.
    """
    found = []
    for target in root.rglob(DIRECTORY):
        parts = target.relative_to(root).parts
        relative = parts[1:] if len(parts) == 6 and parts[0] == "g1" else parts
        if (
            len(relative) != 5
            or relative[2] != "runs"
            or relative[4] != DIRECTORY
            or not target.is_dir()
            or target.is_symlink()
        ):
            raise SiteError("Unsupported trusted viewer history layout")
        found.append(target)
    return sorted(found)


def validate_history(root, viewers):
    for target in discover_viewers(root):
        validate_restored(target.parent, viewers)


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


def publication_approvals(root, viewers):
    """Mint exact per-report approvals only for an authenticated extracted archive.

    Called exclusively by restore after the API/source/log/archive proof. The
    returned entries stay in memory; a file inside history cannot mint authority.
    Every approval still passes the normal complete manifest and native panel check.
    """
    import copy

    approved = []
    for target in discover_viewers(root):
        folder = target.parent
        verify_manifest(folder)
        report = json.loads(read_file(folder / "report.json"))
        assessment = report.get("source_bundle", {}).get("assessment", {})
        results = assessment.get("advisory_results", {})
        candidates = []
        for pin in viewers:
            identity = pin["evaluator"]
            for result in results.values():
                actual = result.get("evaluator", {})
                if not all(actual.get(k) == v for k, v in identity.items()):
                    continue
                version = actual.get("version", "")
                if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
                    raise SiteError("Published evaluator version is not stable")
                repo = pin.get("evaluator_repository")
                descriptor = pin.get("evaluator_descriptor")
                if not isinstance(repo, str) or not isinstance(descriptor, str):
                    raise SiteError("Publication approval requires stable repository/descriptor")
                line = f"{repo}@v{version}/{descriptor}"
                provenance = assessment.get("advisory_provenance", {}).get(identity["id"], {})
                if (
                    provenance.get("line") != line
                    or provenance.get("repo") != repo
                    or provenance.get("ref") != "v" + version
                    or provenance.get("installed_version") != version
                    or provenance.get("outcome") != "result"
                    or provenance.get("exit_code") != 0
                    or not re.fullmatch(r"[a-f0-9]{40}", provenance.get("commit", ""))
                    or not re.fullmatch(r"[a-f0-9]{64}", provenance.get("descriptor_sha256", ""))
                ):
                    raise SiteError("Published evaluator provenance is not approved")
                recording = json.loads(read_file(target / "data/recording.json"))
                trials = recording.get("trials")
                if not isinstance(trials, list) or len(trials) != 1:
                    raise SiteError("Publication approval requires one trial")
                entry = copy.deepcopy(pin)
                entry["evaluator"] = dict(identity, version=version)
                entry["approved_trials"] = [trials[0].get("trial")]
                candidates.append(entry)
        validate_restored(folder, candidates)
        for entry in candidates:
            same = [
                v
                for v in approved
                if v["evaluator"] == entry["evaluator"]
                and v["viewer_sha256"] == entry["viewer_sha256"]
            ]
            if same:
                for trial in entry["approved_trials"]:
                    if trial not in same[0]["approved_trials"]:
                        same[0]["approved_trials"].append(trial)
            else:
                approved.append(entry)
    return approved
