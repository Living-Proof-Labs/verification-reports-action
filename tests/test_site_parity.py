# SPDX-License-Identifier: Apache-2.0
"""#75 step 3: the committed before/after compare of the deployed reports section."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARITY = ROOT / "tests/fixtures/site-parity"
CASES = (
    "golden",
    "g1-46115314157",
    "g1-46118968220",
    "g1-46122371783",
    "g1-history",
)


def test_the_site_is_byte_identical_before_and_after_the_move():
    for case in CASES:
        before = (PARITY / "before" / f"{case}.SHA256SUMS").read_text(encoding="utf-8")
        after = (PARITY / "after" / f"{case}.SHA256SUMS").read_text(encoding="utf-8")
        assert before == after, case
        assert before.strip(), case
    assert sorted(p.name for p in (PARITY / "before").iterdir()) == sorted(
        f"{case}.SHA256SUMS" for case in CASES
    )
    golden = (ROOT / "tests/fixtures/site-golden/SHA256SUMS").read_text(encoding="utf-8")
    assert (PARITY / "before/golden.SHA256SUMS").read_text(encoding="utf-8") == golden


def test_the_parity_script_rebuilds_the_golden_case_through_the_site_action(tmp_path: Path):
    subprocess.run(
        [sys.executable, str(ROOT / "tools/site_parity.py"), str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    built = (tmp_path / "golden.SHA256SUMS").read_text(encoding="utf-8")
    assert built == (PARITY / "after/golden.SHA256SUMS").read_text(encoding="utf-8")
