"""Restore an already-built genuine released-API page via the real site entry point.

Only the GitHub transport is replaced with a local Pages ZIP; no live deploy.
"""

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / name) for name in ("src", "tests", "tools")]
import pytest  # noqa: E402

import reports_site  # noqa: E402
from test_health_display import unpack  # noqa: E402
from test_health_history import invoke, next_input, snapshot, transport  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--published-site", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.mkdir()
docs = args.output / "docs"
shutil.copytree(args.published_site, docs / "reports")
run = next((docs / "reports").glob("*/*/runs/*"))
assert "evaluator-livingproof-health" in (run / "index.html").read_text()
assert not list(run.glob(".report*"))
before = snapshot(run)
source = unpack(args.output)
later = next_input(args.output, source)
with pytest.MonkeyPatch.context() as patch:
    transport(patch, docs)
    invoke(args.output, later, args.output / "restored", restore=True)
restored = args.output / "restored" / run.relative_to(docs / "reports")
assert snapshot(restored) == before
print("Actual peer page restored byte-for-byte without private replay inputs")
reports_site.render(args.output / "restored", now="2027-02-01T00:00:00Z")
assert not (restored / "health.json").exists()
assert "evaluator-livingproof-health" not in (restored / "index.html").read_text()
reports_site.recheck_restored_run(restored)
print("Expired restored peer/data removed through existing retention; no stale result")
