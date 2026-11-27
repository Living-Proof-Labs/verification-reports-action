# SPDX-License-Identifier: Apache-2.0
"""Refuse a link to the retired livingproof-verification repository or docs site.

Format identifiers (`livingproof-verification-definition/…`, `livingproof-verification-lock/…`)
and the User-Agent `livingproof-verification-reports` stay, and this pattern does not match them.
`tests/fixtures/old-runs/` is frozen and is not scanned.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"livingproof-verification(/|\]|\)|\.git| repo)")


def _sources() -> list[Path]:
    listed = subprocess.check_output(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "README.md",
            "docs",
            "src",
        ],
        cwd=ROOT,
        text=True,
    )
    files = []
    for line in listed.splitlines():
        if (
            line == "README.md"
            or (line.startswith("docs/") and line.endswith(".md"))
            or line.startswith("src/")
        ):
            files.append(ROOT / line)
    return files


def test_no_livingproof_verification_repo_or_docs_link():
    hits = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if PATTERN.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")
    assert hits == []
