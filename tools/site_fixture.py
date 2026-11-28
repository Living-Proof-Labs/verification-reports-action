#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fixture run folders for a single-robot `reports/` build, plus link and checksum verification."""

from __future__ import annotations

import argparse
import os
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import reports_site as site_tool  # noqa: E402

DEFAULT_SCENARIOS = (
    {
        "variant": "stock-mujoco-hallway",
        "capability": "walk-hallway",
        "configuration": "stock",
        "grade": "FAIL",
        "k": 12,
        "n": 20,
        "required_passes": 18,
        "finding": "Drifted 0.66 m from the hallway centreline.",
        "with_evidence": True,
    },
    {
        "variant": "stock-mujoco-corner-left",
        "capability": "walk-corner-left",
        "configuration": "stock",
        "grade": "PASS",
        "k": 1,
        "n": 1,
        "required_passes": 1,
        "finding": None,
        "with_evidence": False,
    },
    {
        "variant": "stock-mujoco-corner-right",
        "capability": "walk-corner-right",
        "configuration": "stock",
        "grade": "INSUFFICIENT_EVIDENCE",
        "k": 0,
        "n": 1,
        "required_passes": 1,
        "finding": "Recording stopped before the corner exit.",
        "with_evidence": False,
    },
)


def write(
    directory: Path,
    *,
    run_id: str = "100",
    created: str = "2026-11-23T00:00:00Z",
    repository: str = "Living-Proof-Labs/livingproof-unitree-g1",
    commit: str = "a" * 40,
    tag: str | None = "v2.3.32",
    scenarios: tuple[dict, ...] = DEFAULT_SCENARIOS,
) -> list[Path]:
    """Write per-scenario run folders in the expected input layout."""
    written: list[Path] = []
    robot_slug = repository.rsplit("/", 1)[-1].removeprefix("livingproof-").lower()
    for item in scenarios:
        variant = item["variant"]
        folder = directory / f"{robot_slug}-{variant}"
        folder.mkdir(parents=True, exist_ok=True)

        ev_doc: dict = {
            "format": "livingproof-evaluator-result/1.1.0",
            "evaluator": {"id": "livingproof-scenario", "display_name": "Scenario"},
            "descriptor": {
                "title": "Scenario",
                "purpose": "Evaluates scenario completion and bounds.",
            },
        }
        if item.get("finding"):
            ev_doc["grade"] = "FAIL"
            ev_doc["findings"] = [{"summary": item["finding"]}]

        report = {
            "report_version": "1.0.0",
            "source_bundle": {
                "results": [
                    {
                        "id": f"{variant}-trial-01",
                        "evaluator_results": [ev_doc],
                    }
                ]
            },
            "summaries": [
                {
                    "capability": item["capability"],
                    "spec": {
                        "repository": "Living-Proof-Labs/robot-capability-specs",
                        "tag": "v3.1.0",
                        "commit": "b" * 40,
                        "capability": item["capability"],
                        "revision": "0.2.0",
                    },
                    "system": {
                        "id": f"{robot_slug}/{item['configuration']}",
                        "version": "1.1.1",
                    },
                    "grade": item["grade"],
                    "k": item["k"],
                    "n": item["n"],
                    "required_passes": item["required_passes"],
                }
            ],
        }
        (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

        run_url = f"https://github.com/{repository}/actions/runs/{run_id.split('-')[0]}"
        ci_run = f"<p><a href='{run_url}'>CI run</a></p>"
        evidence_html = f"<section id='evidence'>{ci_run}</section>"
        if item.get("with_evidence"):
            ev_dir = folder / "evidence"
            ev_dir.mkdir(parents=True, exist_ok=True)
            (ev_dir / "trials.mcap").write_bytes(b"fixture mcap bytes")
            (ev_dir / "trial-01-fail.mp4").write_bytes(b"fixture mp4 bytes")
            evidence_html = (
                f"<section id='evidence'><p class='is-kept'>Evidence kept for 7 days</p>{ci_run}"
                "<p><a href='evidence/trials.mcap'>MCAP recording</a></p>"
                "<video controls src='evidence/trial-01-fail.mp4'></video></section>"
            )

        html_body = (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<title>{variant}</title></head><body><main>"
            f"<section id='summary'><div id='evaluator-livingproof-scenario'>"
            f"<h1>{variant}: {item['grade']}</h1></div></section>"
            f"{evidence_html}</main></body></html>\n"
        )
        (folder / "report.html").write_text(html_body, encoding="utf-8")
        (folder / "report.md").write_text(
            f"# {variant}\n\nGrade: {item['grade']}\n", encoding="utf-8"
        )
        (folder / "report.txt").write_text(f"{variant}: {item['grade']}\n", encoding="utf-8")

        commit_block: dict = {
            "sha": commit,
            "url": f"https://github.com/{repository}/commit/{commit}",
        }
        if tag:
            commit_block["tag"] = tag

        run_record = {
            "variant": variant,
            "verification": f"{robot_slug}-{variant}",
            "run": {
                "id": run_id,
                "url": f"https://github.com/{repository}/actions/runs/{run_id.split('-')[0]}",
                "created": created,
            },
            "commit": commit_block,
            "robot": {
                "repository": repository,
                "commit": commit,
                "configuration": item["configuration"],
                "url": (
                    f"https://github.com/{repository}/releases/tag/{tag}"
                    if tag
                    else f"https://github.com/{repository}/tree/{commit}"
                ),
                **({"tag": tag} if tag else {}),
            },
            "poster": {"type": "ci", "name": "CI"},
            "releases": {
                "specs": {
                    "repository": "Living-Proof-Labs/robot-capability-specs",
                    "tag": "v3.1.0",
                    "commit": "b" * 40,
                    "url": "https://github.com/Living-Proof-Labs/robot-capability-specs/releases/tag/v3.1.0",
                },
                "verifier": {
                    "repository": "Living-Proof-Labs/capability-verifier",
                    "tag": "v2.11.4",
                    "commit": "c" * 40,
                    "url": "https://github.com/Living-Proof-Labs/capability-verifier/releases/tag/v2.11.4",
                },
                "reporter": {
                    "repository": "Living-Proof-Labs/livingproof-capability-report",
                    "tag": "v3.15.4",
                    "commit": "d" * 40,
                    "url": "https://github.com/Living-Proof-Labs/livingproof-capability-report/releases/tag/v3.15.4",
                },
            },
        }
        (folder / "run.json").write_text(json.dumps(run_record, indent=2) + "\n", encoding="utf-8")
        (folder / "SHA256SUMS").write_text(
            "".join(
                f"{hashlib.sha256((folder / n).read_bytes()).hexdigest()}  {n}\n"
                for n in site_tool.REPORT_FILES
            ),
            encoding="utf-8",
        )
        written.append(folder)
    return written


def check_reports_tree(reports_dir: Path, repository: str | None = None) -> list[str]:
    """Return a list of link, checksum, or content issues inside `reports_dir`."""
    issues: list[str] = []
    if not (reports_dir / "index.html").is_file():
        issues.append("index.html missing")
    if not (reports_dir / site_tool.MARKER).is_file():
        issues.append(f"{site_tool.MARKER} missing")
    else:
        index_text = (reports_dir / "index.html").read_text(encoding="utf-8")
        for needle in ("livingproof-verification/", "Earlier history"):
            if needle in index_text:
                issues.append(f"index.html still contains {needle!r}")

    parent_overview = (reports_dir.parent / "index.html").resolve()
    pages = sorted(reports_dir.rglob("*.html"))
    for page in pages:
        text = page.read_text(encoding="utf-8")
        for href in re.findall(r"""(?:href|src)=["']([^"'#]+)""", text):
            if re.match(r"^[a-z][a-z0-9+.-]*:", href, re.I) or href.startswith("//"):
                continue
            if href.startswith("/"):
                issues.append(
                    f"{page.relative_to(reports_dir)}: unexpected root-relative link {href}"
                )
                continue
            resolved = (page.parent / href).resolve()
            if href.endswith("/"):
                resolved = resolved / "index.html"
            if resolved == parent_overview and not parent_overview.exists():
                continue
            if not resolved.is_file():
                issues.append(f"{page.relative_to(reports_dir)}: broken link {href}")

    for run_folder in sorted(reports_dir.glob("*/*/runs/*")):
        if not run_folder.is_dir():
            continue
        sums_path = run_folder / "SHA256SUMS"
        if not sums_path.is_file():
            issues.append(f"{run_folder.relative_to(reports_dir)}: missing SHA256SUMS")
            continue
        listed = site_tool._parse_sha256sums(sums_path)
        actual_files = {
            p.relative_to(run_folder).as_posix()
            for p in run_folder.rglob("*")
            if p.is_file() and p.name != "SHA256SUMS"
        }
        if set(listed) != actual_files:
            issues.append(
                f"{run_folder.relative_to(reports_dir)}: SHA256SUMS file set mismatch "
                f"(listed={sorted(listed)}, files={sorted(actual_files)})"
            )
            continue
        for rel_name, digest in listed.items():
            actual = hashlib.sha256((run_folder / rel_name).read_bytes()).hexdigest()
            if actual != digest:
                issues.append(f"{run_folder.relative_to(reports_dir)}/{rel_name}: sha256 mismatch")

    issues.extend(site_tool.public_page_problems(reports_dir, repository=repository))
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    w = sub.add_parser("write", help="write fixture run folders into DIR")
    w.add_argument("directory", type=Path)
    w.add_argument("--run-id", default="100")
    w.add_argument("--created", default="2026-11-23T00:00:00Z")
    w.add_argument("--repository", default="Living-Proof-Labs/livingproof-unitree-g1")
    w.add_argument("--commit", default="a" * 40)
    w.add_argument("--tag", default="v2.3.32")

    c = sub.add_parser("check", help="verify links, checksums, and scrubbed content in REPORTS_DIR")
    c.add_argument("reports_dir", type=Path)
    c.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="caller repository (owner/name) whose CI run pages may be linked",
    )

    args = parser.parse_args(argv)
    if args.command == "write":
        written = write(
            args.directory,
            run_id=args.run_id,
            created=args.created,
            repository=args.repository,
            commit=args.commit,
            tag=args.tag or None,
        )
        for folder in written:
            print(f"Wrote {folder}")
        return 0

    issues = check_reports_tree(args.reports_dir, repository=args.repository)
    for line in issues:
        print(f"issue: {line}", file=sys.stderr)
    pages = len(list(args.reports_dir.rglob("*.html")))
    print(f"{pages} pages, {len(issues)} issues")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
