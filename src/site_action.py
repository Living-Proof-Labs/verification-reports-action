#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Composite GitHub Action entry point for building the `reports/` documentation section."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import reports_site as site_tool  # noqa: E402
import site_fixture  # noqa: E402

PR_EVENTS = frozenset({"pull_request", "pull_request_target", "merge_group"})
BOOL_MODES = frozenset({"auto", "true", "false"})


def _parse_mode(name: str, raw: str, default: str = "auto") -> str:
    val = (raw or default).strip().lower()
    if val not in BOOL_MODES:
        raise site_tool.SiteError(
            f"Invalid {name} value {raw!r}; expected one of: auto, true, false."
        )
    return val


def _parse_bool(name: str, raw: str, default: bool = False) -> bool:
    val = (raw or ("true" if default else "false")).strip().lower()
    if val not in {"true", "false"}:
        raise site_tool.SiteError(f"Invalid {name} value {raw!r}; expected true or false.")
    return val == "true"


def _parse_positive_int(name: str, raw: str, default: int) -> int:
    text = (raw or str(default)).strip()
    if not text.isdigit() or int(text) < 1:
        raise site_tool.SiteError(f"Invalid {name} value {raw!r}; expected a positive integer.")
    return int(text)


def run_site_action(env: dict[str, str] | None = None) -> dict[str, str]:
    env_map = dict(os.environ if env is None else env)
    runs_dir_raw = (env_map.get("INPUT_RUNS_DIR") or "").strip()
    if not runs_dir_raw:
        raise site_tool.SiteError(
            "runs-dir must be a non-empty path to the directory of per-scenario run folders."
        )
    output_dir_raw = (env_map.get("INPUT_OUTPUT_DIR") or "reports").strip()
    if not output_dir_raw:
        raise site_tool.SiteError("output-dir must be a non-empty directory path.")

    retention_days = _parse_positive_int(
        "evidence-retention-days",
        env_map.get("INPUT_EVIDENCE_RETENTION_DAYS", ""),
        site_tool.DEFAULT_RETENTION_DAYS,
    )
    restore_mode = _parse_mode(
        "restore-history", env_map.get("INPUT_RESTORE_HISTORY", "auto"), "auto"
    )
    upload_mode = _parse_mode(
        "upload-artifact", env_map.get("INPUT_UPLOAD_ARTIFACT", "auto"), "auto"
    )
    reset_history = _parse_bool("reset-history", env_map.get("INPUT_RESET_HISTORY", "false"), False)

    event_name = (env_map.get("GITHUB_EVENT_NAME") or "").strip()
    is_pr = event_name in PR_EVENTS
    artifact_only = is_pr

    should_restore = restore_mode == "true" or (restore_mode == "auto" and not is_pr)
    should_upload = upload_mode == "true" or (upload_mode == "auto" and is_pr)

    runs_dir = Path(runs_dir_raw)
    reports_dir = Path(output_dir_raw)

    if should_restore:
        repository = (
            env_map.get("INPUT_REPOSITORY") or env_map.get("GITHUB_REPOSITORY") or ""
        ).strip()
        branch = (env_map.get("INPUT_BRANCH") or "main").strip()
        run_id_str = (env_map.get("GITHUB_RUN_ID") or "").strip()
        current_run = int(run_id_str) if run_id_str.isdigit() else None
        token = (env_map.get("INPUT_GITHUB_TOKEN") or env_map.get("GITHUB_TOKEN") or "").strip()
        site_url = (env_map.get("INPUT_SITE_URL") or "").strip() or None
        pages_artifact = (env_map.get("INPUT_PAGES_ARTIFACT") or "").strip() or None
        pages_job = (env_map.get("INPUT_PAGES_JOB") or "").strip() or None
        api_url = (env_map.get("GITHUB_API_URL") or "https://api.github.com").strip()

        site_tool.restore(
            reports_dir,
            repository,
            branch=branch,
            current_run=current_run,
            token=token or None,
            api=api_url,
            site_url=site_url,
            reset_history=reset_history,
            pages_artifact=pages_artifact,
            pages_job=pages_job,
        )

    now_override = (env_map.get("INPUT_NOW") or "").strip() or None
    allow_replace = (env_map.get("GITHUB_RUN_ID") or "").strip() or None
    workspace = (env_map.get("GITHUB_WORKSPACE") or "").strip()
    git_dir = Path(workspace) if workspace and Path(workspace).is_dir() else None

    site_tool.build(
        reports_dir,
        runs_dir,
        now=now_override,
        days=retention_days,
        allow_replace_run_id=allow_replace,
        git_dir=git_dir,
    )

    issues = site_fixture.check_reports_tree(reports_dir)
    if issues:
        raise site_tool.SiteError("Generated reports/ check failed: " + "; ".join(issues))

    marker = site_tool.read_json_object(reports_dir / site_tool.MARKER)
    artifact_name = (env_map.get("INPUT_ARTIFACT_NAME") or "").strip()
    if not artifact_name:
        run_id_part = (env_map.get("GITHUB_RUN_ID") or "local").strip()
        attempt_part = (env_map.get("GITHUB_RUN_ATTEMPT") or "1").strip()
        artifact_name = f"verification-reports-site-{run_id_part}-{attempt_part}"

    outputs = {
        "reports-dir": str(reports_dir),
        "index-path": str(reports_dir / "index.html"),
        "runs-count": str(marker["runs"]),
        "artifact-only": "true" if artifact_only else "false",
        "should-upload-artifact": "true" if should_upload else "false",
        "artifact-name": artifact_name,
    }

    github_output = env_map.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            for key, value in outputs.items():
                handle.write(f"{key}={value}\n")

    return outputs


def main() -> int:
    try:
        result = run_site_action()
        print(
            f"Built verification reports section at {result['reports-dir']} "
            f"({result['runs-count']} runs, artifact-only={result['artifact-only']})."
        )
    except site_tool.SiteError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
