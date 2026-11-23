#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Assemble a single-robot verification reports section (`reports/`) for GitHub Pages.

Ported from Living-Proof-Labs/livingproof-verification `tools/site.py` for single-robot
per-repository reports (Living-Proof-Labs/verification-reports-action#5):

- Input: a directory of per-scenario run folders, each holding `report.html`, `report.json`,
  `report.md`, `report.txt`, `SHA256SUMS`, and `run.json` (plus optional `thresholds.json`
  grading inputs and `evidence/` MCAP or video files).
- Output: a static `reports/` directory tree that a robot repository's documentation build
  can include directly:
    - `index.html`: single-robot overview of the latest result per scenario (failures first),
      linking to the archived multi-robot reports as earlier history.
    - `<robot>/<variant>/index.html`: run history per scenario, newest first, with a
      `Latest report` link.
    - `<robot>/<variant>/latest/index.html`: static redirect to the newest run.
    - `<robot>/<variant>/runs/<run-id>/`: immutable per-run report pages, metadata, and
      checksums.
- Earlier runs survive subsequent deployments without commits to the robot repository by
  restoring the `reports/` subtree from the calling repository's last successfully deployed
  GitHub Pages artifact.
- Copied MCAP, video, and media evidence older than a configurable number of days is pruned
  on subsequent builds while keeping report pages, `run.json`, and `SHA256SUMS` valid.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import http.client
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

PAGES_ARTIFACT = "github-pages"
PAGES_ARTIFACT_PATTERN = re.compile(r"github-pages(?:-[A-Za-z0-9._-]+)?")
ALLOWED_DEPLOY_EVENTS = frozenset(
    {"push", "workflow_dispatch", "workflow_run", "release", "schedule"}
)
DEFAULT_PAGES_JOBS = frozenset({"pages", "deploy", "deploy-pages"})
MAX_ARTIFACT_PAGES = 10
# GitHub Pages sites are limited to 1 GB; refuse restored archives far beyond that.
MAX_RESTORED_BYTES = 2 * 1024**3
MAX_RESTORED_FILES = 200_000

SEGMENT = re.compile(r"[a-z0-9][a-z0-9.-]*")
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
REPORT_FILES = ("report.html", "report.json", "report.md", "report.txt")
REQUIRED_RUN_FILES = (*REPORT_FILES, "SHA256SUMS", "run.json")
MAX_RUN_EVIDENCE_BYTES = 25_000_000
DEFAULT_RETENTION_DAYS = 7
ORIGINAL_HTML = "report.html.orig"

ARTIFACT_LINK = re.compile(
    r"<a href=(['\"])(https://github\.com/[^'\"<>]+/actions/runs/[0-9]+/artifacts/([0-9]+))\1>"
    r"(.*?)</a>",
    re.S,
)
ACTIONS_ANCHOR = re.compile(
    r"<a\s[^>]*?href=(['\"])([^'\"<>]*/actions/[^'\"<>]*)\1[^>]*>(.*?)</a>", re.S | re.I
)
ACTIONS_URL = re.compile(r"https://github\.com/\S*?/actions/\S*", re.I)
ARTIFACT_WORD = re.compile(r"artifacts?", re.I)
EVIDENCE_ANCHOR = re.compile(
    r"<a\s[^>]*?href=(['\"])(evidence/[^'\"<>]+)\1[^>]*>(.*?)</a>", re.S | re.I
)
EVIDENCE_VIDEO = re.compile(
    r"<video\b(?:(?!</video\b).)*?evidence/[^'\"<>\s]+(?:(?!</video\b).)*?</video\s*>",
    re.S | re.I,
)
EVIDENCE_IMG = re.compile(r"<img\b[^>]*?\bsrc=(['\"])(evidence/[^'\"<>]+)\1[^>]*/?>", re.S | re.I)
KEPT_FOR_COPY = re.compile(r"Evidence kept for \d+ days?")

MEDIA_SUFFIXES = {
    ".mcap",
    ".mp4",
    ".webm",
    ".mov",
    ".mkv",
    ".avi",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
}
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".avi"}

SITE_PATH = re.compile(r"[A-Za-z0-9._~!$&'()*+,;=@%/-]{1,240}")
SEED_LABEL = re.compile(r"^seed (?=\d)")
RELEASE_TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
MARKER = "site.json"
DOCS_HOME_URL = "https://docs.livingprooflabs.com/"
ARCHIVED_REPORTS_URL = (
    "https://docs.livingprooflabs.com/livingproof-verification/reports/index.html"
)

DOWNLOAD_ATTEMPTS = 5
DOWNLOAD_BACKOFF_BASE = 10
DOWNLOAD_JITTER = (0.8, 1.2)
LOOKUP_ATTEMPTS = 3
LOOKUP_BACKOFF_BASE = 1.0
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5


class SiteError(Exception):
    """Raised when report validation, single-robot constraints, or history restore fails."""


# ---------------------------------------------------------------- restore


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Raise on 3xx so the download loop follows blob redirects without forwarding tokens."""

    def http_error_302(self, req, fp, code, msg, headers):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _request(url: str, token: str | None):
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    if token:
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(request, timeout=120)


def _open_once(url: str, token: str | None):
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    if token:
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=120)


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc


def _retryable(error: BaseException) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code == 429 or 500 <= error.code < 600
    return isinstance(error, (OSError, http.client.HTTPException))


def _error_text(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        where = getattr(error, "url", "") or ""
        text = f"HTTP {error.code} {error.reason}".rstrip()
        return f"{text} from {where}" if where else text
    if isinstance(error, urllib.error.URLError):
        return f"connection error: {error.reason}"
    return f"{type(error).__name__}: {error}"


def _backoff_seconds(failed_attempt: int) -> float:
    return DOWNLOAD_BACKOFF_BASE * (2 ** (failed_attempt - 1)) * random.uniform(*DOWNLOAD_JITTER)


def _lookup_backoff(failed_attempt: int) -> float:
    return LOOKUP_BACKOFF_BASE * (2 ** (failed_attempt - 1))


def _read_json(url: str, token: str | None):
    for attempt in range(1, LOOKUP_ATTEMPTS + 1):
        try:
            with _request(url, token) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code < 500 or error.code >= 600 or attempt >= LOOKUP_ATTEMPTS:
                raise
            detail = _error_text(error)
            error.close()
            delay = _lookup_backoff(attempt)
            print(
                f"Lookup attempt {attempt}/{LOOKUP_ATTEMPTS} failed: {detail}; "
                f"retrying in {delay:.1f}s.",
                flush=True,
            )
            time.sleep(delay)
    raise SiteError(f"Could not read {url}. Refusing to publish a site without its earlier runs.")


def _redirect_target(current: str, error: urllib.error.HTTPError) -> str:
    headers = error.headers
    location = headers.get("Location") if headers is not None else None
    if not location:
        raise SiteError(
            f"Redirect ({error.code}) from {current} had no Location header. "
            "Refusing to publish a site without its earlier runs."
        ) from error
    nxt = urllib.parse.urljoin(current, location.strip())
    if urllib.parse.urlsplit(nxt).scheme not in ("http", "https"):
        raise SiteError(f"Refusing to follow a redirect from {current} to {nxt}.") from error
    return nxt


def _read_following_redirects(url: str, token: str | None) -> bytes:
    current = url
    home = _host(url)
    for _hop in range(_MAX_REDIRECTS + 1):
        try:
            with _open_once(current, token if _host(current) == home else None) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            try:
                if error.code not in _REDIRECTS:
                    raise
                current = _redirect_target(current, error)
            finally:
                error.close()
            continue
    raise SiteError(
        f"Too many redirects downloading the Pages artifact (stopped at {current}). "
        "Refusing to publish a site without its earlier runs."
    )


def _download_pages_archive(url: str, token: str | None) -> bytes:
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            body = _read_following_redirects(url, token)
        except SiteError:
            raise
        except Exception as error:
            detail = _error_text(error)
            if attempt < DOWNLOAD_ATTEMPTS and _retryable(error):
                delay = _backoff_seconds(attempt)
                print(
                    f"Pages artifact download attempt {attempt}/{DOWNLOAD_ATTEMPTS} "
                    f"failed: {detail}; retrying in {delay:.1f}s.",
                    flush=True,
                )
                time.sleep(delay)
                continue
            print(
                f"Pages artifact download attempt {attempt}/{DOWNLOAD_ATTEMPTS} failed: {detail}.",
                flush=True,
            )
            noun = "attempt" if attempt == 1 else "attempts"
            raise SiteError(
                f"Could not download the previous {PAGES_ARTIFACT} artifact from {url} "
                f"after {attempt} {noun} ({detail}). "
                "Refusing to publish a site without its earlier runs."
            ) from error
        print(
            f"Pages artifact download attempt {attempt}/{DOWNLOAD_ATTEMPTS} "
            f"succeeded ({len(body)} bytes).",
            flush=True,
        )
        return body
    raise SiteError(
        f"Could not download the previous {PAGES_ARTIFACT} artifact from {url}. "
        "Refusing to publish a site without its earlier runs."
    )


def _matches_pages_artifact_name(name: object, pages_artifact: str | None = None) -> bool:
    if not isinstance(name, str) or not name:
        return False
    if pages_artifact:
        return name == pages_artifact
    return PAGES_ARTIFACT_PATTERN.fullmatch(name) is not None


def _list_pages_artifacts(
    repository: str,
    token: str | None,
    api: str,
    *,
    pages_artifact: str | None = None,
) -> tuple[list[dict], str]:
    artifacts: list[dict] = []
    name_query = f"name={urllib.parse.quote(pages_artifact, safe='')}&" if pages_artifact else ""
    for page in range(1, MAX_ARTIFACT_PAGES + 1):
        url = f"{api}/repos/{repository}/actions/artifacts?{name_query}per_page=100&page={page}"
        try:
            payload = _read_json(url, token)
            batch = payload["artifacts"]
        except urllib.error.HTTPError as error:
            if error.code == 404 and page == 1:
                error.close()
                return [], "artifact listing returned HTTP 404"
            detail = _error_text(error)
            error.close()
            raise SiteError(
                f"Could not list {pages_artifact or PAGES_ARTIFACT} artifacts: {detail}. "
                "Refusing to publish a site without its earlier runs."
            ) from error
        except OSError as error:
            raise SiteError(
                f"Could not list {pages_artifact or PAGES_ARTIFACT} artifacts: "
                f"{_error_text(error)}. "
                "Refusing to publish a site without its earlier runs."
            ) from error
        artifacts.extend(
            item
            for item in batch
            if isinstance(item, dict)
            and _matches_pages_artifact_name(item.get("name"), pages_artifact)
        )
        if len(batch) < 100:
            return artifacts, "none listed"
    return artifacts, "none listed"


def extract_pages_reports(archive: bytes, destination: Path) -> int:
    """Extract only the `reports/` subtree from a GitHub Pages zip (`artifact.tar`).

    Validates every archive entry against path traversal and non-regular files before
    writing anything. Stale manual HTML outside `reports/` is never extracted into
    `destination`. If the previous Pages archive comes from a manual-only deploy before
    verification reports were enabled, 0 files are extracted and no error is raised.
    """
    with zipfile.ZipFile(io.BytesIO(archive)) as outer:
        tar_bytes = outer.read("artifact.tar")
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        members = tar.getmembers()
        normalized: list[tuple[tarfile.TarInfo, tuple[str, ...]]] = []
        for member in members:
            raw = member.name
            if raw.startswith("/") or "\\" in raw:
                raise SiteError(f"Unsafe path in the Pages artifact: {raw}")
            parts = PurePosixPath(raw).parts
            if any(part == ".." for part in parts):
                raise SiteError(f"Unsafe path in the Pages artifact: {raw}")
            if not (member.isfile() or member.isdir()):
                raise SiteError(f"Unexpected entry in the Pages artifact: {raw}")
            normalized.append((member, parts))

        has_reports_prefix = any(parts and parts[0] == "reports" for _, parts in normalized)
        has_standalone_runs = not has_reports_prefix and any(
            len(parts) >= 5 and parts[2] == "runs" and parts[4] == "run.json"
            for member, parts in normalized
            if member.isfile()
        )

        selected: list[tuple[tarfile.TarInfo, Path]] = []
        for member, parts in normalized:
            if not member.isfile() or not parts:
                continue
            if has_reports_prefix:
                if parts[0] == "reports" and len(parts) >= 2:
                    rel = Path(*parts[1:])
                    selected.append((member, destination / rel))
                elif parts == (MARKER,) and not any(
                    p == ("reports", MARKER) for _, p in normalized
                ):
                    selected.append((member, destination / MARKER))
            elif has_standalone_runs:
                selected.append((member, destination / Path(*parts)))

        if len(selected) > MAX_RESTORED_FILES:
            raise SiteError(f"The Pages artifact has too many files ({len(selected)}).")
        total = sum(member.size for member, _ in selected)
        if total > MAX_RESTORED_BYTES:
            raise SiteError(f"The Pages artifact reports/ subtree is too large ({total} bytes).")
        targets = [out for _, out in selected]
        if len(set(targets)) != len(targets):
            raise SiteError("The Pages artifact lists the same reports/ file more than once.")

        destination.mkdir(parents=True, exist_ok=True)
        root = destination.resolve()
        for member, out_path in selected:
            _require_contained(root, out_path)
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            out_path.parent.mkdir(parents=True, exist_ok=True)
            _require_contained(root, out_path)
            out_path.write_bytes(extracted.read())
        return len(selected)


def _require_contained(root: Path, path: Path) -> None:
    """Refuse to write through a symlink or outside the restore destination."""
    current = path
    while True:
        if current.is_symlink():
            raise SiteError(f"Refusing to restore through a symlink: {current}")
        if current.resolve() == root or current.parent == current:
            break
        current = current.parent
    resolved = path.resolve()
    if resolved != root and not resolved.is_relative_to(root):
        raise SiteError(f"Unsafe path outside the reports directory: {path}")


def _unpack_pages_archive(archive: bytes, destination: Path) -> int:
    try:
        return extract_pages_reports(archive, destination)
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, KeyError) as error:
        raise SiteError(
            f"The previous {PAGES_ARTIFACT} artifact downloaded but is not a valid "
            f"Pages archive ({type(error).__name__}: {error}). "
            "Refusing to publish a site without its earlier runs."
        ) from error


def _pages_candidates(
    artifacts: list[dict],
    branch: str,
    current_run: int | None,
    *,
    pages_artifact: str | None = None,
) -> list[dict]:
    return [
        a
        for a in artifacts
        if _matches_pages_artifact_name(a.get("name"), pages_artifact)
        and (a.get("workflow_run") or {}).get("head_branch") == branch
        and (a.get("workflow_run") or {}).get("id") != current_run
    ]


def _github_id(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _same_repository(head_id: object, run_repository_id: object, repository_id: int) -> bool:
    return _github_id(head_id) == repository_id and _github_id(run_repository_id) == repository_id


def _listed_run_is_this_repository(artifact: dict, repository_id: int) -> bool:
    workflow_run = artifact.get("workflow_run") or {}
    return _same_repository(
        workflow_run.get("head_repository_id"),
        workflow_run.get("repository_id"),
        repository_id,
    )


def _fetched_run_is_pages_deploy(
    run: dict | None,
    repository_id: int,
    branch: str,
    *,
    deploy_workflow: str | None = None,
) -> bool:
    if not isinstance(run, dict):
        return False
    if run.get("event") not in ALLOWED_DEPLOY_EVENTS:
        return False
    if deploy_workflow is not None and run.get("path") != deploy_workflow:
        return False
    if run.get("head_branch") != branch:
        return False
    head = run.get("head_repository")
    own = run.get("repository")
    if not isinstance(head, dict) or not isinstance(own, dict):
        return False
    return _same_repository(head.get("id"), own.get("id"), repository_id)


def _artifact_is_pages_deploy(
    artifact: dict,
    repository_id: int,
    branch: str,
    run_of,
    *,
    deploy_workflow: str | None = None,
) -> bool:
    if not _listed_run_is_this_repository(artifact, repository_id):
        return False
    run_id = (artifact.get("workflow_run") or {}).get("id")
    if run_id is None:
        return False
    return _fetched_run_is_pages_deploy(
        run_of(run_id), repository_id, branch, deploy_workflow=deploy_workflow
    )


def deployed_pages_artifact(
    artifacts: list[dict],
    branch: str,
    current_run: int | None,
    conclusion_of,
    *,
    accept=None,
    pages_artifact: str | None = None,
) -> dict | None:
    """Newest Pages artifact on `branch` whose Pages deploy job succeeded."""
    ranked = sorted(
        _pages_candidates(artifacts, branch, current_run, pages_artifact=pages_artifact),
        key=lambda artifact: (artifact.get("created_at") or "", artifact.get("id") or 0),
        reverse=True,
    )
    for artifact in ranked:
        run_id = (artifact.get("workflow_run") or {}).get("id")
        if run_id is None:
            continue
        if accept is not None and not accept(artifact):
            continue
        if conclusion_of(run_id) != "success":
            continue
        if artifact.get("expired"):
            raise SiteError(
                f"The last successfully deployed Pages artifact ({artifact['id']}, "
                f"{artifact['created_at']}) has expired; restoring would drop the "
                "published run history."
            )
        return artifact
    return None


def _matches_pages_job(job_name: object, pages_job: str | None = None) -> bool:
    if not isinstance(job_name, str) or not job_name.strip():
        return False
    leaf = job_name.rsplit(" / ", 1)[-1].strip()
    if pages_job:
        return job_name == pages_job or leaf == pages_job
    return leaf.lower() in DEFAULT_PAGES_JOBS


def _pages_conclusion(
    repository: str,
    run_id: int,
    token: str | None,
    api: str,
    *,
    pages_job: str | None = None,
) -> str | None:
    page = 1
    conclusions: list[str | None] = []
    while True:
        url = (
            f"{api}/repos/{repository}/actions/runs/{run_id}/jobs"
            f"?filter=latest&per_page=100&page={page}"
        )
        try:
            payload = _read_json(url, token)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                error.close()
                return None
            detail = _error_text(error)
            error.close()
            raise SiteError(
                f"Could not read the pages deploy for run {run_id}: {detail}. "
                "Refusing to publish a site without its earlier runs."
            ) from error
        except OSError as error:
            raise SiteError(
                f"Could not read the pages deploy for run {run_id}: {_error_text(error)}. "
                "Refusing to publish a site without its earlier runs."
            ) from error
        jobs = payload.get("jobs") or []
        for job in jobs:
            if isinstance(job, dict) and _matches_pages_job(job.get("name"), pages_job):
                conclusions.append(job.get("conclusion"))
        if len(jobs) < 100:
            break
        page += 1
    if not conclusions:
        return None
    if all(c == "success" for c in conclusions):
        return "success"
    return next((c for c in conclusions if c != "success"), None)


def _conclusion_of(repository: str, token: str | None, api: str, *, pages_job: str | None = None):
    cache: dict[int, str | None] = {}

    def conclusion_of(run_id: int) -> str | None:
        if run_id not in cache:
            cache[run_id] = _pages_conclusion(repository, run_id, token, api, pages_job=pages_job)
        return cache[run_id]

    return conclusion_of


def _repository_id(repository: str, token: str | None, api: str) -> int:
    url = f"{api}/repos/{repository}"
    try:
        payload = _read_json(url, token)
    except urllib.error.HTTPError as error:
        detail = _error_text(error)
        error.close()
        raise SiteError(
            f"Could not read repository {repository}: {detail}. "
            "Refusing to publish a site without its earlier runs."
        ) from error
    except OSError as error:
        raise SiteError(
            f"Could not read repository {repository}: {_error_text(error)}. "
            "Refusing to publish a site without its earlier runs."
        ) from error
    repo_id = _github_id(payload.get("id") if isinstance(payload, dict) else None)
    if repo_id is None:
        raise SiteError(
            f"Could not read repository {repository}: response has no id. "
            "Refusing to publish a site without its earlier runs."
        )
    return repo_id


def _workflow_run(repository: str, run_id: int, token: str | None, api: str) -> dict | None:
    url = f"{api}/repos/{repository}/actions/runs/{run_id}"
    try:
        payload = _read_json(url, token)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            error.close()
            return None
        detail = _error_text(error)
        error.close()
        raise SiteError(
            f"Could not read workflow run {run_id}: {detail}. "
            "Refusing to publish a site without its earlier runs."
        ) from error
    except OSError as error:
        raise SiteError(
            f"Could not read workflow run {run_id}: {_error_text(error)}. "
            "Refusing to publish a site without its earlier runs."
        ) from error
    if not isinstance(payload, dict):
        raise SiteError(
            f"Could not read workflow run {run_id}: response was not an object. "
            "Refusing to publish a site without its earlier runs."
        )
    return payload


def _workflow_runs(repository: str, token: str | None, api: str):
    cache: dict[int, dict | None] = {}

    def run_of(run_id: int) -> dict | None:
        if run_id not in cache:
            cache[run_id] = _workflow_run(repository, run_id, token, api)
        return cache[run_id]

    return run_of


def _fetch_status(url: str) -> int:
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as error:
        try:
            return error.code
        finally:
            error.close()


def _reports_base_url(site_url: str) -> str:
    trimmed = site_url.rstrip("/")
    if trimmed.endswith("/reports"):
        return trimmed + "/"
    return trimmed + "/reports/"


def live_history(site_url: str) -> str | None:
    """Return the live URL proving published report history exists, or None on HTTP 404.

    Probes `reports/site.json` and `reports/index.html` so a robot repository that
    already hosts a documentation manual (`index.html` HTTP 200) without any published
    `reports/` section yet is recognized as having no published report history.
    """
    base = _reports_base_url(site_url)
    for name in (MARKER, "index.html"):
        try:
            status = _fetch_status(base + name)
        except OSError as error:
            raise SiteError(f"Cannot check {base}{name} for published history: {error}") from error
        if status == 200:
            return base + name
        if status != 404:
            raise SiteError(f"Cannot check {base}{name} for published history: HTTP {status}")
    return None


def _discover_pages_site_url(
    repository: str, token: str | None, api: str
) -> tuple[str | None, bool]:
    """Query GitHub Pages API when `--site-url` was omitted.

    Returns `(html_url, pages_not_configured)`. HTTP 404 means GitHub Pages has no
    site configured for the repository yet.
    """
    if not repository or not token:
        return None, False
    url = f"{api}/repos/{repository}/pages"
    try:
        payload = _read_json(url, token)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            error.close()
            return None, True
        error.close()
        return None, False
    except OSError:
        return None, False
    html_url = payload.get("html_url") if isinstance(payload, dict) else None
    if isinstance(html_url, str) and html_url.strip():
        return html_url.strip(), False
    return None, False


def restore(
    destination: Path,
    repository: str,
    branch: str = "main",
    current_run: int | None = None,
    token: str | None = None,
    api: str = "https://api.github.com",
    *,
    dry_run: bool = False,
    site_url: str | None = None,
    reset_history: bool = False,
    pages_artifact: str | None = None,
    pages_job: str | None = None,
    deploy_workflow: str | None = None,
) -> dict | None:
    """Restore earlier runs from the last deployed Pages artifact into `destination`."""
    destination.mkdir(parents=True, exist_ok=True)
    if reset_history:
        print(f"reset-history set: starting a new site; {PAGES_ARTIFACT} history is dropped.")
        return None
    if not repository or "/" not in repository:
        raise SiteError(
            "Repository owner/name is required to restore Pages history "
            "(pass --repository or set GITHUB_REPOSITORY)."
        )
    artifacts, why_empty = _list_pages_artifacts(
        repository, token, api, pages_artifact=pages_artifact
    )
    candidates = _pages_candidates(artifacts, branch, current_run, pages_artifact=pages_artifact)
    if candidates:
        repository_id = _repository_id(repository, token, api)
        run_of = _workflow_runs(repository, token, api)

        def accept(item: dict) -> bool:
            return _artifact_is_pages_deploy(
                item, repository_id, branch, run_of, deploy_workflow=deploy_workflow
            )

        artifact = deployed_pages_artifact(
            artifacts,
            branch,
            current_run,
            _conclusion_of(repository, token, api, pages_job=pages_job),
            accept=accept,
            pages_artifact=pages_artifact,
        )
        if artifact is None:
            why_empty = (
                "no Pages artifact on this repository and branch whose Pages deploy succeeded"
            )
    else:
        artifact = None

    if artifact is None:
        effective_url = site_url
        pages_absent = False
        if not effective_url:
            effective_url, pages_absent = _discover_pages_site_url(repository, token, api)
        if pages_absent:
            print(
                f"WARNING: No earlier {PAGES_ARTIFACT} artifact on {branch} ({why_empty}) and "
                "GitHub Pages is not yet published: starting without run history.",
                flush=True,
            )
            return None
        if not effective_url:
            raise SiteError(
                f"No earlier {PAGES_ARTIFACT} artifact on {branch} ({why_empty}) and no "
                "--site-url to check for published history; pass --site-url, or "
                "--reset-history to start anew."
            )
        published = live_history(effective_url)
        if published:
            raise SiteError(
                f"No earlier {PAGES_ARTIFACT} artifact on {branch} ({why_empty}), but "
                f"{published} is live: the artifact probably expired. Refusing to publish "
                "a site without its run history; run with reset-history to start anew."
            )
        print(
            f"WARNING: No earlier {PAGES_ARTIFACT} artifact on {branch} ({why_empty}) and "
            "nothing is published under reports/: starting without run history.",
            flush=True,
        )
        return None

    run_num = artifact["workflow_run"]["id"]
    print(
        f"Restoring run history from artifact {artifact['id']} (run {run_num}, "
        f"{artifact['created_at']})."
    )
    if dry_run:
        return artifact
    count = _unpack_pages_archive(
        _download_pages_archive(artifact["archive_download_url"], token), destination
    )
    print(f"Restored {count} files into {destination}.")
    return artifact


# ---------------------------------------------------------------- validation & outcomes


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _stored_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def read_json_object(path: Path) -> dict:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SiteError(f"{path}: not valid JSON ({error.msg} at line {error.lineno})") from None
    except OSError as error:
        raise SiteError(f"{path}: cannot read file ({error})") from error
    if not isinstance(parsed, dict):
        raise SiteError(f"{path}: expected a JSON object, found {type(parsed).__name__}")
    return parsed


def read_report(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SiteError(f"{path}: not valid JSON ({error.msg} at line {error.lineno})") from None


def check_evaluators(report: object, where: Path) -> None:
    if not isinstance(report, dict) or "source_bundle" not in report:
        return
    bundle = report.get("source_bundle")
    results = bundle.get("results") if isinstance(bundle, dict) else None
    summaries = report.get("summaries")
    unsupported = (
        isinstance(summaries, list)
        and bool(summaries)
        and all(isinstance(s, dict) and s.get("grade") == "UNSUPPORTED" for s in summaries)
    )
    if not results:
        if unsupported:
            return
        raise SiteError(
            f"{where}: no assessed trials in source_bundle.results, so no evaluator "
            "result to show. Only an UNSUPPORTED report with no trials run publishes "
            "without one."
        )
    for index, result in enumerate(results, start=1):
        trial = result.get("id") if isinstance(result, dict) else None
        trial = trial if _text(trial) else f"trial {index}"
        evaluated = result.get("evaluator_results") if isinstance(result, dict) else None
        if not isinstance(evaluated, list) or not evaluated:
            raise SiteError(
                f"{where}: {trial} has no evaluator result. The report would show a "
                "placeholder evaluator. Append the evaluator results to the bundle "
                "before rendering the report."
            )
        for item in evaluated:
            evaluator = item.get("evaluator") if isinstance(item, dict) else None
            name = evaluator.get("id") if isinstance(evaluator, dict) else None
            name = name if _text(name) else "(no id)"
            descriptor = item.get("descriptor") if isinstance(item, dict) else None
            if not isinstance(descriptor, dict):
                raise SiteError(f"{where}: {trial} evaluator {name} has no descriptor")
            for field in ("title", "purpose"):
                if not _text(descriptor.get(field)):
                    raise SiteError(f"{where}: {trial} evaluator {name} descriptor has no {field}")


REPORT_VERSIONS = {"1.0.0"}
SUMMARY_FIELDS = ("capability", "system", "grade")


def reproducibility_statement(summary: dict) -> str | None:
    block = summary.get("reproducibility")
    if not isinstance(block, dict) or block.get("compared") is not True:
        return None
    return _stored_text(block.get("statement"))


def reproducibility_finding(summary: dict) -> str | None:
    block = summary.get("reproducibility")
    if not isinstance(block, dict) or block.get("compared") is not True:
        return None
    finding = block.get("finding")
    if isinstance(finding, dict):
        return _stored_text(finding.get("detail"))
    return _stored_text(finding)


def outcome(report: object, where: str = "report.json") -> list[dict]:
    if not isinstance(report, dict):
        raise SiteError(f"{where}: expected a JSON object, found {type(report).__name__}")
    version = report.get("report_version")
    if not isinstance(version, str) or version not in REPORT_VERSIONS:
        raise SiteError(
            f"{where}: report_version {version!r} is not one this site reads "
            f"({', '.join(sorted(REPORT_VERSIONS))})"
        )
    summaries = report.get("summaries")
    if not isinstance(summaries, list) or not summaries:
        raise SiteError(f"{where}: report_version {version} without a non-empty summaries list")
    for index, s in enumerate(summaries):
        if not isinstance(s, dict):
            raise SiteError(f"{where}: summaries[{index}] is {type(s).__name__}, not an object")
        missing = [f for f in SUMMARY_FIELDS if f not in s]
        if not isinstance(s.get("system"), dict) or "id" not in s["system"]:
            missing.append("system.id")
        if missing:
            raise SiteError(f"{where}: summaries[{index}] lacks {', '.join(missing)}")
    rows = []
    for s in summaries:
        row: dict = {
            "capability": s["capability"],
            "system": s["system"]["id"],
            "grade": s["grade"],
            "passed": s.get("k"),
            "trials": s.get("n"),
            "required": s.get("required_passes"),
        }
        statement = reproducibility_statement(s)
        if statement is not None:
            row["statement"] = statement
        else:
            finding = reproducibility_finding(s)
            if finding is not None:
                row["finding"] = finding
        rows.append(row)
    return rows


THRESHOLDS_NAME = "thresholds.json"


def _cited_path(uri: object) -> str | None:
    if not isinstance(uri, str) or not uri or "\\" in uri or "://" in uri or uri.startswith("/"):
        return None
    parts = PurePosixPath(uri).parts
    if not parts or any(part in ("", ".", "..") for part in parts) or parts[-1] != THRESHOLDS_NAME:
        return None
    return "/".join(parts)


def grading_inputs(source: Path, report: object) -> list[dict]:
    bundle = report.get("source_bundle") if isinstance(report, dict) else None
    results = bundle.get("results") if isinstance(bundle, dict) else None
    cited: dict[str, str] = {}
    for result in results if isinstance(results, list) else []:
        evaluated = result.get("evaluator_results") if isinstance(result, dict) else None
        for item in evaluated if isinstance(evaluated, list) else []:
            inputs = item.get("inputs") if isinstance(item, dict) else None
            for entry in inputs if isinstance(inputs, list) else []:
                if not isinstance(entry, dict):
                    continue
                uri = entry.get("uri")
                named = (
                    isinstance(uri, str) and uri.rstrip("/").rsplit("/", 1)[-1] == THRESHOLDS_NAME
                )
                if entry.get("role") != "thresholds" and not named:
                    continue
                relative = _cited_path(uri)
                if relative is None:
                    raise SiteError(
                        f"{source}: substituted: an evaluator result cites {uri!r}, not a "
                        f"{THRESHOLDS_NAME} inside the report"
                    )
                digest = entry.get("sha256")
                previous = cited.setdefault(relative, digest)
                if previous != digest:
                    raise SiteError(
                        f"{source}: substituted: {relative} is cited with two different "
                        f"sha256 values ({previous} and {digest})"
                    )
    listed = _parse_sha256sums(source / "SHA256SUMS") if (source / "SHA256SUMS").is_file() else {}
    found = []
    for relative, digest in sorted(cited.items()):
        path = source.joinpath(*PurePosixPath(relative).parts)
        current = path
        while current != source:
            if current.is_symlink():
                raise SiteError(f"{source}: substituted: {relative} is a symlink")
            current = current.parent
        if not path.is_file():
            raise SiteError(
                f"{source}: missing: {relative} is cited by the evaluator results but is not "
                "in the run folder"
            )
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if relative in listed and listed[relative] != actual:
            raise SiteError(f"{path}: does not match SHA256SUMS")
        if actual != digest:
            raise SiteError(
                f"{source}: altered: {relative} sha256 is {actual}, not the cited {digest!r}"
            )
        found.append({"role": "thresholds", "path": relative, "sha256": actual, "bytes": data})
    return found


def _parse_sha256sums(sums_path: Path) -> dict[str, str]:
    listed: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, sep, name = line.partition("  ")
        cleaned = name.strip()
        if not sep or not cleaned:
            raise SiteError(f"{sums_path}: malformed checksum line {line!r}")
        parts = PurePosixPath(cleaned).parts
        if cleaned.startswith("/") or "\\" in cleaned or any(p in ("", ".", "..") for p in parts):
            raise SiteError(f"{sums_path}: unsafe path {cleaned!r} in SHA256SUMS")
        listed[cleaned] = digest.strip()
    return listed


def check_reports(directory: Path) -> object:
    for required in REQUIRED_RUN_FILES:
        req_path = directory / required
        if req_path.is_symlink():
            raise SiteError(f"{directory}: {required} must not be a symlink")
        if not req_path.is_file():
            raise SiteError(f"{directory}: missing {required}")
    listed = _parse_sha256sums(directory / "SHA256SUMS")
    for name in REPORT_FILES:
        if name not in listed:
            raise SiteError(f"{directory}: SHA256SUMS has no entry for {name}")
    for name, expected in listed.items():
        path = directory.joinpath(*PurePosixPath(name).parts)
        if path.is_symlink() or not path.is_file():
            raise SiteError(f"{directory}: missing {name} listed in SHA256SUMS")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected != actual:
            raise SiteError(f"{path}: does not match SHA256SUMS")
    report = read_report(directory / "report.json")
    outcome(report, str(directory / "report.json"))
    check_evaluators(report, directory / "report.json")
    return report


def release_tag(commit: str, git_dir: Path | None = None) -> str | None:
    if not git_dir or not COMMIT_SHA.fullmatch(commit):
        return None

    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(git_dir), *args], capture_output=True, text=True, check=False
        )
        return done.stdout.strip() if done.returncode == 0 else ""

    found = []
    for name in git("tag", "--list", "--points-at", commit).splitlines():
        if (
            RELEASE_TAG.fullmatch(name)
            and git("rev-parse", f"refs/tags/{name}^{{commit}}") == commit
        ):
            found.append(name)
    return max(found, key=lambda name: tuple(map(int, name[1:].split(".")))) if found else None


def _stamp(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as error:
        raise SiteError(
            f"Unexpected timestamp {value!r}; expected UTC ISO-8601 YYYY-MM-DDTHH:MM:SSZ"
        ) from error


def _robot_slug_from_record(record: dict, where: Path) -> str:
    explicit = _stored_text(record.get("robot_slug") or record.get("robot_id"))
    if explicit is not None:
        slug = explicit.removeprefix("livingproof-").lower()
    else:
        robot = record.get("robot")
        if isinstance(robot, dict):
            sub_id = _stored_text(robot.get("slug") or robot.get("id"))
            if sub_id is not None:
                slug = sub_id.removeprefix("livingproof-").lower()
            else:
                repo = _stored_text(robot.get("repository"))
                if repo is None:
                    raise SiteError(f"{where}: robot.repository is missing in run.json")
                repo_tail = repo.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]
                slug = repo_tail.removeprefix("livingproof-").lower()
        elif isinstance(robot, str) and robot.strip():
            slug = robot.strip().removeprefix("livingproof-").lower()
        else:
            raise SiteError(f"{where}: missing robot metadata in run.json")
    if not SEGMENT.fullmatch(slug):
        raise SiteError(f"{where}: unsafe robot path segment {slug!r}")
    return slug


def _normalize_run_record(
    raw_record: dict,
    report: object,
    where: Path,
    *,
    git_dir: Path | None = None,
) -> tuple[str, str, str, dict]:
    robot_slug = _robot_slug_from_record(raw_record, where)
    variant = _stored_text(raw_record.get("variant"))
    if variant is not None and variant.startswith(f"{robot_slug}-"):
        stripped = variant.removeprefix(f"{robot_slug}-")
        if stripped:
            variant = stripped
    robot_raw = raw_record.get("robot")
    config = _stored_text(robot_raw.get("configuration")) if isinstance(robot_raw, dict) else None
    if variant is not None and config is not None and config.lower() not in variant.split("-"):
        variant = f"{variant}-{config.lower()}"
    if variant is None or not SEGMENT.fullmatch(variant):
        raise SiteError(f"{where}: invalid or missing variant {raw_record.get('variant')!r}")

    run_block = raw_record.get("run")
    if not isinstance(run_block, dict):
        raise SiteError(f"{where}: run.json lacks a 'run' object")
    run_id = _stored_text(run_block.get("id"))
    if run_id is None or not RUN_ID.fullmatch(run_id) or run_id in {".", ".."}:
        raise SiteError(f"{where}: invalid or missing run.id {run_block.get('id')!r}")
    created = _stored_text(run_block.get("created"))
    if created is None:
        raise SiteError(f"{where}: missing run.created timestamp")
    _stamp(created)

    computed_outcomes = outcome(report, str(where.with_name("report.json")))
    stored_outcomes = raw_record.get("outcome")
    if stored_outcomes is not None:
        if not isinstance(stored_outcomes, list) or len(stored_outcomes) != len(computed_outcomes):
            raise SiteError(f"{where}: run.json outcome count disagrees with report.json")
        for idx, (stored_item, computed_item) in enumerate(zip(stored_outcomes, computed_outcomes)):
            if (
                not isinstance(stored_item, dict)
                or stored_item.get("grade") != computed_item["grade"]
            ):
                raise SiteError(
                    f"{where}: run.json outcome[{idx}] grade disagrees with report.json"
                )

    record = dict(raw_record)
    record["variant"] = variant
    record["outcome"] = computed_outcomes

    robot_raw = raw_record.get("robot")
    if isinstance(robot_raw, dict):
        robot_info = dict(robot_raw)
        repo = _stored_text(robot_info.get("repository")) or robot_slug
        commit_sha = _stored_text(robot_info.get("commit")) or ""
        robot_info.setdefault("repository", repo)
        robot_info.setdefault("commit", commit_sha)
        if not _stored_text(robot_info.get("url")):
            tag = _stored_text(robot_info.get("tag"))
            base_repo = repo if repo.startswith("https://") else f"https://github.com/{repo}"
            if tag:
                robot_info["url"] = f"{base_repo}/releases/tag/{tag}"
            elif commit_sha:
                robot_info["url"] = f"{base_repo}/tree/{commit_sha}"
            else:
                robot_info["url"] = base_repo
    else:
        robot_info = {
            "repository": robot_slug,
            "commit": "",
            "url": f"https://github.com/{robot_slug}",
        }
    if not _stored_text(robot_info.get("software_version")) and isinstance(report, dict):
        for summary in report.get("summaries") or []:
            if isinstance(summary, dict) and isinstance(summary.get("system"), dict):
                sw = _stored_text(summary["system"].get("version"))
                if sw is not None:
                    robot_info["software_version"] = sw
                    break
    record["robot"] = robot_info

    commit_raw = raw_record.get("commit")
    commit_info = dict(commit_raw) if isinstance(commit_raw, dict) else {}
    sha = _stored_text(commit_info.get("sha")) or _stored_text(robot_info.get("commit")) or ""
    commit_info["sha"] = sha
    if not _stored_text(commit_info.get("url")) and sha:
        repo_url = _spec_repository_url({"repository": robot_info.get("repository")})
        if repo_url:
            commit_info["url"] = f"{repo_url}/commit/{sha}"
    existing_tag = _stored_text(commit_info.get("tag"))
    if existing_tag is not None:
        if not RELEASE_TAG.fullmatch(existing_tag):
            raise SiteError(f"{where}: unexpected release tag {existing_tag!r}")
    elif sha and git_dir is not None:
        resolved_tag = release_tag(sha, git_dir)
        if resolved_tag is not None:
            commit_info["tag"] = resolved_tag
    record["commit"] = commit_info

    poster = raw_record.get("poster")
    if not isinstance(poster, dict) or not _stored_text(poster.get("name")):
        record["poster"] = {"type": "ci", "name": "CI"}

    releases = raw_record.get("releases")
    record["releases"] = dict(releases) if isinstance(releases, dict) else {}

    failing = failing_evaluators(report)
    if failing:
        record["failing_evaluators"] = failing
    else:
        record.pop("failing_evaluators", None)

    markers = integrity_markers(report)
    if markers:
        record["integrity_status"] = markers
    else:
        record.pop("integrity_status", None)

    return robot_slug, variant, run_id, record


# ---------------------------------------------------------------- evaluators & chips


def _evaluator_documents(report: object) -> list[dict]:
    if not isinstance(report, dict):
        return []
    docs: list[dict] = []
    bundle = report.get("source_bundle")
    if isinstance(bundle, dict):
        results = bundle.get("results")
        if isinstance(results, list):
            for result in results:
                if not isinstance(result, dict):
                    continue
                items = result.get("evaluator_results")
                if isinstance(items, list):
                    docs.extend(d for d in items if isinstance(d, dict))
                prov = result.get("provenance")
                if isinstance(prov, dict):
                    p_items = prov.get("evaluator_results")
                    if isinstance(p_items, list):
                        docs.extend(d for d in p_items if isinstance(d, dict))
                    one = prov.get("evaluator_result")
                    if isinstance(one, dict):
                        docs.append(one)
                original = result.get("original_result")
                if isinstance(original, dict):
                    docs.append(original)
        assessment = bundle.get("assessment")
        integrity = assessment.get("integrity_results") if isinstance(assessment, dict) else None
        if isinstance(integrity, dict):
            for item in integrity.values():
                if isinstance(item, dict):
                    docs.append(item)
                elif isinstance(item, list):
                    docs.extend(d for d in item if isinstance(d, dict))
    summaries = report.get("summaries")
    if isinstance(summaries, list):
        for summary in summaries:
            if not isinstance(summary, dict):
                continue
            items = summary.get("evaluator_results")
            if isinstance(items, list):
                docs.extend(d for d in items if isinstance(d, dict))
    return docs


def _finding_summary(findings: list, descriptor: dict) -> str | None:
    for item in findings:
        if isinstance(item, dict):
            text = _stored_text(item.get("text")) or _stored_text(item.get("summary"))
            if text is not None:
                return text
        elif isinstance(item, str):
            text = _stored_text(item)
            if text is not None:
                return text
    return _stored_text(descriptor.get("details_copy"))


INCONCLUSIVE_COPY = "Not graded: no check applied to this run."
EVALUATOR_ANCHOR = "#summary"
ANCHOR_REPORTER = (3, 14, 0)
_REPORTER_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")


def evaluator_slug(ev_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", str(ev_id)) or "evaluator"


def reporter_has_evaluator_anchors(record: dict) -> bool:
    releases = record.get("releases") if isinstance(record.get("releases"), dict) else {}
    reporter = releases.get("reporter") if isinstance(releases.get("reporter"), dict) else {}
    tag = _stored_text(reporter.get("tag"))
    found = _REPORTER_TAG.fullmatch(tag) if tag else None
    return found is not None and tuple(int(n) for n in found.groups()) >= ANCHOR_REPORTER


def page_evaluator_ids(report: object) -> set[str]:
    ids = set()
    for doc in _evaluator_documents(report):
        ev = doc.get("evaluator") if isinstance(doc.get("evaluator"), dict) else {}
        ev_id = _stored_text(ev.get("id"))
        if ev_id is not None:
            ids.add(ev_id)
    return ids


def evaluator_fragment(record: dict, ev_id: object, report: object = None) -> str:
    ev_id_str = _stored_text(ev_id)
    if ev_id_str is None or not reporter_has_evaluator_anchors(record):
        return EVALUATOR_ANCHOR
    ids = page_evaluator_ids(report)
    slug = evaluator_slug(ev_id_str)
    if ev_id_str not in ids or any(o != ev_id_str and evaluator_slug(o) == slug for o in ids):
        return EVALUATOR_ANCHOR
    return f"#evaluator-{slug}"


def failing_evaluators(report: object) -> list[dict]:
    if not isinstance(report, dict):
        return []
    summaries = report.get("summaries")
    if isinstance(summaries, list) and summaries:
        grades = [s.get("grade") for s in summaries if isinstance(s, dict)]
        if grades and all(g in ("PASS", "UNSUPPORTED") for g in grades):
            return []
    seen: dict[str, dict] = {}
    for doc in _evaluator_documents(report):
        ev = doc.get("evaluator") if isinstance(doc.get("evaluator"), dict) else {}
        ev_id = _stored_text(ev.get("id"))
        if ev_id is None:
            continue
        desc = doc.get("descriptor") if isinstance(doc.get("descriptor"), dict) else {}
        raw_findings = (
            desc.get("findings") if isinstance(desc.get("findings"), list) else doc.get("findings")
        )
        findings = raw_findings if isinstance(raw_findings, list) else []
        grade = desc.get("grade") if "grade" in desc else doc.get("grade")
        integrity = doc.get("integrity") if isinstance(doc.get("integrity"), dict) else {}
        run_info = integrity.get("run") if isinstance(integrity.get("run"), dict) else {}
        status = run_info.get("test_system_status")
        spoke = (
            bool(findings)
            or grade == "FAIL"
            or (isinstance(grade, (int, float)) and not isinstance(grade, bool) and grade < 100)
            or (isinstance(status, str) and status.upper() in {"ERROR", "WARN", "FAIL"})
            or status == "INCONCLUSIVE"
        )
        if not spoke:
            continue
        title = _stored_text(ev.get("display_name")) or _stored_text(desc.get("title")) or ev_id
        summary = _finding_summary(findings, desc)
        if summary is None and status == "INCONCLUSIVE":
            summary = INCONCLUSIVE_COPY
        if ev_id not in seen:
            entry: dict = {"id": ev_id, "title": title, "href": EVALUATOR_ANCHOR}
            if summary is not None:
                entry["summary"] = summary
            if status == "INCONCLUSIVE":
                entry["not_graded"] = True
            seen[ev_id] = entry
        elif "summary" not in seen[ev_id] and summary is not None:
            seen[ev_id]["summary"] = summary
    return list(seen.values())


MARKED_INTEGRITY_STATUSES = ("ERROR", "INCONCLUSIVE")


def integrity_markers(report: object) -> list[dict]:
    if not isinstance(report, dict):
        return []
    found: dict[str, dict] = {}
    for doc in _evaluator_documents(report):
        integrity = doc.get("integrity") if isinstance(doc.get("integrity"), dict) else {}
        run_info = integrity.get("run") if isinstance(integrity.get("run"), dict) else {}
        status = run_info.get("test_system_status")
        if status not in MARKED_INTEGRITY_STATUSES:
            continue
        ev = doc.get("evaluator") if isinstance(doc.get("evaluator"), dict) else {}
        ev_id = _stored_text(ev.get("id"))
        if ev_id is None or ev_id in found:
            continue
        desc = doc.get("descriptor") if isinstance(doc.get("descriptor"), dict) else {}
        title = _stored_text(ev.get("display_name")) or _stored_text(desc.get("title")) or ev_id
        found[ev_id] = {"id": ev_id, "title": title, "status": status}
    return list(found.values())


# ---------------------------------------------------------------- evidence & run folders


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _infer_evidence_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".mcap":
        return "mcap"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in {".log", ".txt"}:
        return "log"
    return "note"


def _copy_run_evidence(
    source: Path, destination: Path, stored_evidence: object
) -> list[dict] | None:
    evidence_src = source / "evidence"
    source_files: list[Path] = []
    if evidence_src.exists():
        if evidence_src.is_symlink():
            raise SiteError(f"{source}: evidence directory must not be a symlink")
        for item in sorted(evidence_src.rglob("*")):
            if item.is_symlink():
                raise SiteError(f"{source}: symlink inside evidence is not allowed ({item})")
            if item.is_file() and item.suffix.lower() != ".html":
                source_files.append(item)

    if isinstance(stored_evidence, list) and stored_evidence:
        budget = MAX_RUN_EVIDENCE_BYTES
        copied_all = set()
        records = []
        for raw_entry in stored_evidence:
            if not isinstance(raw_entry, dict):
                continue
            entry = json.loads(json.dumps(raw_entry))
            files_meta = entry.get("files") if isinstance(entry.get("files"), list) else []
            resolved_files: list[Path] = []
            for fmeta in files_meta:
                rel_str = fmeta.get("path") if isinstance(fmeta, dict) else None
                if not isinstance(rel_str, str) or not rel_str.startswith("evidence/"):
                    continue
                parts = PurePosixPath(rel_str).parts
                if any(p in ("", ".", "..") for p in parts):
                    raise SiteError(f"{source}: unsafe evidence file path {rel_str!r}")
                candidate = source.joinpath(*parts)
                if candidate.is_file() and not candidate.is_symlink():
                    resolved_files.append(candidate)
            if resolved_files:
                size = sum(p.stat().st_size for p in resolved_files)
                if size > budget:
                    entry["preserved"] = False
                    entry["files"] = []
                    entry["reason"] = f"over the per-run evidence budget ({size} bytes)"
                else:
                    budget -= size
                    copied_entries = []
                    for p in resolved_files:
                        rel = p.relative_to(source)
                        dest_file = destination / rel
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(p, dest_file)
                        copied_all.add(p)
                        copied_entries.append(
                            {
                                "path": rel.as_posix(),
                                "sha256": _sha256(dest_file),
                                "bytes": p.stat().st_size,
                            }
                        )
                    entry["preserved"] = True
                    entry["files"] = copied_entries
            records.append(entry)
        remaining = [p for p in source_files if p not in copied_all]
        for p in remaining:
            size = p.stat().st_size
            rel = p.relative_to(source)
            if size > budget:
                records.append(
                    {
                        "label": p.stem.replace("-", " "),
                        "kind": _infer_evidence_kind(p),
                        "preserved": False,
                        "files": [],
                        "reason": f"over the per-run evidence budget ({size} bytes)",
                    }
                )
            else:
                budget -= size
                dest_file = destination / rel
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, dest_file)
                records.append(
                    {
                        "label": p.stem.replace("-", " "),
                        "kind": _infer_evidence_kind(p),
                        "preserved": True,
                        "files": [
                            {
                                "path": rel.as_posix(),
                                "sha256": _sha256(dest_file),
                                "bytes": size,
                            }
                        ],
                    }
                )
        return records or None

    if not source_files:
        return None

    budget = MAX_RUN_EVIDENCE_BYTES
    records = []
    for p in source_files:
        size = p.stat().st_size
        rel = p.relative_to(source)
        kind = _infer_evidence_kind(p)
        label = p.stem.replace("-", " ").replace("_", " ")
        if size > budget:
            records.append(
                {
                    "label": label,
                    "kind": kind,
                    "preserved": False,
                    "files": [],
                    "reason": f"over the per-run evidence budget ({size} bytes)",
                }
            )
            continue
        budget -= size
        dest_file = destination / rel
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest_file)
        records.append(
            {
                "label": label,
                "kind": kind,
                "preserved": True,
                "files": [
                    {
                        "path": rel.as_posix(),
                        "sha256": _sha256(dest_file),
                        "bytes": size,
                    }
                ],
            }
        )
    return records


def discover_run_folders(runs_dir: Path) -> list[Path]:
    """Find run folders inside `runs_dir` (including `runs_dir` itself if it holds `run.json`)."""
    if not runs_dir.exists() or not runs_dir.is_dir():
        raise SiteError(
            f"Runs directory {runs_dir} does not exist. Provide a directory of run folders "
            "each containing report.html, report.json, report.md, report.txt, SHA256SUMS, "
            "and run.json."
        )
    if (runs_dir / "run.json").is_file():
        return [runs_dir]
    found: list[Path] = []
    for run_json in sorted(runs_dir.rglob("run.json")):
        rel_parts = run_json.relative_to(runs_dir).parts
        if "evidence" in rel_parts[:-1]:
            continue
        if run_json.is_file() and not run_json.is_symlink():
            found.append(run_json.parent)
    if not found:
        raise SiteError(
            f"No run folders containing run.json were found in {runs_dir}. "
            "Each run folder must hold report.html, report.json, report.md, report.txt, "
            "SHA256SUMS, and run.json."
        )
    return found


def runs(reports_dir: Path) -> dict[tuple[str, str], list[dict]]:
    """Return `{(robot, variant): [run records, newest first]}` from `runs/*/run.json`."""
    found: dict[tuple[str, str], list[dict]] = {}
    if not reports_dir.is_dir():
        return found
    for path in sorted(reports_dir.glob("*/*/runs/*/run.json")):
        if path.is_symlink() or not path.is_file():
            continue
        robot = path.parts[-5]
        variant = path.parts[-4]
        folder = path.parent.name
        if (
            not SEGMENT.fullmatch(robot)
            or not SEGMENT.fullmatch(variant)
            or not RUN_ID.fullmatch(folder)
            or any(p.is_symlink() for p in list(path.parents)[:4])
        ):
            raise SiteError(f"{path}: unsafe run path in the reports directory")
        record = read_json_object(path)
        record["folder"] = folder
        found.setdefault((robot, variant), []).append(record)
    for records in found.values():
        records.sort(key=lambda r: (r["run"]["created"], r["folder"]), reverse=True)
    return found


def _require_single_robot(robots: set[str]) -> str | None:
    if len(robots) > 1:
        names = ", ".join(sorted(robots))
        raise SiteError(
            f"Expected runs for one robot per site, found multiple robots: {names}. "
            "Each robot repository publishes only its own verification reports."
        )
    return next(iter(robots), None)


def add_run_folder(
    reports_dir: Path,
    source: Path,
    *,
    restored_keys: set[tuple[str, str, str]],
    seen_in_batch: set[tuple[str, str, str]],
    allow_replace_run_id: str | None = None,
    git_dir: Path | None = None,
) -> tuple[Path, tuple[str, str, str], dict]:
    report_data = check_reports(source)
    inputs = grading_inputs(source, report_data)
    raw_record = read_json_object(source / "run.json")
    robot, variant, run_id, record = _normalize_run_record(
        raw_record, report_data, source / "run.json", git_dir=git_dir
    )
    key = (robot, variant, run_id)
    if key in seen_in_batch:
        raise SiteError(
            f"Duplicate run folder for {robot}/{variant} run {run_id!r} in input directory."
        )
    seen_in_batch.add(key)

    folder = reports_dir / robot / variant / "runs" / run_id
    if key in restored_keys and run_id != allow_replace_run_id:
        raise SiteError(
            f"Run {robot}/{variant}/runs/{run_id} is already published in history and is immutable."
        )
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)

    for name in (*REPORT_FILES, "SHA256SUMS"):
        shutil.copyfile(source / name, folder / name)
    for item in inputs:
        destination = folder.joinpath(*PurePosixPath(item["path"]).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(item["bytes"])

    if inputs:
        record["grading_inputs"] = [
            {k: item[k] for k in ("role", "path", "sha256")} for item in inputs
        ]
    preserved = _copy_run_evidence(source, folder, raw_record.get("evidence"))
    if preserved is not None:
        record["evidence"] = preserved

    (folder / "run.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return folder, key, record


# ---------------------------------------------------------------- pruning & run page publishing


def validate_retention_days(days: int) -> int:
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise SiteError(f"evidence-retention-days must be a positive integer, got {days!r}")
    return days


def evidence_expired(created: str, now: str, days: int) -> bool:
    return _stamp(now) - _stamp(created) >= timedelta(days=days)


def prune_expired_evidence(reports_dir: Path, now: str, days: int) -> tuple[list[str], set[Path]]:
    """Remove copied MCAP, video, and media under runs older than `days`.

    Returns `(removed_relative_paths, affected_run_folders)`. Report pages,
    `run.json`, `thresholds.json`, and `SHA256SUMS` remain.
    """
    validate_retention_days(days)
    removed: list[str] = []
    affected: set[Path] = set()
    if not now or not reports_dir.is_dir():
        return removed, affected
    for record_path in sorted(reports_dir.glob("*/*/runs/*/run.json")):
        if record_path.is_symlink() or not record_path.is_file():
            continue
        record = read_json_object(record_path)
        created = (record.get("run") or {}).get("created")
        if not isinstance(created, str) or not evidence_expired(created, now, days):
            continue
        run_folder = record_path.parent
        evidence = run_folder / "evidence"
        if evidence.is_dir():
            for path in sorted(evidence.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                if (
                    path.suffix.lower() in MEDIA_SUFFIXES
                    or path.name == "media.json"
                    or path.suffix.lower() == ".html"
                ):
                    path.unlink()
                    removed.append(path.relative_to(reports_dir).as_posix())
                    affected.add(run_folder)
            for subdir in sorted(evidence.rglob("*"), reverse=True):
                if subdir.is_dir() and not any(subdir.iterdir()):
                    subdir.rmdir()
            if evidence.exists() and not any(evidence.iterdir()):
                evidence.rmdir()
        index_html = run_folder / "index.html"
        if index_html.is_file():
            text = index_html.read_text(encoding="utf-8", errors="replace")
            if KEPT_FOR_COPY.search(text) or 'href="evidence/' in text or "href='evidence/" in text:
                affected.add(run_folder)
    return removed, affected


def site_path(value: object) -> str | None:
    if not isinstance(value, str) or not SITE_PATH.fullmatch(value):
        return None
    if (
        "://" in value
        or value.startswith(("/", "\\"))
        or ".." in value.split("/")
        or ":" in value
        or "/actions/" in value.lower()
        or ARTIFACT_WORD.search(value)
    ):
        return None
    return value


def _plain_label(value: object) -> str:
    text = value if isinstance(value, str) else ""
    cleaned = ARTIFACT_WORD.sub("", text).strip(" .:-")
    if not cleaned or re.search(r"[\[\]()|`<>\n]", cleaned) or len(cleaned) > 80:
        return "evidence"
    return cleaned


def listing_path(record: dict) -> str | None:
    name = (record.get("artifact") or {}).get("name") or _stored_text(record.get("name"))
    if not (record.get("preserved") and len(record.get("files") or []) > 1 and name):
        return None
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "-", str(name))
    return f"evidence/{safe_name}.html"


def link_path(record: dict, *, expired: bool) -> str | None:
    if expired or not record.get("preserved"):
        return None
    listing = listing_path(record)
    if listing and site_path(listing):
        return listing
    paths = [
        site_path(item.get("path")) for item in record.get("files") or [] if isinstance(item, dict)
    ]
    kept = [path for path in paths if path]
    if len(kept) == 1:
        return kept[0]
    return None


def evidence_links_document(records: list[dict] | None, *, expired: bool) -> dict:
    links = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        kind = record.get("kind")
        if kind not in {"video", "mcap", "log", "note"}:
            continue
        entry: dict = {"label": _plain_label(record.get("label")), "kind": kind}
        if type(record.get("seed")) is int and record.get("status") in {"PASS", "FAIL"}:
            entry["seed"] = record["seed"]
            entry["status"] = record["status"]
        if expired or not record.get("preserved") or not record.get("files"):
            if record.get("preserved"):
                entry["expired"] = True
                links.append(entry)
            continue
        target = link_path(record, expired=False)
        if target is None:
            for item in record.get("files") or []:
                path = site_path(item.get("path")) if isinstance(item, dict) else None
                if path:
                    links.append(entry | {"path": path, "expired": False})
            continue
        entry["path"] = target
        entry["expired"] = False
        links.append(entry)
    document = {"links": links}
    blob = json.dumps(document)
    if "run" in document or "://" in blob or "/actions/" in blob or ARTIFACT_WORD.search(blob):
        raise SiteError("refusing to pass a non-site evidence link to the reporter")
    return document


LISTING_STYLE = (
    "body{font-family:system-ui,sans-serif;margin:2rem auto;max-width:72rem;padding:0 1rem;"
    "color:#1b1f24}table{border-collapse:collapse}td,th{border:1px solid #d0d7de;"
    "padding:.35rem .6rem;text-align:left}code{font-size:.9em}"
)


def _simple_page(title: str, body: str, up: str = "") -> str:
    nav = (
        f'<p><a href="{html.escape(up, quote=True)}">← All verification reports</a></p>'
        if up
        else ""
    )
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{LISTING_STYLE}</style></head>"
        f"<body>{nav}<h1>{html.escape(title)}</h1>\n{body}\n</body></html>\n"
    )


def a(url: str, text: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(text)}</a>'


def write_listing(folder: Path, record: dict, up: str) -> None:
    path = listing_path(record)
    if path is None or not site_path(path):
        return
    prefix = path.removesuffix(".html").removeprefix("evidence/") + "/"
    rows = "".join(
        "<tr><td>"
        + a(
            item["path"].removeprefix("evidence/"),
            item["path"].removeprefix(f"evidence/{prefix}"),
        )
        + f"</td><td>{item['bytes']}</td><td><code>{html.escape(item['sha256'])}</code></td></tr>"
        for item in record["files"]
    )
    title = _plain_label(record.get("label"))
    body = (
        f"<p>All {len(record['files'])} files copied to this site. "
        f"{a('../index.html', 'Back to the report')}</p>"
        f"<table><tr><th>File</th><th>Bytes</th><th>sha256</th></tr>{rows}</table>"
    )
    out = folder / path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_simple_page(title, body, up=f"../{up}index.html"), encoding="utf-8")


def drop_listing_pages(folder: Path) -> None:
    evidence = folder / "evidence"
    if not evidence.is_dir():
        return
    for path in evidence.glob("*.html"):
        path.unlink()


def trial_model(node: object) -> bool:
    if isinstance(node, dict):
        original = node.get("original_result")
        if isinstance(original, dict) and "index" in original:
            return True
        plan = node.get("plan")
        if isinstance(plan, dict) and isinstance(plan.get("trials"), dict):
            return True
        return any(trial_model(value) for value in node.values())
    if isinstance(node, list):
        return any(trial_model(value) for value in node)
    return False


def display_label(label: str, trial: bool) -> str:
    if trial:
        return SEED_LABEL.sub("trial ", label, count=1)
    return label


def html_is_dirty(text: str) -> bool:
    return ARTIFACT_WORD.search(text) is not None or "/actions/" in text.lower()


def _is_actions_url(value: object) -> bool:
    return isinstance(value, str) and (
        "/actions/" in value.lower() or ARTIFACT_WORD.search(value) is not None
    )


def strip_actions_links(report: dict) -> bool:
    links = report.get("evidence_links")
    if not isinstance(links, dict):
        return False
    changed = False
    if _is_actions_url(links.get("run")):
        links.pop("run", None)
        changed = True
    entries = links.get("links")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and _is_actions_url(entry.get("url")):
                entry.pop("url", None)
                changed = True
    return changed


def publish_evidence_links(report: dict, links: dict) -> bool:
    stored = report.get("evidence_links")
    if not isinstance(stored, dict) and not links.get("links"):
        return False
    kept = []
    if isinstance(stored, dict):
        kept = [
            entry
            for entry in stored.get("links") or []
            if isinstance(entry, dict)
            and isinstance(entry.get("url"), str)
            and entry["url"].startswith("https://")
            and not _is_actions_url(entry["url"])
        ]
    public = {"links": [dict(entry) for entry in links.get("links", [])] + kept}
    if public == stored:
        return False
    report["evidence_links"] = public
    return True


def strip_actions_text(text: str) -> str:
    text = ACTIONS_ANCHOR.sub(lambda match: match.group(3), text)
    text = ACTIONS_URL.sub("", text)
    return ARTIFACT_WORD.sub("", text)


def reporter_html(folder: Path) -> str:
    parked = folder / ORIGINAL_HTML
    if parked.is_file():
        text = parked.read_text(encoding="utf-8", errors="replace")
        parked.unlink()
        return text
    report = folder / "report.html"
    if report.is_file():
        return report.read_text(encoding="utf-8", errors="replace")
    return ""


def write_published_checksums(folder: Path) -> None:
    names = sorted(
        path.relative_to(folder).as_posix()
        for path in folder.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    if any(name == ORIGINAL_HTML or name.endswith("/" + ORIGINAL_HTML) for name in names):
        raise SiteError(f"{folder}: {ORIGINAL_HTML} is not a published file")
    lines = [f"{_sha256(folder / name)}  {name}" for name in names]
    (folder / "SHA256SUMS").write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


def publish_report_files(
    folder: Path,
    html_text: str,
    *,
    expired: bool = False,
    days: int = DEFAULT_RETENTION_DAYS,
) -> None:
    parked = folder / ORIGINAL_HTML
    if parked.is_file():
        parked.unlink()
    report_path = folder / "report.json"
    if report_path.is_file():
        report = read_report(report_path)
        if isinstance(report, dict) and strip_actions_links(report):
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if html_text:
        cleaned_html = strip_actions_text(html_text)
        if expired:
            cleaned_html = scrub_expired_evidence_html(cleaned_html, days)
        (folder / "report.html").write_text(cleaned_html, encoding="utf-8")
    for name in ("report.md", "report.txt"):
        path = folder / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        cleaned = strip_actions_text(text)
        if cleaned != text:
            path.write_text(cleaned, encoding="utf-8")
    write_published_checksums(folder)


def scrub_expired_evidence_html(text: str, days: int) -> str:
    """Remove local `evidence/...` links and media tags once retention has pruned copies."""
    expired_note = f"Evidence expired after {days} days"
    text = EVIDENCE_VIDEO.sub(f'<p class="evidence-expired">{expired_note}</p>', text)
    text = EVIDENCE_IMG.sub("", text)

    def unwrap_evidence_anchor(match: re.Match) -> str:
        inner = re.sub(r"<[^>]+>", "", match.group(3)).strip()
        if inner:
            return f"{html.escape(inner)} ({expired_note})"
        return expired_note

    text = EVIDENCE_ANCHOR.sub(unwrap_evidence_anchor, text)
    text = KEPT_FOR_COPY.sub(expired_note, text)
    text = re.sub(r"\bis-kept\b", "is-expired", text)
    return text


def scrub_reporter_html(text: str, record: dict, *, trial: bool, expired: bool, days: int) -> str:
    by_id = {
        str(entry["artifact"]["id"]): entry
        for entry in record.get("evidence") or []
        if isinstance(entry, dict)
        and isinstance(entry.get("artifact"), dict)
        and "id" in entry["artifact"]
    }

    def shown(label: str) -> str:
        cleaned = ARTIFACT_WORD.sub("", label).strip()
        return cleaned or "evidence"

    def replace(match: re.Match) -> str:
        label = shown(display_label(match.group(4), trial))
        entry = by_id.get(match.group(3))
        target = None if entry is None else link_path(entry, expired=expired)
        if target:
            return f"<a href='{html.escape(target, quote=True)}'>{html.escape(label)}</a>"
        if expired and entry is not None and entry.get("preserved"):
            return f"{html.escape(label)} (Evidence expired after {days} days)"
        return html.escape(label)

    def unwrap(match: re.Match) -> str:
        return html.escape(shown(match.group(3)))

    text = ARTIFACT_LINK.sub(replace, text)
    text = ACTIONS_ANCHOR.sub(unwrap, text)
    text = ACTIONS_URL.sub("", text)
    text = ARTIFACT_WORD.sub("", text)
    if expired:
        text = scrub_expired_evidence_html(text, days)
    return text


# ---------------------------------------------------------------- provenance & nav injection

_REPO_PATH = re.compile(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+")


def _spec_repository_url(spec: dict) -> str | None:
    repository = _stored_text(spec.get("repository"))
    if repository is None:
        return None
    repository = repository.rstrip("/").removesuffix(".git")
    if repository.startswith("https://"):
        try:
            parsed = urllib.parse.urlsplit(repository)
        except ValueError:
            return None
        if not re.fullmatch(r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*", parsed.netloc):
            return None
        if parsed.query or parsed.fragment:
            return None
        path = parsed.path.removeprefix("/")
        url = repository
    else:
        path = repository
        url = "https://github.com/" + repository
    if not _REPO_PATH.fullmatch(path) or path.rsplit("/", 1)[-1] in {".", ".."}:
        return None
    return url


def _spec_repository_label(spec: dict) -> str | None:
    url = _spec_repository_url(spec)
    if url:
        return urllib.parse.urlsplit(url).path.removeprefix("/")
    return _stored_text(spec.get("repository"))


def _spec_assessments(report: object) -> list[dict]:
    summaries = report.get("summaries", []) if isinstance(report, dict) else []
    assessed: list[dict] = []
    for summary in summaries if isinstance(summaries, list) else []:
        spec = summary.get("spec") if isinstance(summary, dict) else None
        if isinstance(spec, dict) and spec and spec not in assessed:
            assessed.append(spec)
    return assessed


def _spec_core(spec: object) -> tuple | None:
    if not isinstance(spec, dict) or not spec:
        return None
    repo_label = _spec_repository_label(spec)
    tag = _stored_text(spec.get("tag"))
    commit = _stored_text(spec.get("commit"))
    if repo_label is None and tag is None and commit is None:
        return None
    return (repo_label, tag, commit)


def _spec_line_html(spec: object, *, assessed: bool) -> str:
    core = _spec_core(spec)
    if core is None or not isinstance(spec, dict):
        return "Not recorded"
    _, tag, commit = core
    repository_url = _spec_repository_url(spec)
    pieces = []
    if tag is not None:
        label = html.escape(tag)
        if repository_url:
            href = f"{repository_url}/releases/tag/{urllib.parse.quote(tag, safe='')}"
            title = f"Release {tag}" + (f", commit {commit}" if commit is not None else "")
            pieces.append(
                f'<a href="{html.escape(href, quote=True)}" '
                f'title="{html.escape(title, quote=True)}">{label}</a>'
            )
        else:
            pieces.append(label)
    elif commit is not None:
        title = html.escape(f"Commit {commit}", quote=True)
        if repository_url and COMMIT_SHA.fullmatch(commit):
            pieces.append(
                f'<a href="{html.escape(repository_url, quote=True)}/commit/{commit}" '
                f'title="{title}" aria-label="{title}">commit</a>'
            )
        else:
            pieces.append(f'<span title="{title}">commit</span>')
    repository_label = _spec_repository_label(spec)
    if repository_label is not None:
        pieces.append(html.escape(repository_label))
    if assessed:
        cap = _stored_text(spec.get("capability"))
        if cap:
            pieces.append(html.escape(cap))
        rev = _stored_text(spec.get("revision"))
        if rev:
            pieces.append(html.escape("revision " + rev))
    return " · ".join(pieces)


def _spec_summary_html(generation: object, assessed: list[dict]) -> str:
    spec = assessed[0] if assessed else generation
    core = _spec_core(spec)
    if core is None or not isinstance(spec, dict):
        return "<strong>Spec</strong> not recorded"
    _, tag, _commit = core
    pieces = [html.escape(tag or "commit")]
    cap = _stored_text(spec.get("capability"))
    if cap:
        pieces.append(html.escape(cap))
    rev = _stored_text(spec.get("revision"))
    if rev:
        pieces.append(html.escape("revision " + rev))
    if len(assessed) > 1:
        pieces.append(f"+{len(assessed) - 1} more")
    return "<strong>Spec</strong> " + " · ".join(pieces)


_PROVENANCE_SUMMARY_STYLE = (
    "cursor:pointer;margin:0;font-size:inherit;font-weight:400;"
    "text-transform:none;letter-spacing:normal;color:inherit"
)
_PROVENANCE_SUMMARY_CSS = (
    "details.spec-provenance>summary::after{"
    'content:"Show";float:none;margin-left:.6rem;text-transform:none;'
    "letter-spacing:normal;font-weight:600;color:var(--link,#0b5cad)}"
    'details.spec-provenance[open]>summary::after{content:"Hide"}'
)


def spec_provenance_html(record: dict, report: object) -> str:
    generation = (record.get("releases") or {}).get("specs")
    assessed = _spec_assessments(report)
    if len(assessed) == 1 and _spec_core(generation) == _spec_core(assessed[0]) is not None:
        lines = [("Generation and assessment", _spec_line_html(assessed[0], assessed=True))]
    elif not assessed and _spec_core(generation) is None:
        lines = [("Generation and assessment", "Not recorded")]
    else:
        lines = [("Generation", _spec_line_html(generation, assessed=False))]
        lines += [("Assessment", _spec_line_html(spec, assessed=True)) for spec in assessed] or [
            ("Assessment", "Not recorded")
        ]
    rows = "".join(
        f'<dt style="font-weight:600">{label}</dt><dd style="margin:0 0 .5rem">{line}</dd>'
        for label, line in lines
    )
    return (
        '<details class="spec-provenance" id="spec-provenance" '
        'style="margin:1.5rem 0 0;font-size:.875rem;overflow-wrap:anywhere">'
        f'<summary style="{_PROVENANCE_SUMMARY_STYLE}">'
        f"{_spec_summary_html(generation, assessed)}</summary>"
        f"<style>{_PROVENANCE_SUMMARY_CSS}</style>"
        f'<dl style="margin:.5rem 0 0">{rows}</dl>'
        '<p style="margin:0;font-size:.8rem;opacity:.75">'
        "Recorded at run time; not reassessed.</p></details>"
    )


def with_spec_provenance(public: str, record: dict, report: object) -> str:
    if 'id="spec-provenance"' in public:
        return public
    section = spec_provenance_html(record, report)
    for closing in (r"</main\s*>", r"</body\s*>"):
        found = list(re.finditer(closing, public, re.I))
        if found:
            return public[: found[-1].start()] + section + public[found[-1].start() :]
    return public + section


SITE_NAV_STYLES = """.site-nav {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.35rem 0.6rem;
  margin: 0 0 0.85rem;
  font-size: 0.86rem;
}
.site-nav a {
  color: var(--teal-bright, #64dfc7);
  font-weight: 500;
  text-decoration: none;
}
.site-nav a:hover {
  text-decoration: underline;
}
.site-nav a[aria-current="page"] {
  color: var(--header-text, #f8fafc);
  font-weight: 650;
}
.site-nav-sep {
  color: var(--header-muted, #b7c5d6);
  opacity: 0.72;
}
"""


def _site_nav_html(overview_href: str, reports_href: str, *, on_reports_index: bool = False) -> str:
    reports_attr = ' aria-current="page"' if on_reports_index else ""
    return (
        '<nav class="site-nav" aria-label="Site">'
        f'<a href="{html.escape(DOCS_HOME_URL, quote=True)}">Documentation home</a>'
        '<span class="site-nav-sep" aria-hidden="true">/</span>'
        f'<a href="{html.escape(overview_href, quote=True)}">Overview</a>'
        '<span class="site-nav-sep" aria-hidden="true">/</span>'
        f'<a href="{html.escape(reports_href, quote=True)}"{reports_attr}>Reports</a>'
        "</nav>"
    )


def with_site_nav(public: str, *, overview_href: str, reports_href: str) -> str:
    nav = _site_nav_html(overview_href, reports_href)
    if '<nav class="site-nav"' in public:
        return public
    style_tag = f"<style>{SITE_NAV_STYLES}</style>"
    if "</head>" in public and ".site-nav" not in public:
        public = public.replace("</head>", f"{style_tag}</head>", 1)
    masthead_inner = re.search(
        r"<div\s+class=[\"'][^\"']*\bmasthead-inner\b[^\"']*[\"'][^>]*>", public, re.I
    )
    if masthead_inner:
        pos = masthead_inner.end()
        return public[:pos] + nav + public[pos:]
    body_open = re.search(r"<body[^>]*>", public, re.I)
    if body_open:
        pos = body_open.end()
        header = f'<header class="masthead"><div class="wrap masthead-inner">{nav}</div></header>'
        return public[:pos] + header + public[pos:]
    return nav + public


def publish_run_page(folder: Path, record: dict, *, now: str, days: int, trial: bool) -> None:
    html_text = reporter_html(folder)
    created = (record.get("run") or {}).get("created", "")
    expired = bool(now) and bool(created) and evidence_expired(created, now, days)
    drop_listing_pages(folder)
    if not expired:
        for entry in record.get("evidence") or []:
            if isinstance(entry, dict):
                write_listing(folder, entry, up="../../../../")
    links = evidence_links_document(record.get("evidence"), expired=expired)
    report_path = folder / "report.json"
    report = read_report(report_path)
    if isinstance(report, dict) and (
        strip_actions_links(report) | publish_evidence_links(report, links)
    ):
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    # The run's report.html was rendered by report.yml's reporter; republish it scrubbed.
    # The old multi-robot site re-rendered with whatever reporter binary was on PATH,
    # which made output depend on the runner; that path is intentionally gone.
    public = scrub_reporter_html(html_text, record, trial=trial, expired=expired, days=days)
    public = with_site_nav(
        public,
        overview_href="../../../../../index.html",
        reports_href="../../../../index.html",
    )
    public = with_spec_provenance(public, record, report)
    if html_is_dirty(public):
        raise SiteError(
            f"{folder / 'index.html'}: public page still names an artifact or links to /actions/"
        )
    (folder / "index.html").write_text(public, encoding="utf-8")
    publish_report_files(folder, html_text, expired=expired, days=days)


def redirect_page(target: str, title: str = "Latest report") -> str:
    safe = html.escape(target, quote=True)
    shown = html.escape(title)
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0; url={safe}">'
        f'<link rel="canonical" href="{safe}">'
        f"<title>{shown}</title></head><body>"
        f'<p><a href="{safe}">{shown}</a></p>'
        "</body></html>\n"
    )


_TEXT_SUFFIXES = {
    ".html",
    ".json",
    ".txt",
    ".md",
    ".svg",
    ".orig",
    ".xml",
    ".css",
    ".js",
    ".yml",
    ".yaml",
}


def published_text(path: Path) -> bool:
    if not path.is_file() or path.name == "run.json":
        return False
    if path.suffix.lower() in _TEXT_SUFFIXES or path.name.endswith(".orig"):
        return True
    return path.name in {"SHA256SUMS"}


def public_page_problems(reports_dir: Path) -> list[str]:
    problems = []
    if not reports_dir.is_dir():
        return problems
    for path in sorted(reports_dir.rglob("*")):
        if not published_text(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(reports_dir).as_posix()
        if ARTIFACT_WORD.search(text):
            problems.append(f"{relative} mentions an artifact")
        if "/actions/" in text.lower():
            problems.append(f"{relative} links to /actions/")
    return problems


def remember_stored_wording(folder: Path, record: dict) -> bool:
    outcomes = record.get("outcome")
    report_path = folder / "report.json"
    if not isinstance(outcomes, list) or not report_path.is_file():
        return False
    report = read_report(report_path)
    summaries = report.get("summaries") if isinstance(report, dict) else None
    if not isinstance(summaries, list) or len(summaries) != len(outcomes):
        return False
    changed = False
    for item, summary in zip(outcomes, summaries):
        if not isinstance(item, dict) or not isinstance(summary, dict):
            continue
        if _stored_text(item.get("statement")) or _stored_text(item.get("finding")):
            continue
        if item.get("passed") is not None and item.get("trials") is not None:
            continue
        statement = reproducibility_statement(summary)
        if statement is not None:
            item["statement"] = statement
            changed = True
            continue
        finding = reproducibility_finding(summary)
        if finding is not None:
            item["finding"] = finding
            changed = True
    if not changed:
        return False
    stored = read_json_object(folder / "run.json")
    stored["outcome"] = outcomes
    (folder / "run.json").write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
    return True


# ---------------------------------------------------------------- HTML styles, JS & pages

COUNTS_NOT_STORED = "counts not stored"


def grade_label(grade: str) -> str:
    if grade == "UNSUPPORTED":
        return "not supported"
    return grade


def outcome_detail(item: dict) -> str:
    if item.get("grade") == "UNSUPPORTED":
        return "no trials run"
    statement = _stored_text(item.get("statement"))
    if statement is not None:
        return html.escape(statement)
    passed, trials = item.get("passed"), item.get("trials")
    if passed is not None and trials is not None:
        return f"{passed}/{trials} passed ({item['required']} needed)"
    finding = _stored_text(item.get("finding"))
    if finding is not None:
        return html.escape(finding)
    return COUNTS_NOT_STORED


def outcome_cells(record: dict) -> str:
    return "<br>".join(
        f'<span class="{html.escape(o["grade"])}">{html.escape(grade_label(o["grade"]))}</span> '
        f"{outcome_detail(o)} · {html.escape(o['capability'])}"
        for o in record["outcome"]
    )


SITE_STYLES = """:root {
  color-scheme: light;
  --font-sans: "DM Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --navy: #01152d;
  --teal: #0e8785;
  --teal-bright: #64dfc7;
  --track: #0b4f4d;
  --text: #0f172a;
  --text-muted: #475569;
  --bg: #f4f7fb;
  --bg-muted: #e8eef5;
  --surface: #ffffff;
  --border: #d5dee9;
  --border-strong: #c3cedb;
  --shadow: 0 12px 32px rgba(1, 21, 45, 0.06);
  --chip-pass-fg: #0f5f5c;
  --chip-pass-bg: #d7f3ef;
  --chip-fail-fg: #9f1239;
  --chip-fail-bg: #ffe4e6;
  --chip-warn-fg: #92400e;
  --chip-warn-bg: #fef3c7;
  --chip-muted-fg: #334155;
  --chip-muted-bg: #e2e8f0;
  --header-text: #f8fafc;
  --header-muted: #b7c5d6;
  --focus: #0e8785;
  --pre-bg: #0b1120;
  --pre-text: #e2e8f0;
  --link: #0b6e6c;
  --link-hover: #08524f;
  --speak: #8a6a32;
  --speak-text: #6b5428;
  --pip-pass: #0e8785;
  --pip-fail: #e11d48;
  --pip-warn: #b45309;
  --pip-muted: #64748b;
  --pip-size: 0.7rem;
}

@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --text: #e2e8f0;
    --text-muted: #94a3b8;
    --bg: #0b1120;
    --bg-muted: #111827;
    --surface: #151f32;
    --border: #2a364a;
    --border-strong: #3d4b63;
    --shadow: 0 16px 40px rgba(0, 0, 0, 0.35);
    --chip-pass-fg: #5eead4;
    --chip-pass-bg: #0f2e2b;
    --chip-fail-fg: #fda4af;
    --chip-fail-bg: #4c0519;
    --chip-warn-fg: #fcd34d;
    --chip-warn-bg: #33260a;
    --chip-muted-fg: #cbd5e1;
    --chip-muted-bg: #1e293b;
    --link: #64dfc7;
    --link-hover: #8ef3e4;
    --speak: #c4a46a;
    --speak-text: #e0c48a;
    --pip-pass: #14b8a6;
    --pip-fail: #fb7185;
    --pip-warn: #fbbf24;
    --pip-muted: #94a3b8;
  }
}

html:has(#theme-light:checked) {
  color-scheme: light;
  --text: #0f172a;
  --text-muted: #475569;
  --bg: #f4f7fb;
  --bg-muted: #e8eef5;
  --surface: #ffffff;
  --border: #d5dee9;
  --border-strong: #c3cedb;
  --shadow: 0 12px 32px rgba(1, 21, 45, 0.06);
  --chip-pass-fg: #0f5f5c;
  --chip-pass-bg: #d7f3ef;
  --chip-fail-fg: #9f1239;
  --chip-fail-bg: #ffe4e6;
  --chip-warn-fg: #92400e;
  --chip-warn-bg: #fef3c7;
  --chip-muted-fg: #334155;
  --chip-muted-bg: #e2e8f0;
  --link: #0b6e6c;
  --link-hover: #08524f;
  --speak: #8a6a32;
  --speak-text: #6b5428;
  --pip-pass: #0e8785;
  --pip-fail: #e11d48;
  --pip-warn: #b45309;
  --pip-muted: #64748b;
}

html:has(#theme-dark:checked) {
  color-scheme: dark;
  --text: #e2e8f0;
  --text-muted: #94a3b8;
  --bg: #0b1120;
  --bg-muted: #111827;
  --surface: #151f32;
  --border: #2a364a;
  --border-strong: #3d4b63;
  --shadow: 0 16px 40px rgba(0, 0, 0, 0.35);
  --chip-pass-fg: #5eead4;
  --chip-pass-bg: #0f2e2b;
  --chip-fail-fg: #fda4af;
  --chip-fail-bg: #4c0519;
  --chip-warn-fg: #fcd34d;
  --chip-warn-bg: #33260a;
  --chip-muted-fg: #cbd5e1;
  --chip-muted-bg: #1e293b;
  --link: #64dfc7;
  --link-hover: #8ef3e4;
  --speak: #c4a46a;
  --speak-text: #e0c48a;
  --pip-pass: #14b8a6;
  --pip-fail: #fb7185;
  --pip-warn: #fbbf24;
  --pip-muted: #94a3b8;
}

*, *::before, *::after { box-sizing: border-box; }
html, body { margin: 0; }
body {
  font-family: var(--font-sans);
  font-size: 1.0625rem;
  line-height: 1.55;
  color: var(--text);
  background: var(--bg);
  -webkit-font-smoothing: antialiased;
  font-variant-numeric: tabular-nums;
}
a {
  color: var(--link);
  text-decoration-thickness: 1px;
  text-underline-offset: 0.15em;
}
a:hover { color: var(--link-hover); }
a:focus-visible, button:focus-visible, summary:focus-visible,
input:focus-visible, .row:focus-visible {
  outline: 3px solid var(--focus);
  outline-offset: 3px;
}
.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}
.skip {
  position: absolute;
  left: -999px;
  top: 0;
}
.skip:focus {
  left: 1rem;
  top: 1rem;
  z-index: 10;
  background: var(--surface);
  padding: 0.4rem 0.7rem;
  border-radius: 0.4rem;
}
.masthead {
  background:
    radial-gradient(ellipse 70% 80% at 88% 0%, rgba(14, 135, 133, 0.28), transparent 58%),
    var(--navy);
  color: var(--header-text);
  border-bottom: 3px solid var(--teal);
}
.masthead-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 0.5rem 1rem;
  margin-bottom: 0.65rem;
}
.masthead-top .eyebrow,
.masthead-top .crumb {
  margin: 0;
}
.theme {
  position: relative;
  display: flex;
  gap: 0.15rem;
  margin: 0;
  min-inline-size: 0;
  padding: 0.15rem;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.04);
}
.theme legend {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}
.theme label {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 1.7rem;
  padding: 0 0.7rem;
  border: 2px solid transparent;
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--header-muted);
  cursor: pointer;
}
.theme input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}
.theme label:has(input:checked) {
  background: rgba(14, 135, 133, 0.35);
  color: #fff;
  border-color: var(--header-text);
}
.theme label:hover { color: #fff; }
.theme label:focus-within {
  outline: 3px solid var(--teal-bright);
  outline-offset: 2px;
}
.wrap {
  width: min(72rem, 100% - 2.25rem);
  margin-inline: auto;
}
.masthead-inner { padding: 1.2rem 0 1.45rem; }
.eyebrow {
  margin: 0 0 0.45rem;
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.72rem;
  font-weight: 650;
  color: var(--teal-bright);
}
.masthead h1 {
  margin: 0 0 0.35rem;
  font-size: 1.7rem;
  letter-spacing: -0.03em;
  font-weight: 650;
  line-height: 1.2;
}
.lede {
  margin: 0;
  max-width: 46rem;
  color: var(--header-muted);
  font-size: 0.98rem;
}
.lede a { color: var(--teal-bright); }
.crumb {
  margin: 0 0 0.7rem;
  font-size: 0.9rem;
}
.crumb a { color: var(--teal-bright); }
.meta-line {
  margin: 0.55rem 0 0;
  color: var(--header-muted);
  font-size: 0.88rem;
}
.meta-line a { color: var(--teal-bright); }
.meta-line .quiet { color: var(--header-muted); }
.page { padding: 1.25rem 0 2.4rem; }
.status, .all-pass {
  margin: 0 0 1rem;
  color: var(--text-muted);
  font-size: 0.95rem;
}
.status strong, .all-pass strong { color: var(--text); font-weight: 650; }
.summary-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem 1rem;
  margin: 0 0 1.15rem;
  padding: 0.65rem 0.9rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.75rem;
}
.scenarios-label {
  margin: 0 0 0.55rem;
  font-size: 0.88rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text);
}
.archived-history {
  margin: 1.75rem 0 0;
  padding-top: 1rem;
  border-top: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 0.9rem;
}
.stack {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}
.card {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.85rem;
  box-shadow: var(--shadow);
  padding: 0.85rem 1rem 0.9rem;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.card:hover, .card:focus-within {
  background: var(--bg-muted);
  border-color: var(--border-strong);
}
.card:has(.card-link:focus-visible) {
  outline: 3px solid var(--focus);
  outline-offset: 3px;
}
.card.is-fail {
  border-left: 3px solid color-mix(in srgb, var(--chip-fail-fg) 70%, #fda4af);
}
.card.is-fail:hover, .card.is-fail:focus-within {
  border-left-color: color-mix(in srgb, var(--chip-fail-fg) 70%, #fda4af);
}
.entry-main {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 0.65rem 0.85rem;
  align-items: start;
}
.entry-body { min-width: 0; }
.entry-side {
  text-align: right;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.35rem;
}
.title {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 650;
  letter-spacing: -0.02em;
  line-height: 1.3;
}
.title a { color: inherit; text-decoration: none; }
.title a:hover { color: var(--link); text-decoration: underline; }
.card-link:focus-visible { outline: none; }
.card-link::after {
  content: "";
  position: absolute;
  inset: 0;
  z-index: 1;
  border-radius: 0.85rem;
}
.history-link {
  position: relative;
  z-index: 2;
  display: inline-flex;
  align-items: center;
  border-radius: 0.45rem;
}
.sub {
  margin: 0.15rem 0 0;
  color: var(--text-muted);
  font-size: 0.9rem;
}
.finding {
  margin: 0.5rem 0 0;
  padding: 0.35rem 0.55rem;
  border-radius: 0.45rem;
  border-left: 3px solid var(--speak);
  background: color-mix(in srgb, var(--speak) 8%, var(--surface));
  color: var(--text);
  font-size: 0.92rem;
  font-weight: 550;
}
.finding-neutral {
  border-left-color: var(--border-strong);
  background: var(--bg-muted);
  color: var(--text-muted);
  font-weight: 450;
}
.badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 3.6rem;
  padding: 0.12rem 0.5rem;
  border-radius: 999px;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  line-height: 1.4;
}
.badge-pass { color: var(--chip-pass-fg); background: var(--chip-pass-bg); }
.badge-fail { color: var(--chip-fail-fg); background: var(--chip-fail-bg); }
.badge-warn { color: var(--chip-warn-fg); background: var(--chip-warn-bg); }
.badge-muted { color: var(--chip-muted-fg); background: var(--chip-muted-bg); }
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  margin-top: 0.4rem;
  justify-content: flex-start;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.12rem 0.55rem;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--speak) 40%, var(--border));
  background: color-mix(in srgb, var(--speak) 12%, var(--surface));
  color: var(--speak-text);
  font-size: 0.78rem;
  font-weight: 650;
  text-decoration: none;
  line-height: 1.4;
}
.chip-spec {
  border-color: var(--border);
  background: var(--bg-muted);
  color: var(--text-muted);
  font-weight: 550;
}
a.chip:hover {
  background: color-mix(in srgb, var(--speak) 20%, var(--surface));
  color: var(--speak-text);
  border-color: color-mix(in srgb, var(--speak) 65%, var(--border));
}
.chip-integrity-error {
  border-color: color-mix(in srgb, var(--chip-fail-fg) 45%, var(--border));
  background: var(--chip-fail-bg);
  color: var(--chip-fail-fg);
}
.chip-integrity-inconclusive {
  border-style: dashed;
  border-color: var(--border-strong);
  background: var(--chip-muted-bg);
  color: var(--chip-muted-fg);
}
a.chip-spec:hover {
  background: var(--bg-muted);
  color: var(--text);
  border-color: var(--border-strong);
}
.when, .release, .poster, .links {
  font-size: 0.88rem;
  color: var(--text-muted);
}
.release {
  font-weight: 650;
  color: var(--text);
  text-decoration: none;
}
a.release.is-fallback {
  font-weight: 550;
  color: var(--link);
  text-decoration: underline;
}
a.release.is-fallback:hover { color: var(--link-hover); }
a.release:hover { color: var(--link); text-decoration: underline; }
.dot { margin: 0 0.35rem; color: var(--text-muted); }
.links a { margin-left: 0.7rem; }
.links a:first-child { margin-left: 0; }
.side-meta { color: var(--text-muted); font-size: 0.88rem; }
.side-meta .release { margin-right: 0; }
.history {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.row {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.85rem;
  padding: 1rem;
  text-decoration: none;
  color: inherit;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.row:hover, .row:focus-within {
  background: color-mix(in srgb, var(--bg-muted) 70%, var(--surface));
  border-color: var(--border-strong);
}
.row.is-fail {
  border-left: 3px solid color-mix(in srgb, var(--chip-fail-fg) 70%, #fda4af);
}
.row.is-pass {
  border-left: 3px solid color-mix(in srgb, var(--teal) 45%, var(--border));
}
.row-top {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.65rem 0.85rem;
  align-items: start;
}
.row-top > div { min-width: 0; }
.row-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.15rem 0;
  margin-top: 0.5rem;
  color: var(--text-muted);
  font-size: 0.88rem;
}
.row-meta a { font-weight: 550; }
.card:hover .card-link { color: var(--link); text-decoration: underline; }
.counts {
  color: var(--text-muted);
  font-size: 0.9rem;
}
.fail-count { color: var(--chip-fail-fg); font-weight: 700; }
.pass-count { color: var(--text-muted); }
.count-sep { margin: 0 0.3rem; color: var(--text-muted); }
.dots {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 0.35rem;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
}
.pip {
  flex: none;
  width: var(--pip-size);
  height: var(--pip-size);
  border-radius: 50%;
  display: block;
}
.pip[hidden], .pip-more[hidden] { display: none; }
.pip-pass { background: var(--pip-pass); }
.pip-fail { background: var(--pip-fail); }
.pip-warn { background: var(--pip-warn); }
.pip-muted { background: var(--pip-muted); }
.pip-more {
  flex: none;
  color: var(--text-muted);
  font-size: 0.78rem;
  font-weight: 650;
  letter-spacing: 0.01em;
  white-space: nowrap;
}
.meta-latest {
  color: var(--text-muted);
  font-size: 0.88rem;
  white-space: nowrap;
}
time { white-space: nowrap; }
@media (max-width: 720px) {
  .entry-main, .row-top {
    grid-template-columns: auto minmax(0, 1fr);
  }
  .entry-side {
    grid-column: 2;
    min-width: 0;
    max-width: 100%;
    text-align: left;
    align-items: flex-start;
  }
  .side-meta { max-width: 100%; }
  .side-meta > .dot, .meta-latest > .dot { display: none; }
  .side-meta time, .meta-latest time { display: block; }
  .meta-latest {
    white-space: normal;
    min-width: 0;
    max-width: 100%;
  }
  .chips, .entry-body .chips { justify-content: flex-start; }
  .links a { margin-left: 0; }
  .history-link {
    min-height: 44px;
    padding: 0 0.75rem;
    border: 1px solid var(--border);
    background: var(--surface);
    font-weight: 650;
    text-decoration: none;
  }
  .history-link:hover {
    border-color: var(--border-strong);
    color: var(--link);
  }
  .masthead h1 { font-size: 1.4rem; }
  .wrap { width: min(72rem, 100% - 1.5rem); }
  .row-meta { font-size: 0.82rem; }
  .row-meta .dot { margin: 0 0.22rem; }
}
@media screen and (max-width: 720px) {
  .masthead:has(.crumb) .masthead-inner { padding: 0.75rem 0 0.85rem; }
  .masthead:has(.crumb) .lede { line-height: 1.4; }
  .masthead:has(.crumb) .meta-line { margin-top: 0.35rem; line-height: 1.4; }
  .history .row { padding: 0.75rem; }
}
@media (prefers-reduced-motion: reduce) {
  .card, .row { transition: none; }
}
@media print {
  :root, html:has(#theme-light:checked), html:has(#theme-dark:checked) {
    color-scheme: light;
    --text: #0f172a;
    --text-muted: #475569;
    --bg: #fff;
    --bg-muted: #f1f5f9;
    --surface: #fff;
    --border: #cbd5e1;
    --border-strong: #c3cedb;
    --shadow: none;
    --chip-pass-fg: #0f5f5c;
    --chip-pass-bg: #d7f3ef;
    --chip-fail-fg: #9f1239;
    --chip-fail-bg: #ffe4e6;
    --chip-warn-fg: #92400e;
    --chip-warn-bg: #fef3c7;
    --chip-muted-fg: #334155;
    --chip-muted-bg: #e2e8f0;
    --link: #0b6e6c;
    --link-hover: #08524f;
    --speak: #8a6a32;
    --speak-text: #6b5428;
    --pip-pass: #0e8785;
    --pip-fail: #e11d48;
    --pip-warn: #b45309;
    --pip-muted: #64748b;
  }
  body { background: #fff; }
  .theme { display: none; }
  .masthead {
    background: none;
    color: var(--text);
    border-bottom: 2px solid var(--border-strong);
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
  .eyebrow, .lede a, .crumb a, .meta-line a { color: var(--link); }
  .lede, .meta-line, .meta-line .quiet { color: var(--text-muted); }
  .card, .row, .summary-bar { box-shadow: none; break-inside: avoid; transition: none; }
  .badge, .pip, .chip, .finding {
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
}
"""

THEME_SWITCH = (
    '<fieldset class="theme"><legend>Colour theme</legend>'
    '<label><input type="radio" name="theme" id="theme-system" value="system" checked> '
    "System</label>"
    '<label><input type="radio" name="theme" id="theme-light" value="light"> Light</label>'
    '<label><input type="radio" name="theme" id="theme-dark" value="dark"> Dark</label>'
    "</fieldset>"
)

INDEX_SCRIPT = """// Truncate the scenario-dot strip to one line and show +n when dots don't fit.
// Format <time datetime> elements in the viewer's timezone while keeping the UTC fallback.
(function () {
  function formatLocalTime(iso) {
    const ms = Date.parse(iso);
    if (!Number.isFinite(ms) || typeof Intl === "undefined" || !Intl.DateTimeFormat) {
      return "";
    }
    try {
      const date = new Date(ms);
      const dateFmt = new Intl.DateTimeFormat("en-GB", {
        day: "numeric",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      });
      const parts = {};
      for (const part of dateFmt.formatToParts(date)) {
        parts[part.type] = part.value;
      }
      if (!parts.day || !parts.month || !parts.year || !parts.hour || !parts.minute) {
        return "";
      }
      let zone = "";
      try {
        const tzFmt = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" });
        const tzPart = tzFmt.formatToParts(date).find((p) => p.type === "timeZoneName");
        if (tzPart && tzPart.value) zone = tzPart.value;
      } catch {
        zone = "";
      }
      if (!zone) {
        const offsetMin = -date.getTimezoneOffset();
        if (offsetMin === 0) {
          zone = "UTC";
        } else {
          const sign = offsetMin >= 0 ? "+" : "-";
          const abs = Math.abs(offsetMin);
          const hh = String(Math.floor(abs / 60)).padStart(2, "0");
          const mm = String(abs % 60).padStart(2, "0");
          zone = mm === "00" ? "UTC" + sign + hh : "UTC" + sign + hh + ":" + mm;
        }
      }
      return (
        parts.day +
        " " +
        parts.month +
        " " +
        parts.year +
        ", " +
        parts.hour +
        ":" +
        parts.minute +
        " " +
        zone
      );
    } catch {
      return "";
    }
  }

  function updateTimeElement(el) {
    if (el.getAttribute("data-utc") === null) {
      el.setAttribute("data-utc", (el.textContent || "").trim());
    }
    const iso = el.getAttribute("datetime") || "";
    const formatted = formatLocalTime(iso);
    if (!formatted) return false;
    if ((el.textContent || "").trim() === formatted && el.getAttribute("title") === formatted) {
      return false;
    }
    el.textContent = formatted;
    el.setAttribute("title", formatted);
    return true;
  }

  function fitDots(strip) {
    const more = strip.querySelector(".pip-more");
    const pips = [...strip.querySelectorAll(".pip")];
    pips.forEach((p) => { p.hidden = false; });
    if (more) {
      more.hidden = true;
      more.textContent = "";
    }
    if (!pips.length || strip.clientWidth === 0) return;
    const fits = () => strip.scrollWidth <= strip.clientWidth + 1;
    if (fits()) return;
    if (!more) return;
    more.hidden = false;
    let hidden = 0;
    for (let i = pips.length - 1; i >= 1; i--) {
      pips[i].hidden = true;
      hidden += 1;
      more.textContent = "+" + hidden;
      if (fits()) return;
    }
  }

  function fitAll() {
    document.querySelectorAll(".dots").forEach(fitDots);
  }

  function updateTimes() {
    let changed = false;
    document.querySelectorAll("time[datetime]").forEach((el) => {
      if (updateTimeElement(el)) changed = true;
    });
    if (changed) fitAll();
  }

  function start() {
    updateTimes();
    fitAll();
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(fitAll);
    }
  }
  window.addEventListener("resize", fitAll);
  window.addEventListener("load", fitAll);
  document.addEventListener("DOMContentLoaded", start);
  if (document.readyState !== "loading") start();
})();
"""

_ROBOT_TITLES = {
    "booster-t1": "Booster T1",
    "unitree-g1": "Unitree G1",
    "unitree-go1": "Unitree Go1",
}


def _robot_title(robot: str) -> str:
    if robot in _ROBOT_TITLES:
        return _ROBOT_TITLES[robot]
    parts = []
    for part in robot.split("-"):
        if re.fullmatch(r"[a-z]+\d+", part) and len(part) <= 3:
            parts.append(part.upper())
        else:
            parts.append(part.capitalize())
    return " ".join(parts)


def _scenario_title(record: dict, variant: str) -> str:
    outcomes = record.get("outcome") or []
    capability = outcomes[0].get("capability", "") if outcomes else ""
    if variant.endswith("-corner-left") or capability == "walk-corner-left":
        return "Walk around the corner (left)"
    if variant.endswith("-corner-right") or capability == "walk-corner-right":
        return "Walk around the corner (right)"
    if capability == "walk-corner" or variant.endswith("-corner"):
        return "Walk around the corner"
    if capability == "walk-hallway" or variant.endswith("-hallway"):
        return "Walk the hallway"
    return (capability or variant).replace("-", " ").capitalize()


def _variant_subtitle(record: dict, variant: str) -> str:
    robot = record.get("robot") if isinstance(record.get("robot"), dict) else {}
    config = _stored_text(robot.get("configuration")) or variant.split("-", 1)[0]
    tokens = variant.split("-")
    sim = ""
    if "mujoco" in tokens:
        sim = "MuJoCo"
    elif "gazebo" in tokens:
        sim = "Gazebo"
    return f"{config} · {sim}" if sim else config


def _record_grade(record: dict) -> str:
    outcomes = record.get("outcome") or []
    grades = [o.get("grade", "") for o in outcomes if isinstance(o, dict)]
    if "FAIL" in grades:
        return "FAIL"
    if "INSUFFICIENT_EVIDENCE" in grades:
        return "INSUFFICIENT_EVIDENCE"
    if grades and all(g == "UNSUPPORTED" for g in grades):
        return "UNSUPPORTED"
    if "PASS" in grades:
        return "PASS"
    return (grades[0] if grades else "") or "UNKNOWN"


def _grade_sort_rank(grade: str) -> int:
    return {
        "FAIL": 0,
        "INSUFFICIENT_EVIDENCE": 1,
        "PASS": 2,
        "UNSUPPORTED": 3,
    }.get(grade, 4)


def _badge_label(grade: str) -> str:
    if grade == "UNSUPPORTED":
        return "Not supported"
    if grade == "INSUFFICIENT_EVIDENCE":
        return "Insufficient evidence"
    return grade


def _badge_html(grade: str) -> str:
    cls = {
        "PASS": "badge-pass",
        "FAIL": "badge-fail",
        "UNSUPPORTED": "badge-muted",
        "INSUFFICIENT_EVIDENCE": "badge-warn",
    }.get(grade, "badge-muted")
    return f'<span class="badge {cls}">{html.escape(_badge_label(grade))}</span>'


def _utc_time_text(created: str) -> str:
    created_dt = _stamp(created)
    return f"{created_dt.day} {created_dt.strftime('%b %Y, %H:%M')} UTC"


def _time_html(created: str) -> str:
    utc_text = _utc_time_text(created)
    return (
        f'<time datetime="{html.escape(created, quote=True)}" '
        f'title="{html.escape(utc_text, quote=True)}">{html.escape(utc_text)}</time>'
    )


def _release_html(record: dict, *, linked: bool = True) -> str:
    commit = record.get("commit") if isinstance(record.get("commit"), dict) else {}
    tag = _stored_text(record.get("release_tag") or record.get("bundle_tag") or commit.get("tag"))
    sha = _stored_text(commit.get("sha")) or ""
    commit_url = _stored_text(commit.get("url")) or ""
    if tag is not None:
        if "/commit/" in commit_url:
            tag_url = commit_url.split("/commit/", 1)[0] + f"/releases/tag/{tag}"
        else:
            tag_url = commit_url
        title = f"Release {tag}"
        if linked and tag_url:
            return (
                f'<a class="release" href="{html.escape(tag_url, quote=True)}" '
                f'title="{html.escape(title, quote=True)}">{html.escape(tag)}</a>'
            )
        return (
            f'<span class="release" title="{html.escape(title, quote=True)}">'
            f"{html.escape(tag)}</span>"
        )
    title = html.escape(
        "No release tag on this commit" + (f": {sha}" if sha else ""),
        quote=True,
    )
    if linked and commit_url:
        return (
            f'<a class="release is-fallback" href="{html.escape(commit_url, quote=True)}" '
            f'title="{title}">commit</a>'
        )
    return f'<span class="release is-fallback" title="{title}">commit</span>'


def _integrity_chip(
    title: str, status: str, prefix: str, linked: bool, fragment: str = EVALUATOR_ANCHOR
) -> str:
    tip = f"{title}: integrity status {status}"
    if status == "INCONCLUSIVE":
        tip += " (not graded)"
    words = "error" if status == "ERROR" else "not graded"
    icon = "\u2715" if status == "ERROR" else "?"
    klass = f"chip chip-integrity chip-integrity-{status.lower()}"
    body = f'<span aria-hidden="true">{icon}</span> {html.escape(title)} {words}'
    if linked:
        target = html.escape(prefix + fragment, quote=True)
        return (
            f'<a class="{klass}" href="{target}" title="{html.escape(tip, quote=True)}">{body}</a>'
        )
    return f'<span class="{klass}" title="{html.escape(tip, quote=True)}">{body}</span>'


def _chips_html(
    record: dict, prefix: str, *, linked: bool = True, extra: str = "", report: object = None
) -> str:
    grade = _record_grade(record)
    evaluators = record.get("failing_evaluators") or []
    items = []
    if grade not in ("PASS", "UNSUPPORTED") and isinstance(evaluators, list):
        for ev in evaluators:
            if not isinstance(ev, dict):
                continue
            title = _stored_text(ev.get("title"))
            href = _stored_text(ev.get("href"))
            if title is None or href is None:
                continue
            if ev.get("not_graded") is True:
                items.append(
                    _integrity_chip(
                        title,
                        "INCONCLUSIVE",
                        prefix,
                        linked,
                        evaluator_fragment(record, ev.get("id"), report),
                    )
                )
            elif linked:
                fragment = evaluator_fragment(record, ev.get("id"), report)
                tip = f"Open the {title} finding in the report"
                items.append(
                    f'<a class="chip" href="{html.escape(prefix + fragment, quote=True)}" '
                    f'title="{html.escape(tip, quote=True)}" '
                    f'aria-label="{html.escape(tip, quote=True)}">{html.escape(title)}</a>'
                )
            else:
                items.append(f'<span class="chip">{html.escape(title)}</span>')
    markers = record.get("integrity_status") or []
    if grade in ("PASS", "UNSUPPORTED") and isinstance(markers, list):
        for marker in markers:
            if not isinstance(marker, dict):
                continue
            title = _stored_text(marker.get("title"))
            status = marker.get("status")
            if title is None or status not in MARKED_INTEGRITY_STATUSES:
                continue
            if _stored_text(marker.get("id")) is None:
                continue
            items.append(
                _integrity_chip(
                    title,
                    status,
                    prefix,
                    linked,
                    evaluator_fragment(record, marker.get("id"), report),
                )
            )
    if extra:
        items.append(extra)
    if not items:
        return ""
    return f'<div class="chips">{"".join(items)}</div>'


def _spec_versions(record: dict, report: object) -> dict:
    generation = _stored_text(((record.get("releases") or {}).get("specs") or {}).get("tag"))
    assessed = _spec_assessments(report)

    def distinct(key: str) -> tuple | None:
        values = sorted({v for spec in assessed if (v := _stored_text(spec.get(key)))})
        return tuple(values) or None

    return {"generation": generation, "tags": distinct("tag"), "revisions": distinct("revision")}


def _spec_change(newer: dict, older: dict) -> str | None:
    def differs(key: str) -> bool:
        return newer[key] is not None and older[key] is not None and newer[key] != older[key]

    def label(value: object) -> str:
        return ", ".join(value) if isinstance(value, tuple) else str(value)

    pieces = []
    if differs("generation"):
        pieces.append(f"{older['generation']} → {newer['generation']}")
    elif differs("tags"):
        pieces.append(f"{label(older['tags'])} → {label(newer['tags'])}")
    if differs("revisions"):
        pieces.append(f"revision {label(older['revisions'])} → {label(newer['revisions'])}")
    return " · ".join(pieces) or None


def _spec_chip_html(change: str | None) -> str:
    if change is None:
        return ""
    return f'<span class="chip chip-spec">Spec changed: {html.escape(change)}</span>'


def _finding_html(record: dict) -> str:
    grade = _record_grade(record)
    if grade in ("PASS", "UNSUPPORTED"):
        return ""
    for ev in record.get("failing_evaluators") or []:
        if isinstance(ev, dict):
            summary = _stored_text(ev.get("summary"))
            if summary is not None:
                klass = "finding finding-neutral" if ev.get("not_graded") is True else "finding"
                return f'<p class="{klass}">{html.escape(summary)}</p>'
    for item in record.get("outcome") or []:
        if isinstance(item, dict):
            finding = _stored_text(item.get("finding"))
            if finding is not None:
                return f'<p class="finding">{html.escape(finding)}</p>'
    return ""


def _styled_document(
    title: str,
    css_href: str,
    skip_id: str,
    skip_label: str,
    masthead: str,
    main_body: str,
    *,
    script_href: str | None = None,
) -> str:
    script = (
        f'<script src="{html.escape(script_href, quote=True)}"></script>\n' if script_href else ""
    )
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n"
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        '<link href="https://fonts.googleapis.com/css2?family=DM+Sans:'
        "ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,650;0,9..40,700;1,9..40,400"
        '&display=swap" rel="stylesheet">\n'
        f'<link rel="stylesheet" href="{html.escape(css_href, quote=True)}">\n'
        "<body>\n"
        f'<a class="skip" href="#{html.escape(skip_id, quote=True)}">'
        f"{html.escape(skip_label)}</a>\n"
        f'<header class="masthead"><div class="wrap masthead-inner">{masthead}</div></header>\n'
        f'<main class="page wrap" id="{html.escape(skip_id, quote=True)}">{main_body}</main>\n'
        f"{script}"
        "</body>\n"
        "</html>\n"
    )


def _history_page_html(
    robot: str,
    variant: str,
    records: list[dict],
    reports: dict | None = None,
) -> str:
    newest = records[0]
    robot_title = _robot_title(robot)
    scenario_title = _scenario_title(newest, variant)
    subtitle = _variant_subtitle(newest, variant)
    count = len(records)
    plural = "s" if count != 1 else ""

    releases_dict = newest.get("releases") if isinstance(newest.get("releases"), dict) else {}
    releases_parts = []
    for name, release in releases_dict.items():
        if not isinstance(release, dict):
            continue
        rel_tag = _stored_text(release.get("tag"))
        rel_url = _stored_text(release.get("url"))
        if rel_tag and rel_url:
            releases_parts.append(f"{html.escape(name)} {a(rel_url, rel_tag)}")
        elif rel_tag:
            releases_parts.append(f"{html.escape(name)} {html.escape(rel_tag)}")
    releases_suffix = (
        f'<span class="quiet"> · {" · ".join(releases_parts)}</span>' if releases_parts else ""
    )

    robot_info = newest.get("robot") if isinstance(newest.get("robot"), dict) else {}
    repo_str = _stored_text(robot_info.get("repository")) or robot
    repo_short = repo_str.rstrip("/").rsplit("/", 1)[-1]
    robot_tag = _stored_text(robot_info.get("tag"))
    robot_commit = _stored_text(robot_info.get("commit")) or ""
    robot_url = _stored_text(robot_info.get("url")) or ""
    robot_label = html.escape(f"{repo_short} {robot_tag or 'commit'}")
    robot_pin_title = (
        ""
        if robot_tag or not robot_commit
        else f' title="{html.escape("Commit " + robot_commit, quote=True)}"'
    )
    if robot_url:
        robot_line = (
            f'<a href="{html.escape(robot_url, quote=True)}"{robot_pin_title}>{robot_label}</a>'
        )
    else:
        robot_line = f"<span{robot_pin_title}>{robot_label}</span>"
    software_version = _stored_text(robot_info.get("software_version"))
    if software_version is not None:
        config = _stored_text(robot_info.get("configuration")) or ""
        sw_label = f"{config} {software_version}".strip()
        robot_line += f'<span class="quiet"> · software {html.escape(sw_label)}</span>'

    masthead = (
        f"{_site_nav_html('../../../index.html', '../../index.html')}"
        '<div class="masthead-top">'
        '<p class="crumb"><a href="../../index.html">← All verification reports</a></p>'
        f"{THEME_SWITCH}"
        "</div>"
        f'<p class="eyebrow">{html.escape(robot_title)} · {html.escape(subtitle)}</p>'
        f"<h1>{html.escape(scenario_title)}</h1>"
        f'<p class="lede">{count} run{plural}, newest first.</p>'
        f'<p class="meta-line">{a("latest/", "Latest report")}{releases_suffix}</p>'
        f'<p class="meta-line">{robot_line}</p>'
    )
    row_cards = []
    for position, record in enumerate(records):
        grade = _record_grade(record)
        row_class = "row is-fail" if grade in ("FAIL", "INSUFFICIENT_EVIDENCE") else "row is-pass"
        run_prefix = f"runs/{record['folder']}/"
        poster = record.get("poster")
        poster_name = (
            _stored_text(poster.get("name")) if isinstance(poster, dict) else None
        ) or "CI"
        below = records[position + 1] if position + 1 < len(records) else None
        change = None
        if below is not None:
            change = _spec_change(
                _spec_versions(record, (reports or {}).get(record["folder"])),
                _spec_versions(below, (reports or {}).get(below["folder"])),
            )
        chips = _chips_html(
            record,
            run_prefix,
            extra=_spec_chip_html(change),
            report=(reports or {}).get(record["folder"]),
        )
        finding = _finding_html(record)
        row_cards.append(
            f'<article class="{row_class}">'
            '<div class="row-top">'
            f"{_badge_html(grade)}"
            "<div>"
            f'<h2 class="title">{html.escape(scenario_title)}</h2>'
            f"{chips}{finding}"
            '<div class="row-meta">'
            f"{_release_html(record)}"
            '<span class="dot">·</span>'
            f"{_time_html(record['run']['created'])}"
            '<span class="dot">·</span>'
            f'<span class="poster">{html.escape(poster_name)}</span>'
            '<span class="dot">·</span>'
            f"{a(run_prefix, 'Report')}"
            "</div>"
            "</div>"
            "</div>"
            "</article>"
        )
    main_body = f'<div class="history">{"".join(row_cards)}</div>'
    return _styled_document(
        f"{robot_title} · {subtitle} · {scenario_title} · run history",
        "../../styles.css",
        "history",
        "Skip to history",
        masthead,
        main_body,
        script_href="../../open.js",
    )


def _index_card_html(robot: str, variant: str, records: list[dict]) -> str:
    newest = records[0]
    grade = _record_grade(newest)
    card_class = "card is-fail" if grade in ("FAIL", "INSUFFICIENT_EVIDENCE") else "card"
    scenario_title = _scenario_title(newest, variant)
    subtitle = _variant_subtitle(newest, variant)
    latest_href = f"{robot}/{variant}/latest/"
    history_href = f"{robot}/{variant}/"
    run_prefix = f"{robot}/{variant}/runs/{newest['folder']}/"
    chips = _chips_html(newest, run_prefix, linked=False)
    finding = _finding_html(newest)
    card_label = f"{scenario_title}, latest report ({_badge_label(grade)})"
    return (
        f'<article class="{card_class}">'
        '<div class="entry-main">'
        f"{_badge_html(grade)}"
        '<div class="entry-body">'
        f'<h3 class="title"><a class="card-link" href="{html.escape(latest_href, quote=True)}" '
        f'aria-label="{html.escape(card_label, quote=True)}">{html.escape(scenario_title)}</a></h3>'
        f'<p class="sub">{html.escape(subtitle)}</p>'
        f"{chips}{finding}"
        "</div>"
        '<div class="entry-side">'
        f'<div class="side-meta">{_release_html(newest, linked=False)}'
        f'<span class="dot">·</span>{_time_html(newest["run"]["created"])}</div>'
        '<div class="links"><a class="history-link" '
        f'href="{html.escape(history_href, quote=True)}">'
        f"History ({len(records)})</a></div>"
        "</div>"
        "</div>"
        "</article>"
    )


def _archived_history_html() -> str:
    return (
        '<p class="archived-history">'
        "Earlier history before per-repository reports is preserved in the "
        f'<a href="{html.escape(ARCHIVED_REPORTS_URL, quote=True)}">'
        "archived verification reports</a>.</p>"
    )


def _index_page_html(history: dict[tuple[str, str], list[dict]]) -> str:
    robots = {robot for robot, _ in history}
    robot = _require_single_robot(robots)
    robot_title = _robot_title(robot) if robot else "Living Proof Labs"

    variants = [(variant, records) for (_, variant), records in sorted(history.items())]
    ordered = sorted(
        variants,
        key=lambda item: (
            _grade_sort_rank(_record_grade(item[1][0])),
            _scenario_title(item[1][0], item[0]).lower(),
            _variant_subtitle(item[1][0], item[0]).lower(),
            item[0],
        ),
    )
    total_count = len(ordered)
    all_latest = [recs[0] for _, recs in ordered]

    if total_count == 0:
        body_html = '<p class="status">No verification reports published yet.</p>'
    else:
        fail_count = sum(1 for r in all_latest if _record_grade(r) == "FAIL")
        insuff_count = sum(1 for r in all_latest if _record_grade(r) == "INSUFFICIENT_EVIDENCE")
        pass_count = sum(1 for r in all_latest if _record_grade(r) == "PASS")
        unsup_count = sum(1 for r in all_latest if _record_grade(r) == "UNSUPPORTED")
        unknown_count = total_count - fail_count - insuff_count - pass_count - unsup_count

        if fail_count:
            noun = "result" if fail_count == 1 else "results"
            status_line = (
                f'<p class="status"><strong>{fail_count} latest {noun} failed.</strong> '
                "Failing scenarios are listed first below.</p>"
            )
        elif all(_record_grade(r) == "PASS" for r in all_latest):
            noun = "result" if total_count == 1 else "results"
            Verb = "passes" if total_count == 1 else "pass"
            status_line = f'<p class="all-pass">All {total_count} latest {noun} {Verb}.</p>'
        else:
            labels = {
                "PASS": "passed",
                "INSUFFICIENT_EVIDENCE": "insufficient evidence",
                "UNSUPPORTED": "not supported",
            }
            tally = {label: 0 for label in (*labels.values(), "unknown")}
            for rec in all_latest:
                tally[labels.get(_record_grade(rec), "unknown")] += 1
            summary = ", ".join(f"{c} {lbl}" for lbl, c in tally.items() if c)
            noun = "result" if total_count == 1 else "results"
            status_line = f'<p class="status">{total_count} latest {noun}: {summary}.</p>'

        scenario_noun = "scenario" if total_count == 1 else "scenarios"
        count_parts = [f"{total_count} {scenario_noun}"]
        if fail_count:
            count_parts.append(f'<span class="fail-count">{fail_count} failed</span>')
        if insuff_count:
            count_parts.append(f'<span class="fail-count">{insuff_count} insufficient</span>')
        if pass_count:
            count_parts.append(f'<span class="pass-count">{pass_count} passed</span>')
        if unsup_count:
            count_parts.append(f'<span class="pass-count">{unsup_count} not supported</span>')
        if unknown_count:
            count_parts.append(f'<span class="counts">{unknown_count} unknown</span>')
        counts_html = '<span class="count-sep">·</span>'.join(count_parts)

        dots = []
        cards = []
        assert robot is not None
        for variant, records in ordered:
            newest = records[0]
            grade = _record_grade(newest)
            scenario_title = _scenario_title(newest, variant)
            pip_cls, pip_status = {
                "FAIL": ("pip-fail", "failed"),
                "PASS": ("pip-pass", "passed"),
                "INSUFFICIENT_EVIDENCE": ("pip-warn", "insufficient evidence"),
                "UNSUPPORTED": ("pip-muted", "not supported"),
            }.get(grade, ("pip-muted", grade.lower()))
            dots.append(
                f'<span class="pip {pip_cls}" '
                f'title="{html.escape(f"{scenario_title}: {pip_status}", quote=True)}"></span>'
            )
            cards.append(_index_card_html(robot, variant, records))

        latest_record = max(
            all_latest,
            key=lambda r: (r["run"]["created"], r.get("folder", "")),
        )
        meta_latest = (
            f"{_release_html(latest_record, linked=False)}"
            '<span class="dot">·</span>'
            f"{_time_html(latest_record['run']['created'])}"
        )
        summary_bar = (
            '<div class="summary-bar">'
            f'<div class="counts">{counts_html}</div>'
            f'<span class="dots">{"".join(dots)}<span class="pip-more" hidden></span></span>'
            f'<span class="meta-latest">{meta_latest}</span>'
            "</div>"
        )
        scenarios_section = (
            '<section aria-labelledby="scenarios">'
            '<h2 class="scenarios-label" id="scenarios">Scenarios</h2>'
            f'<div class="stack">{"".join(cards)}</div>'
            "</section>"
        )
        body_html = status_line + summary_bar + scenarios_section

    masthead = (
        f"{_site_nav_html('../index.html', 'index.html', on_reports_index=True)}"
        '<div class="masthead-top">'
        f'<p class="eyebrow">{html.escape(robot_title)}</p>'
        f"{THEME_SWITCH}"
        "</div>"
        "<h1>Verification reports</h1>"
        '<p class="lede">The latest result for each scenario, with failures first.</p>'
    )
    page_title = f"{robot_title} · verification reports" if robot else "Verification reports"
    return _styled_document(
        page_title,
        "styles.css",
        "results",
        "Skip to results",
        masthead,
        body_html + _archived_history_html(),
        script_href="open.js",
    )


# ---------------------------------------------------------------- render & build


def render(
    reports_dir: Path,
    *,
    now: str = "",
    days: int = DEFAULT_RETENTION_DAYS,
    publish_folders: set[Path] | None = None,
) -> dict[tuple[str, str], list[dict]]:
    validate_retention_days(days)
    _, pruned_folders = prune_expired_evidence(reports_dir, now, days)
    history = runs(reports_dir)
    _require_single_robot({robot for robot, _ in history})

    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "styles.css").write_text(SITE_STYLES + "\n" + SITE_NAV_STYLES, encoding="utf-8")
    (reports_dir / "open.js").write_text(INDEX_SCRIPT, encoding="utf-8")

    needs_publish = set(publish_folders or set()) | pruned_folders
    for (robot, variant), records in sorted(history.items()):
        base = reports_dir / robot / variant
        latest = base / "latest"
        if latest.exists():
            shutil.rmtree(latest)
        loaded_reports = {
            record["folder"]: read_report(base / "runs" / record["folder"] / "report.json")
            for record in records
        }
        trials = {folder: trial_model(rep) for folder, rep in loaded_reports.items()}
        newest = records[0]
        latest.mkdir(parents=True, exist_ok=True)
        (latest / "index.html").write_text(
            redirect_page(f"../runs/{newest['folder']}/"), encoding="utf-8"
        )
        for record in records:
            run_folder = base / "runs" / record["folder"]
            wording_updated = remember_stored_wording(run_folder, record)
            if wording_updated:
                write_published_checksums(run_folder)
            if (run_folder / ORIGINAL_HTML).is_file() or not (run_folder / "index.html").is_file():
                needs_publish.add(run_folder)
            if run_folder in needs_publish:
                publish_run_page(
                    run_folder,
                    record,
                    now=now,
                    days=days,
                    trial=trials[record["folder"]],
                )
        (base / "index.html").write_text(
            _history_page_html(robot, variant, records, loaded_reports), encoding="utf-8"
        )

    (reports_dir / "index.html").write_text(_index_page_html(history), encoding="utf-8")
    return history


def build(
    reports_dir: Path,
    runs_dir: Path,
    *,
    now: str | None = None,
    days: int = DEFAULT_RETENTION_DAYS,
    allow_replace_run_id: str | None = None,
    git_dir: Path | None = None,
) -> list[Path]:
    validate_retention_days(days)
    existing = runs(reports_dir)
    restored_keys = {
        (robot, variant, rec["folder"])
        for (robot, variant), recs in existing.items()
        for rec in recs
    }
    seen_in_batch: set[tuple[str, str, str]] = set()
    sources = discover_run_folders(runs_dir)

    added_folders: list[Path] = []
    timestamps: list[str] = []
    for source in sources:
        folder, _key, record = add_run_folder(
            reports_dir,
            source,
            restored_keys=restored_keys,
            seen_in_batch=seen_in_batch,
            allow_replace_run_id=allow_replace_run_id,
            git_dir=git_dir,
        )
        added_folders.append(folder)
        timestamps.append(record["run"]["created"])

    all_runs = runs(reports_dir)
    robot = _require_single_robot({r for r, _ in all_runs})

    effective_now = now
    if not effective_now:
        effective_now = max(
            timestamps,
            key=lambda ts: _stamp(ts),
        )
    else:
        _stamp(effective_now)

    history = render(
        reports_dir,
        now=effective_now,
        days=days,
        publish_folders=set(added_folders),
    )
    if problems := public_page_problems(reports_dir):
        raise SiteError(
            "published pages contain Actions links or artifact wording: " + ", ".join(problems)
        )

    total_runs = sum(len(recs) for recs in history.values())
    marker_payload = {
        "robot": robot,
        "scenarios": len(history),
        "runs": total_runs,
        "updated": effective_now,
    }
    (reports_dir / MARKER).write_text(json.dumps(marker_payload, indent=2) + "\n", encoding="utf-8")
    return added_folders


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    r = sub.add_parser(
        "restore",
        help="restore earlier runs from the last successfully deployed Pages artifact",
    )
    r.add_argument("reports_dir", type=Path, help="destination reports/ directory")
    r.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    r.add_argument("--branch", default="main")
    r.add_argument(
        "--current-run",
        type=int,
        default=int(os.environ["GITHUB_RUN_ID"]) if os.environ.get("GITHUB_RUN_ID") else None,
    )
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--site-url", default=os.environ.get("SITE_URL") or None)
    r.add_argument("--pages-artifact", default=None)
    r.add_argument("--pages-job", default=None)
    r.add_argument("--deploy-workflow", default=None)
    r.add_argument("--reset-history", action="store_true")

    b = sub.add_parser(
        "build",
        help="add run folders to reports_dir, prune expired media, and render index and history",
    )
    b.add_argument("reports_dir", type=Path, help="destination reports/ directory")
    b.add_argument("--runs", type=Path, required=True, help="directory of incoming run folders")
    b.add_argument(
        "--evidence-retention-days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        help="days to retain copied MCAP, video, and media files before pruning",
    )
    b.add_argument("--now", default=None, help="UTC ISO-8601 reference time for retention pruning")
    b.add_argument(
        "--allow-replace-run-id",
        default=None,
        help="optional current run id allowed to replace itself on a same-run retry",
    )
    b.add_argument("--git-dir", type=Path, default=None)

    args = parser.parse_args(argv)
    try:
        if args.command == "restore":
            restore(
                args.reports_dir,
                args.repository,
                args.branch,
                args.current_run,
                os.environ.get("GITHUB_TOKEN"),
                dry_run=args.dry_run,
                site_url=args.site_url,
                reset_history=args.reset_history,
                pages_artifact=args.pages_artifact or None,
                pages_job=args.pages_job or None,
                deploy_workflow=args.deploy_workflow or None,
            )
        elif args.command == "build":
            added = build(
                args.reports_dir,
                args.runs,
                now=args.now or None,
                days=args.evidence_retention_days,
                allow_replace_run_id=args.allow_replace_run_id or None,
                git_dir=args.git_dir,
            )
            marker = read_json_object(args.reports_dir / MARKER)
            for folder in added:
                print(f"Added {folder.relative_to(args.reports_dir)}")
            print(
                f"Built {args.reports_dir} "
                f"({marker['scenarios']} scenarios, {marker['runs']} runs)."
            )
    except SiteError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
