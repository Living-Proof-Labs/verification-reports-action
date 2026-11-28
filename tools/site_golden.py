#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build the deterministic two-build fixture site and print or check its SHA256SUMS.

The golden file pins the generated `reports/` byte for byte, so a refactor that drops,
duplicates, or rewrites markup, styles, or scripts fails `pytest` instead of shipping.
Regenerate it with `python3 tools/site_golden.py write` only for an intended change.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]

import reports_site  # noqa: E402
import site_fixture  # noqa: E402

GOLDEN = ROOT / "tests/fixtures/site-golden/SHA256SUMS"


def build_golden_site(work: Path) -> Path:
    reports = work / "reports"
    site_fixture.write(work / "in1", run_id="100", created="2026-11-20T06:00:00Z")
    site_fixture.write(
        work / "in2",
        run_id="200",
        created="2026-11-29T06:00:00Z",
        commit="b" * 40,
        tag="v2.3.33",
    )
    caller = "Living-Proof-Labs/livingproof-unitree-g1"
    reports_site.build(reports, work / "in1", repository=caller)
    reports_site.build(reports, work / "in2", days=7, repository=caller)
    return reports


def digests(reports: Path) -> str:
    return "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(reports).as_posix()}\n"
        for path in sorted(reports.rglob("*"))
        if path.is_file()
    )


def main(argv: list[str]) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        text = digests(build_golden_site(Path(tmp)))
    if argv[1:] == ["write"]:
        GOLDEN.write_text(text, encoding="utf-8")
        return 0
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
