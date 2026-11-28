#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the deployed reports site through site/action.yml's entry point and list its digests.

va#75 moved the renderer into livingproof-capability-report. This script drives
`src/site_action.py` exactly as the composite action does (environment in, reports/ out,
restore off) for the fixture golden and for real G1 run artifacts, and writes one
SHA256SUMS list per case. Run it on the commit before the move and on the commit after;
the lists must be identical.

    python3 tools/site_parity.py OUT_DIR [--g1 RUN_ID=ARTIFACT_DIR ...]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import site_fixture  # noqa: E402

GOLDEN_CALLER = "Living-Proof-Labs/livingproof-unitree-g1"
G1_CALLER = "Living-Proof-Labs/verification-reports-action"


def digests(reports: Path) -> str:
    return "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(reports).as_posix()}\n"
        for path in sorted(reports.rglob("*"))
        if path.is_file()
    )


def site_action(workspace: Path, runs: Path, repository: str, run_id: str, days: str = "7"):
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", str(workspace)),
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_REPOSITORY": repository,
        "GITHUB_RUN_ID": run_id,
        "GITHUB_EVENT_NAME": "push",
        "INPUT_RUNS_DIR": str(runs),
        "INPUT_OUTPUT_DIR": "reports",
        "INPUT_EVIDENCE_RETENTION_DAYS": days,
        "INPUT_RESTORE_HISTORY": "false",
        "INPUT_UPLOAD_ARTIFACT": "false",
    }
    subprocess.run(
        [sys.executable, str(ROOT / "src/site_action.py")], env=env, cwd=workspace, check=True
    )
    return workspace / "reports"


def golden(work: Path) -> Path:
    site_fixture.write(work / "in1", run_id="100", created="2026-11-20T06:00:00Z")
    site_fixture.write(
        work / "in2",
        run_id="200",
        created="2026-11-29T06:00:00Z",
        commit="b" * 40,
        tag="v2.3.33",
    )
    site_action(work, work / "in1", GOLDEN_CALLER, "100")
    return site_action(work, work / "in2", GOLDEN_CALLER, "200")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--g1", action="append", default=[], metavar="RUN_ID=DIR")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "golden").mkdir()
        (args.out / "golden.SHA256SUMS").write_text(digests(golden(work / "golden")))
        history = work / "history"
        history.mkdir()
        for item in args.g1:
            run_id, _, folder = item.partition("=")
            one = work / f"g1-{run_id}"
            one.mkdir()
            shutil.copytree(folder, one / "in")
            reports = site_action(one, one / "in", G1_CALLER, run_id)
            (args.out / f"g1-{run_id}.SHA256SUMS").write_text(digests(reports))
            shutil.copytree(folder, history / f"in-{run_id}")
            reports = site_action(history, history / f"in-{run_id}", G1_CALLER, run_id)
        if args.g1:
            (args.out / "g1-history.SHA256SUMS").write_text(digests(reports))
    for path in sorted(args.out.glob("*.SHA256SUMS")):
        print(f"{path.name}: {len(path.read_text().splitlines())} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
