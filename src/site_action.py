#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Composite GitHub Action entry point for building the `reports/` documentation section.

The action only wires things together: it checks its inputs, restores earlier runs from
this repository's last Pages deploy (reports_restore.py), and runs the pinned reporter's
`livingproof-capability-report site` command, which renders and checks the section.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import reports_restore  # noqa: E402
from reports_restore import SiteError  # noqa: E402

REPORTER = "livingproof-capability-report"
MARKER = "site.json"
DEFAULT_RETENTION_DAYS = 7
# A restored run folder name, as the reporter's site command accepts it.
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
PR_EVENTS = frozenset({"pull_request", "pull_request_target", "merge_group"})
BOOL_MODES = frozenset({"auto", "true", "false"})


def _parse_mode(name: str, raw: str, default: str = "auto") -> str:
    val = (raw or default).strip().lower()
    if val not in BOOL_MODES:
        raise SiteError(f"Invalid {name} value {raw!r}; expected one of: auto, true, false.")
    return val


def _parse_bool(name: str, raw: str, default: bool = False) -> bool:
    val = (raw or ("true" if default else "false")).strip().lower()
    if val not in {"true", "false"}:
        raise SiteError(f"Invalid {name} value {raw!r}; expected true or false.")
    return val == "true"


def _parse_positive_int(name: str, raw: str, default: int) -> int:
    text = (raw or str(default)).strip()
    if not text.isdigit() or int(text) < 1:
        raise SiteError(f"Invalid {name} value {raw!r}; expected a positive integer.")
    return int(text)


def validate_exclude_run_id(entry: object) -> str:
    if not isinstance(entry, str) or not entry:
        raise SiteError(
            f"Invalid exclude-runs entry {entry!r}: must be a non-empty run folder name."
        )
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in entry):
        raise SiteError(
            f"Invalid exclude-runs entry {entry!r}: must not contain control characters."
        )
    if "/" in entry or "\\" in entry or entry in {".", ".."} or ".." in entry:
        raise SiteError(f"Invalid exclude-runs entry {entry!r}: must be a plain run folder name.")
    if not RUN_ID.fullmatch(entry):
        raise SiteError(f"Invalid exclude-runs entry {entry!r}: must be a plain run folder name.")
    return entry


def parse_exclude_runs(raw: str) -> tuple[str, ...]:
    """The exclude-runs input: run folder names separated by commas or whitespace."""
    normalized = raw.replace("\r\n", "\n")
    if any((ord(ch) < 32 or ord(ch) == 127) and ch not in "\n\t" for ch in normalized):
        raise SiteError("exclude-runs must not contain control characters.")
    if not normalized.strip():
        return ()
    if "," in normalized:
        tokens: list[str] = []
        for part in normalized.split(","):
            part_tokens = part.split()
            if not part_tokens:
                raise SiteError("exclude-runs must not contain empty entries.")
            tokens.extend(part_tokens)
    else:
        tokens = normalized.split()
    return tuple(dict.fromkeys(validate_exclude_run_id(entry) for entry in tokens))


def reporter_command(env_map: dict[str, str], executable_dir: Path | None = None) -> str:
    """The pinned reporter: site/action.yml puts its isolated runtime first on PATH."""
    found = shutil.which(REPORTER, path=env_map.get("PATH") or os.defpath)
    if found:
        return found
    beside = (executable_dir or Path(sys.executable).parent) / REPORTER
    if beside.is_file() and os.access(beside, os.X_OK):
        return str(beside)
    raise SiteError(
        f"{REPORTER} is not on PATH. site/action.yml installs the pinned reporter first."
    )


def run_site_command(command: list[str], env_map: dict[str, str]) -> str:
    """Run the reporter's site command and return what it printed."""
    result = subprocess.run(command, env=env_map, capture_output=True, text=True, check=False)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        lines = [line for line in result.stderr.splitlines() if line.strip()]
        reason = lines[-1].removeprefix("Error: ") if lines else f"exit {result.returncode}"
        raise SiteError(f"{REPORTER} site command failed: {reason}")
    return result.stdout


def added_run_folders(output: str) -> list[Path]:
    """The run folders the site command added, from its `Added <folder>` lines."""
    return [
        Path(line.removeprefix("Added "))
        for line in output.splitlines()
        if line.startswith("Added ")
    ]


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
        raise SiteError("output-dir must not contain control characters.")
    cleaned = raw.strip()
    if not cleaned or "\\" in cleaned:
        raise SiteError("output-dir must be a non-empty relative directory path.")
    candidate = Path(cleaned)
    if ".." in candidate.parts or ".git" in candidate.parts or cleaned in {".", "./"}:
        raise SiteError(
            f"output-dir {raw!r} must not contain '..' or '.git' or be the workspace root."
        )
    workspace_root = Path(workspace or os.getcwd()).resolve()
    resolved = (candidate if candidate.is_absolute() else workspace_root / candidate).resolve()
    if resolved == workspace_root or not resolved.is_relative_to(workspace_root):
        raise SiteError(f"output-dir {raw!r} must stay inside GITHUB_WORKSPACE ({workspace_root}).")
    return cleaned, resolved


def run_site_action(
    env: dict[str, str] | None = None, executable_dir: Path | None = None
) -> dict[str, str]:
    env_map = dict(os.environ if env is None else env)
    runs_dir_raw = (env_map.get("INPUT_RUNS_DIR") or "").strip()
    if not runs_dir_raw:
        raise SiteError(
            "runs-dir must be a non-empty path to the directory of per-scenario run folders."
        )
    raw_output_input = env_map.get("INPUT_OUTPUT_DIR")
    if raw_output_input is not None and _has_control_character(raw_output_input):
        raise SiteError("output-dir must not contain control characters.")
    if raw_output_input is not None and not raw_output_input.strip():
        raise SiteError("output-dir must be a non-empty directory path.")
    output_dir_raw = (raw_output_input or "reports").strip()
    workspace = (env_map.get("GITHUB_WORKSPACE") or "").strip()
    output_dir_given, reports_dir = _validate_output_dir(output_dir_raw, workspace)

    retention_days = _parse_positive_int(
        "evidence-retention-days",
        env_map.get("INPUT_EVIDENCE_RETENTION_DAYS", ""),
        DEFAULT_RETENTION_DAYS,
    )
    restore_mode = _parse_mode(
        "restore-history", env_map.get("INPUT_RESTORE_HISTORY", "auto"), "auto"
    )
    upload_mode = _parse_mode(
        "upload-artifact", env_map.get("INPUT_UPLOAD_ARTIFACT", "auto"), "auto"
    )
    reset_history = _parse_bool("reset-history", env_map.get("INPUT_RESET_HISTORY", "false"), False)
    exclude_runs = parse_exclude_runs(env_map.get("INPUT_EXCLUDE_RUNS") or "")

    event_name = (env_map.get("GITHUB_EVENT_NAME") or "").strip()
    is_pr = event_name in PR_EVENTS
    artifact_only = is_pr

    should_restore = restore_mode == "true" or (restore_mode == "auto" and not is_pr)
    should_upload = upload_mode == "true" or (upload_mode == "auto" and is_pr)

    raw_artifact_name = env_map.get("INPUT_ARTIFACT_NAME") or ""
    if _has_control_character(raw_artifact_name):
        raise SiteError("artifact-name must not contain control characters.")
    artifact_name = raw_artifact_name.strip()
    if not artifact_name:
        run_id_part = (env_map.get("GITHUB_RUN_ID") or "local").strip()
        attempt_part = (env_map.get("GITHUB_RUN_ATTEMPT") or "1").strip()
        artifact_name = f"verification-reports-site-{run_id_part}-{attempt_part}"
    if _has_control_character(artifact_name):
        raise SiteError("artifact-name must not contain control characters.")

    runs_dir = Path(runs_dir_raw)

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
            raise SiteError("restore-root must contain output-dir")
    restored = None
    repository = (env_map.get("GITHUB_REPOSITORY") or "").strip()
    if not repository:
        raise SiteError("GITHUB_REPOSITORY is required")

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
        restored = reports_restore.restore(
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

    from livingproof_capability_report.site import check_reports_tree

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

    pages = {
        target.parent: (target.parent / "index.html").read_bytes()
        for target in trusted_viewer.discover_viewers(reports_dir)
    }

    command = [
        reporter_command(env_map, executable_dir),
        "site",
        str(reports_dir),
        "--runs",
        str(runs_dir),
        "--evidence-retention-days",
        str(retention_days),
        "--repository",
        repository,
    ]
    if allow_replace:
        command += ["--allow-replace-run-id", allow_replace]
    if git_dir is not None:
        command += ["--git-dir", str(git_dir)]
    for entry in exclude_runs:
        command += ["--exclude-run", entry]
    output = run_site_command(command, env_map)
    for folder in added_run_folders(output):
        trusted_viewer.render_fresh(reports_dir / folder, viewers)
    for folder, page in pages.items():
        if (folder / "index.html").read_bytes() != page:
            # Ordinary expiry republished this run's shell; compose its approved panel again.
            trusted_viewer.render_fresh(folder, history_viewers, existing=True)

    issues = check_reports_tree(reports_dir, repository=repository)
    if issues:
        raise SiteError("Generated reports/ check failed: " + "; ".join(issues))

    marker = json.loads((reports_dir / MARKER).read_text(encoding="utf-8"))

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
    except SiteError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
