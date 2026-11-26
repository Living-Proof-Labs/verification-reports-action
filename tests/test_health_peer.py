"""Released API replay of unchanged, authenticated hosted archives through ./site."""

import hashlib
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures/health-peer"


def test_released_peer_replay_and_history(tmp_path):
    for name, digest in {
        "g1-report": "dd0f42c25b3ed235ccda95b311edbebc94717fdf240b723058e46fefb4aba71e",
        "reporter-original": "a14d7d6a106d4ef52da5af419d43ae43d712c166eaa7a6ce7b8bc062fa978e8c",
    }.items():
        archive = FIXTURE / (name + ".zip")
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(tmp_path / name)
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/prove_health_replay.py"),
            "--g1-input",
            str(tmp_path / "g1-report"),
            "--reporter-input",
            str(tmp_path / "reporter-original"),
            "--python",
            sys.executable,
            "--output",
            str(tmp_path / "replay"),
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/prove_health_history.py"),
            "--published-site",
            str(tmp_path / "replay/on/site"),
            "--output",
            str(tmp_path / "history"),
        ],
        check=True,
    )
    if os.environ.get("HEALTH_BROWSER_PROOF") == "1":
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/prove_health_browser.py"),
                "--replay",
                str(tmp_path / "replay"),
                "--baseline-page",
                str(FIXTURE / "baseline.html"),
                "--output",
                str(ROOT / "artifacts/health-peer-browser"),
            ],
            check=True,
        )
