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
import re
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


ADVISORY_CUE = re.compile(r"""eval-cue['"]>Advisory<""")
NOT_RUN = re.compile(r"NOT RUN")


def run_folders(root: Path) -> dict[str, Path]:
    found = {}
    for path in sorted(root.rglob("run.json")):
        if path.is_file() and not path.is_symlink():
            found[path.parent.relative_to(root).as_posix()] = path.parent
    return found


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def file_bytes(folder: Path, relative: str) -> dict[str, bytes]:
    base = folder / relative
    if not base.exists():
        return {}
    found = {}
    paths = [base] if base.is_file() else sorted(item for item in base.rglob("*") if item.is_file())
    for path in paths:
        if path.is_symlink():
            raise SystemExit(f"{path} is a symlink")
        found[path.relative_to(folder).as_posix()] = path.read_bytes()
    return found


def compare_on_off(
    on_root: Path,
    off_root: Path,
    *,
    grade: str | None = None,
    advisory_lines: int | None = None,
    grades: str | None = None,
) -> int:
    """Compare two collected reports. Exit 0 when grade, scenario, integrity and evidence match.

    The on report has one advisory card per advisory line. The off report has
    none, and no extra NOT RUN placeholder.
    """
    on_runs = run_folders(on_root)
    off_runs = run_folders(off_root)
    if on_runs.keys() != off_runs.keys() or not on_runs:
        print(f"run folders differ: {sorted(on_runs)} vs {sorted(off_runs)}")
        return 1
    failed = False
    for key in sorted(on_runs):
        on = on_runs[key]
        off = off_runs[key]
        on_report = _load(on / "report.json")
        off_report = _load(off / "report.json")
        on_grades = [
            item.get("grade") for item in on_report.get("summaries") or [] if isinstance(item, dict)
        ]
        off_grades = [
            item.get("grade")
            for item in off_report.get("summaries") or []
            if isinstance(item, dict)
        ]
        if on_grades != off_grades:
            print(f"{key}: grades differ {on_grades} vs {off_grades}")
            failed = True
        if grade is not None and grade not in on_grades:
            print(f"{key}: grade is {on_grades}, expected {grade}")
            failed = True
        on_bundle = on_report.get("source_bundle") if isinstance(on_report, dict) else {}
        off_bundle = off_report.get("source_bundle") if isinstance(off_report, dict) else {}
        if not isinstance(on_bundle, dict) or not isinstance(off_bundle, dict):
            print(f"{key}: report has no source bundle")
            return 1
        if on_bundle.get("results") != off_bundle.get("results"):
            print(f"{key}: scenario results differ")
            failed = True
        on_integrity = documents_from_bundle(on_bundle)
        off_integrity = documents_from_bundle(off_bundle)
        if len(on_integrity) != len(off_integrity) or any(
            different(left, right) for left, right in zip(on_integrity, off_integrity)
        ):
            print(f"{key}: Run Integrity differs")
            failed = True
        if file_bytes(on, "evidence") != file_bytes(off, "evidence"):
            print(f"{key}: evidence bytes differ")
            failed = True
        on_html = (on / "report.html").read_text(encoding="utf-8")
        off_html = (off / "report.html").read_text(encoding="utf-8")
        on_cards = len(ADVISORY_CUE.findall(on_html))
        off_cards = len(ADVISORY_CUE.findall(off_html))
        if advisory_lines is not None and on_cards != advisory_lines:
            print(f"{key}: advisory cards {on_cards}, expected {advisory_lines}")
            failed = True
        if off_cards != 0:
            print(f"{key}: advisory-off report has {off_cards} advisory cards")
            failed = True
        if len(NOT_RUN.findall(on_html)) != len(NOT_RUN.findall(off_html)):
            print(f"{key}: NOT RUN placeholder differs")
            failed = True
        if "placeholder" in off_html.lower():
            print(f"{key}: advisory-off report contains a placeholder")
            failed = True
    if grades:
        parsed = json.loads(grades)
        if not isinstance(parsed, dict) or not parsed:
            print("grades output is empty")
            return 1
        if grade is not None and any(value != grade for value in parsed.values()):
            print(f"grades output is not {grade}: {parsed}")
            failed = True
    if failed:
        return 1
    print("no difference")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--media", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--evaluator", type=Path, default=Path("/tmp/livingproof-evaluator-run-v1.9.0")
    )
    parser.add_argument("--out", type=Path, default=Path("/tmp/integrity-previous.json"))
    parser.add_argument("--on", type=Path, help="collected report with advisory lines")
    parser.add_argument("--off", type=Path, help="collected report without advisory lines")
    parser.add_argument("--grade", help="robot grade both reports must show")
    parser.add_argument(
        "--advisory-lines", type=int, help="advisory cards expected on the on report"
    )
    parser.add_argument("--grades", help="JSON grade map from the on workflow")
    args = parser.parse_args(argv)
    if args.on is not None or args.off is not None:
        if args.on is None or args.off is None:
            parser.error("--on and --off are used together")
        return compare_on_off(
            args.on,
            args.off,
            grade=args.grade,
            advisory_lines=args.advisory_lines,
            grades=args.grades,
        )
    if args.evidence is None or args.bundle is None:
        parser.error("--evidence and --bundle are required")
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
