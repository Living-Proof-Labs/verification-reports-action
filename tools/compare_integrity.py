# SPDX-License-Identifier: Apache-2.0
"""Compare Run Integrity from the declarative bundle with the previous command.

The pass/fail check runs livingproof-evaluator-run v1.9.0
(73176b6bf92067d805aaaa52b947cb5817976788) the previous way and diffs that
document against ``assessment.integrity_results``. Timestamps and version
stamps are ignored. The compared view is the run status, the percentage, and
the findings.

``--baseline`` prints a second diff and does not change the exit code. Use it
for an older evaluator-run result. Differences there are a version change.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

EVALUATOR_REPOSITORY = "https://github.com/Living-Proof-Labs/livingproof-evaluator-run.git"
EVALUATOR_COMMIT = "73176b6bf92067d805aaaa52b947cb5817976788"
TIME_KEYS = {"timestamps", "started_at", "completed_at", "generated_at", "created_at", "timestamp"}


def strip_stamps(value, parent=None):
    """Drop timestamps and the version stamps on an evaluator or a descriptor."""
    if isinstance(value, dict):
        kept = {}
        for key, item in value.items():
            if key in TIME_KEYS:
                continue
            if key == "version" and parent in {"evaluator", "descriptor"}:
                continue
            kept[key] = strip_stamps(item, key)
        return kept
    if isinstance(value, list):
        return [strip_stamps(item, parent) for item in value]
    return value


def view(document: dict) -> dict:
    integrity = document.get("integrity") if isinstance(document.get("integrity"), dict) else {}
    run = integrity.get("run") if isinstance(integrity.get("run"), dict) else {}
    findings = integrity.get("findings")
    if findings is None:
        findings = integrity.get("checks")
    return {
        "status": run.get("test_system_status"),
        "percentage": run.get("percentage"),
        "findings": strip_stamps(findings, "findings"),
        "integrity": strip_stamps(integrity, "integrity"),
    }


def documents_from_bundle(bundle: dict) -> list[dict]:
    assessment = bundle.get("assessment") if isinstance(bundle.get("assessment"), dict) else {}
    stored = assessment.get("integrity_results")
    if not isinstance(stored, dict) or not stored:
        return []
    return [item for item in stored.values() if isinstance(item, dict)]


def different(left: dict, right: dict) -> bool:
    return view(left) != view(right)


def checkout(dest: Path) -> Path:
    if not (dest / ".git").is_dir():
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                EVALUATOR_REPOSITORY,
                str(dest),
            ],
            check=True,
        )
    head = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True
    )
    if head.stdout.strip() != EVALUATOR_COMMIT:
        subprocess.run(
            ["git", "-C", str(dest), "fetch", "--depth", "1", "origin", EVALUATOR_COMMIT],
            check=True,
        )
        subprocess.run(["git", "-C", str(dest), "checkout", "--detach", "FETCH_HEAD"], check=True)
    subprocess.run(["uv", "sync", "--locked"], cwd=dest, check=True)
    return dest


def run_previous(checkout_dir: Path, evidence: Path, media: Path | None, out: Path) -> dict:
    command = ["uv", "run", "--no-sync", "python", "-m", "livingproof_evaluator_run", str(evidence)]
    if media is not None:
        command += ["--media", str(media)]
    command += ["--out", str(out)]
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    completed = subprocess.run(command, cwd=checkout_dir, env=env, capture_output=True, text=True)
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        raise SystemExit(completed.returncode or 1)
    return json.loads(out.read_text(encoding="utf-8"))


def print_view(label: str, document: dict) -> None:
    shown = view(document)
    print(f"## {label}")
    print(f"status: {shown['status']}")
    print(f"percentage: {shown['percentage']}")
    print("findings:")
    print(json.dumps(shown["findings"], indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--media", type=Path)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--evaluator", type=Path, default=Path("/tmp/livingproof-evaluator-run-v1.9.0")
    )
    parser.add_argument("--out", type=Path, default=Path("/tmp/integrity-previous.json"))
    args = parser.parse_args(argv)
    previous = run_previous(checkout(args.evaluator), args.evidence, args.media, args.out)
    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    stored = documents_from_bundle(bundle)
    print_view("previous command", previous)
    if len(stored) != 1:
        print(f"bundle integrity_results has {len(stored)} documents")
        return 1
    print_view("bundle", stored[0])
    failed = different(previous, stored[0])
    if failed:
        print("difference: status, percentage, or findings")
    else:
        print("no difference")
    if args.baseline is not None:
        baseline_doc = json.loads(args.baseline.read_text(encoding="utf-8"))
        if "assessment" in baseline_doc:
            baseline_docs = documents_from_bundle(baseline_doc)
            baseline_doc = baseline_docs[0] if baseline_docs else {}
        print_view("baseline (version change, not a pass/fail)", baseline_doc)
        if different(stored[0], baseline_doc):
            print("version change: the baseline view differs")
        else:
            print("version change: none")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
