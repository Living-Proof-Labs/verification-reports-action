#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Restore earlier verification runs from the calling repository's deployed Pages artifact."""

from __future__ import annotations

import http.client
import io
import json
import random
import re
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from reports_common import (
    SiteError,
)

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
MARKER = "site.json"

DOWNLOAD_ATTEMPTS = 5
DOWNLOAD_BACKOFF_BASE = 10
DOWNLOAD_JITTER = (0.8, 1.2)
LOOKUP_ATTEMPTS = 3
LOOKUP_BACKOFF_BASE = 1.0
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5


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


# GitHub API paths built from caller data (the repository) or API responses (run ids).
# Each segment must be a plain name, and each is percent-encoded, so no value can change
# the path or the query. Same rules as apiPathSegments in src/definitions.mjs.
_API_PATH_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f\\%?#]")


def _api_path_segments(value: object, label: str) -> list[str]:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return [str(value)]
    if (
        not isinstance(value, str)
        or not value
        or _API_PATH_FORBIDDEN.search(value)
        # An empty part also refuses a leading or trailing "/".
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise SiteError(f"{label} is not a safe API path: {value!r}.")
    return value.split("/")


def _api_url(api: str, *parts: object, query: dict | None = None) -> str:
    segments = [
        segment for part in parts for segment in _api_path_segments(part, "GitHub API path")
    ]
    url = f"{api}/" + "/".join(urllib.parse.quote(segment, safe="") for segment in segments)
    return f"{url}?{urllib.parse.urlencode(query)}" if query else url


def _repository_parts(repository: object) -> list[str]:
    parts = _api_path_segments(repository, "Repository")
    if len(parts) != 2:
        raise SiteError(f"Repository is not OWNER/NAME: {repository!r}.")
    return parts


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


def _read_following_redirects(
    url: str, token: str | None, *, max_bytes: int | None = None
) -> bytes:
    current = url
    home = _host(url)
    for _hop in range(_MAX_REDIRECTS + 1):
        if max_bytes is not None and urllib.parse.urlsplit(current).scheme != "https":
            raise SiteError("Authenticated log download requires HTTPS")
        try:
            with _open_once(current, token if _host(current) == home else None) as response:
                body = response.read() if max_bytes is None else response.read(max_bytes + 1)
                if max_bytes is not None and len(body) > max_bytes:
                    raise SiteError("Authenticated download exceeds its size bound")
                return body
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
    name_query = {"name": pages_artifact} if pages_artifact else {}
    for page in range(1, MAX_ARTIFACT_PAGES + 1):
        url = _api_url(
            api,
            "repos",
            *_repository_parts(repository),
            "actions/artifacts",
            query={**name_query, "per_page": 100, "page": page},
        )
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
        url = _api_url(
            api,
            "repos",
            *_repository_parts(repository),
            "actions/runs",
            run_id,
            "jobs",
            query={"filter": "latest", "per_page": 100, "page": page},
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
    url = _api_url(api, "repos", *_repository_parts(repository))
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
    url = _api_url(api, "repos", *_repository_parts(repository), "actions/runs", run_id)
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
    url = _api_url(api, "repos", *_repository_parts(repository), "pages")
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
    publication_policy: dict | None = None,
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
    archive = _download_pages_archive(artifact["archive_download_url"], token)
    if publication_policy is not None:
        import shutil
        import tempfile

        from publication_authority import verify
        from trusted_viewer import discover_viewers, publication_approvals

        # Extract only into a private staging directory. Never modify existing
        # history before all authenticated archive and native panel checks pass.
        with tempfile.TemporaryDirectory(prefix="vra-approved-history-") as directory:
            staging = Path(directory)
            # When GitHub provides the digest, reject changed ZIP bytes before
            # extraction. Legacy undecorated archives without a digest remain
            # compatible; decorated archives always require it in verify().
            checksum = artifact.get("digest")
            if checksum is not None:
                import hashlib

                if checksum != "sha256:" + hashlib.sha256(archive).hexdigest():
                    raise SiteError("Unverified Pages publication: archive digest")
            count = _unpack_pages_archive(archive, staging)
            decorated = discover_viewers(staging)
            approvals = []
            if decorated:
                verify(artifact, archive, repository, branch, token, api, publication_policy)
                approvals = publication_approvals(staging, publication_policy["viewers"])
            if any(destination.iterdir()):
                raise SiteError("Authenticated restore requires an empty destination")
            shutil.copytree(staging, destination, dirs_exist_ok=True)
            artifact = dict(artifact, _viewer_approvals=approvals)
    else:
        count = _unpack_pages_archive(archive, destination)
    print(f"Restored {count} files into {destination}.")
    return artifact
