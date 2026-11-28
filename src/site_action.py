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


def _has_control_character(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _validate_output_dir(raw: str, workspace: str) -> tuple[str, Path]:
    """Return `(as_given, resolved)` for an output-dir that stays inside the workspace.

    The workspace is GITHUB_WORKSPACE, or the current directory when it is unset, so
    the check never silently turns off. Symlinks are resolved before the containment
    check, so a link inside the workspace that points elsewhere is refused.
    """
    if _has_control_character(raw):
        # A newline here would also forge extra lines in GITHUB_OUTPUT.
        raise site_tool.SiteError("output-dir must not contain control characters.")
    cleaned = raw.strip()
    if not cleaned or "\\" in cleaned:
        raise site_tool.SiteError("output-dir must be a non-empty relative directory path.")
    candidate = Path(cleaned)
    if ".." in candidate.parts or ".git" in candidate.parts or cleaned in {".", "./"}:
        raise site_tool.SiteError(
            f"output-dir {raw!r} must not contain '..' or '.git' or be the workspace root."
        )
    workspace_root = Path(workspace or os.getcwd()).resolve()
    resolved = (candidate if candidate.is_absolute() else workspace_root / candidate).resolve()
    if resolved == workspace_root or not resolved.is_relative_to(workspace_root):
        raise site_tool.SiteError(
            f"output-dir {raw!r} must stay inside GITHUB_WORKSPACE ({workspace_root})."
        )
    return cleaned, resolved


def run_site_action(env: dict[str, str] | None = None) -> dict[str, str]:
    env_map = dict(os.environ if env is None else env)
    runs_dir_raw = (env_map.get("INPUT_RUNS_DIR") or "").strip()
    if not runs_dir_raw:
        raise site_tool.SiteError(
            "runs-dir must be a non-empty path to the directory of per-scenario run folders."
        )
    raw_output_input = env_map.get("INPUT_OUTPUT_DIR")
    if raw_output_input is not None and _has_control_character(raw_output_input):
        raise site_tool.SiteError("output-dir must not contain control characters.")
    if raw_output_input is not None and not raw_output_input.strip():
        raise site_tool.SiteError("output-dir must be a non-empty directory path.")
    output_dir_raw = (raw_output_input or "reports").strip()
    workspace = (env_map.get("GITHUB_WORKSPACE") or "").strip()
    output_dir_given, reports_dir = _validate_output_dir(output_dir_raw, workspace)

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
    exclude_runs = site_tool.parse_exclude_runs(env_map.get("INPUT_EXCLUDE_RUNS") or "")

    event_name = (env_map.get("GITHUB_EVENT_NAME") or "").strip()
    is_pr = event_name in PR_EVENTS
    artifact_only = is_pr

    should_restore = restore_mode == "true" or (restore_mode == "auto" and not is_pr)
    should_upload = upload_mode == "true" or (upload_mode == "auto" and is_pr)

    raw_artifact_name = env_map.get("INPUT_ARTIFACT_NAME") or ""
    if _has_control_character(raw_artifact_name):
        raise site_tool.SiteError("artifact-name must not contain control characters.")
    artifact_name = raw_artifact_name.strip()
    if not artifact_name:
        run_id_part = (env_map.get("GITHUB_RUN_ID") or "local").strip()
        attempt_part = (env_map.get("GITHUB_RUN_ATTEMPT") or "1").strip()
        artifact_name = f"verification-reports-site-{run_id_part}-{attempt_part}"
    if _has_control_character(artifact_name):
        raise site_tool.SiteError("artifact-name must not contain control characters.")

    runs_dir = Path(runs_dir_raw)

    import json

    import trusted_viewer

    publication_policy = None
    publication_file = env_map.get("INPUT_PUBLICATION_APPROVAL", "")
    if publication_file:
        publication_policy = json.loads(trusted_viewer.read_file(Path(publication_file)))
        publication_policy["viewers"] = trusted_viewer.load_viewers(
            publication_policy["viewer_registry"], publication=True
        )
    restore_dir = reports_dir
    if env_map.get("INPUT_RESTORE_ROOT"):
        _, restore_dir = _validate_output_dir(env_map["INPUT_RESTORE_ROOT"], workspace)
        if not reports_dir.is_relative_to(restore_dir):
            raise site_tool.SiteError("restore-root must contain output-dir")
    restored = None
    repository = (env_map.get("GITHUB_REPOSITORY") or "").strip()
    if not repository:
        raise site_tool.SiteError("GITHUB_REPOSITORY is required")

    if should_restore:
        # History is only ever restored from the calling repository's own Pages deploys.
        branch = (env_map.get("INPUT_BRANCH") or "main").strip()
        run_id_str = (env_map.get("GITHUB_RUN_ID") or "").strip()
        current_run = int(run_id_str) if run_id_str.isdigit() else None
        token = (env_map.get("INPUT_GITHUB_TOKEN") or env_map.get("GITHUB_TOKEN") or "").strip()
        site_url = (env_map.get("INPUT_SITE_URL") or "").strip() or None
        pages_artifact = (env_map.get("INPUT_PAGES_ARTIFACT") or "").strip() or None
        pages_job = (env_map.get("INPUT_PAGES_JOB") or "").strip() or None
        api_url = (env_map.get("GITHUB_API_URL") or "https://api.github.com").strip()

        restore_options = {}
        if publication_policy is not None:
            restore_options["publication_policy"] = publication_policy
        restored = site_tool.restore(
            restore_dir,
            repository,
            branch=branch,
            current_run=current_run,
            token=token or None,
            api=api_url,
            site_url=site_url,
            reset_history=reset_history,
            pages_artifact=pages_artifact,
            pages_job=pages_job,
            **restore_options,
        )

    allow_replace = (env_map.get("GITHUB_RUN_ID") or "").strip() or None
    git_dir = Path(workspace) if workspace and Path(workspace).is_dir() else None

    import trusted_viewer
    from evaluators_run import resolve_caller_evaluators

    declarations = resolve_caller_evaluators(
        env_map.get("INPUT_EVALUATORS", ""), Path(workspace or ".")
    )
    viewers = trusted_viewer.load_viewers(
        env_map.get("INPUT_TRUSTED_VIEWERS", ""), declarations["lines"]
    )
    history_viewers = trusted_viewer.load_history_viewers(
        env_map.get("INPUT_HISTORICAL_VIEWERS", "")
    )
    if restored is not None and publication_policy is not None:
        history_viewers = restored.get("_viewer_approvals", [])
    trusted_viewer.validate_history(reports_dir, history_viewers)

    produced = site_tool.build(
        reports_dir,
        runs_dir,
        days=retention_days,
        allow_replace_run_id=allow_replace,
        git_dir=git_dir,
        exclude_runs=exclude_runs,
        trusted_viewers=history_viewers,
        repository=repository,
    )

    for folder in produced:
        trusted_viewer.render_fresh(folder, viewers)

    issues = site_fixture.check_reports_tree(reports_dir, repository=repository)
    if issues:
        raise site_tool.SiteError("Generated reports/ check failed: " + "; ".join(issues))

    marker = site_tool.read_json_object(reports_dir / site_tool.MARKER)

    outputs = {
        "reports-dir": output_dir_given,
        "index-path": str(Path(output_dir_given) / "index.html"),
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
