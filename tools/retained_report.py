# SPDX-License-Identifier: Apache-2.0
"""Download an explicitly pinned successful main Verify bundle; never dispatch CI."""

import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = "Living-Proof-Labs/verification-reports-action"
LIMIT = 256 * 1024 * 1024


def validate(pin, run, artifact, workflow):
    if any(type(pin[key]) is not int or pin[key] <= 0 for key in ("run", "artifact")):
        raise ValueError("Expected numeric retained run and artifact IDs")
    if artifact.get("size_in_bytes", 0) > LIMIT:
        raise ValueError("Retained artifact exceeds size limit")
    if not re.fullmatch(r"[a-f0-9]{40}", pin["commit"]):
        raise ValueError("Expected exact retained source commit")
    if not re.fullmatch(r"[a-f0-9]{64}", pin["sha256"]):
        raise ValueError("Expected artifact SHA-256")
    expected = {
        "id": pin["run"],
        "head_sha": pin["commit"],
        "event": "push",
        "head_branch": "main",
        "status": "completed",
        "conclusion": "success",
        "workflow_id": workflow["id"],
    }
    if any(run.get(k) != v for k, v in expected.items()):
        raise ValueError("Retained source must be the pinned successful main Verify run")
    if run.get("head_repository", {}).get("full_name") != REPOSITORY:
        raise ValueError("Retained source repository mismatch")
    if (
        artifact.get("id") != pin["artifact"]
        or artifact.get("expired") is not False
        or artifact.get("name") != "g1-verification-report"
        or artifact.get("workflow_run", {}).get("id") != pin["run"]
        or artifact.get("digest") != "sha256:" + pin["sha256"]
    ):
        raise ValueError("Artifact identity, expiry, or digest mismatch")


def extract(raw, pin, output):
    if len(raw) > LIMIT or hashlib.sha256(raw).hexdigest() != pin["sha256"]:
        raise ValueError("Retained archive digest or size mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        if sum(m.file_size for m in members) > LIMIT or len(members) > 10000:
            raise ValueError("Retained archive exceeds limits")
        seen = set()
        for member in members:
            path = PurePosixPath(member.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in member.filename
                or any(ord(c) < 32 for c in member.filename)
                or member.filename in seen
                or stat.S_ISLNK(member.external_attr >> 16)
            ):
                raise ValueError("Unsafe retained archive path")
            seen.add(member.filename)
        output.mkdir(parents=True, exist_ok=False)
        for member in members:
            target = output / member.filename
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))


def main():
    filename = sys.argv[1]
    if not re.fullmatch(r"config/retained/[A-Za-z0-9_-]+\.json", filename):
        raise ValueError("Select a reviewed config/retained/*.json from released code")
    pin = json.loads(Path(filename).read_text())

    def api(path):
        return json.loads(subprocess.check_output(["gh", "api", f"repos/{REPOSITORY}/{path}"]))

    validate(
        pin,
        api(f"actions/runs/{pin['run']}"),
        api(f"actions/artifacts/{pin['artifact']}"),
        api("actions/workflows/verify.yml"),
    )
    raw = subprocess.check_output(
        ["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{pin['artifact']}/zip"]
    )
    extract(raw, pin, Path(sys.argv[2]))
    viewers = pin.get("trusted_viewers", "")
    if viewers and not re.fullmatch(r"config/retained/[A-Za-z0-9_-]+\.json", viewers):
        raise ValueError("Viewer registry must belong to released caller code")
    with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
        handle.write("viewers=" + viewers + "\n")


if __name__ == "__main__":
    main()
