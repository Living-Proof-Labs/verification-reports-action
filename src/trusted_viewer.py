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


def load_viewers(filename):
    """Executable bytes and evaluator identities come only from the caller."""
    from pathlib import Path

    from viewer_release import fetch_release_viewer

    if not filename:
        return []
    location = Path(filename)
    entries = json.loads(read_file(location))
    seen = set()
    for entry in entries:
        if not isinstance(entry.get("recording_format"), str) or not entry["recording_format"]:
            raise SiteError("Caller must declare recording format")
        identity = entry["evaluator"]
        if (
            set(identity) != {"id", "version", "publisher", "kind"}
            or identity["kind"] != "advisory"
        ):
            raise SiteError("Expected exact advisory evaluator identity")
        if identity["id"] in seen:
            raise SiteError("Duplicate viewer evaluator declaration")
        seen.add(identity["id"])
        raw = fetch_release_viewer(entry)
        entry["bytes"] = raw
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
        if len(docs) != 1 or any(docs[0]["evaluator"].get(k) != v for k, v in identity.items()):
            raise SiteError("Viewer requires exact unambiguous evaluator identity")
        matched.append((entry, docs[0]))
    if not matched:
        return False
    if len(matched) != 1:
        raise SiteError("Only one viewer per report is supported")
    entry, document = matched[0]
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
    pins = [
        TrustedViewer(
            evaluator_id=v["evaluator"]["id"],
            evaluator_version=v["evaluator"]["version"],
            evaluator_publisher=v["evaluator"]["publisher"],
            viewer_href=f"{DIRECTORY}/viewer.html",
            viewer_sha256=v["viewer_sha256"],
            recording_format=v["recording_format"],
        )
        for v in viewers
    ]
    validate_viewer_page(
        read_file(folder / "index.html").decode("utf-8"),
        json.loads(read_file(folder / "report.json")),
        trusted_viewers=pins,
        asset_root=folder,
        validate_shell=lambda value: checked_report_html(value, "restored shell"),
    )


def validate_history(root, viewers):
    for target in root.glob("*/*/runs/*/trusted-viewer"):
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
