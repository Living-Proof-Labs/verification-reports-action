# SPDX-License-Identifier: Apache-2.0
"""Stage caller-pinned inputs and invoke Reporter; this module renders no HTML."""

import hashlib
import json
import re
import shutil
import subprocess
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


def render_fresh(folder, viewers, *, command=("livingproof-capability-report",)):
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
    if (folder / DIRECTORY).exists() or (folder / DIRECTORY).is_symlink():
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
        (staging / "pins.json").write_text(json.dumps([pin]))
        result = subprocess.run(
            [
                *command,
                "render",
                str(staging / "report.json"),
                "--format",
                "html",
                "--trusted-viewers",
                str(staging / "pins.json"),
                "--asset-root",
                str(staging),
                "--output",
                str(staging / "index.html"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise SiteError(f"Pinned Reporter refused viewer: {result.stderr.strip()}")
        # Recheck all incoming bytes after the subprocess, before publishing output.
        verify_manifest(folder)
        shutil.copytree(target, folder / DIRECTORY)
        shutil.copyfile(staging / "index.html", folder / "index.html")
    from reports_site import write_published_checksums

    write_published_checksums(folder)
    return True


def refuse_decorated_history(root):
    # No historical regeneration or silent removal. Restoration needs a separately
    # reviewed Reporter validation interface before this local candidate can ship.
    if any(root.glob("*/*/runs/*/trusted-viewer")):
        raise SiteError("Restored trusted viewers require Reporter-owned validation")


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
