# SPDX-License-Identifier: Apache-2.0
"""Tests for the site action (`src/site_action.py`) and the Pages restore (`reports_restore.py`).

The reports section itself is rendered by the pinned reporter's `site` command; its tests
moved to livingproof-capability-report with the renderer (#75).
"""

from __future__ import annotations

import email.message
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from importlib import resources
from pathlib import Path

import pytest
from livingproof_capability_report.site import assemble as site_build

import reports_restore
import site_action
import site_fixture

REPO = "Living-Proof-Labs/livingproof-unitree-g1"
REPO_ID = 1441484729
ARCHIVE_URL = "https://api.example/7/zip"
BLOB_URL = "https://objects.githubusercontent.example/github-pages/artifact"
SITE_URL = "https://docs.example/livingproof-unitree-g1/"


def _run_url_id(run_id: str) -> str:
    """GitHub run URLs use a numeric id; some tests use a non-numeric folder name."""
    token = run_id.split("-")[0]
    return token if token.isdigit() else "100"


def _make_run_folder(
    parent: Path,
    name: str,
    *,
    repository: str = REPO,
    variant: str = "stock-mujoco-hallway",
    capability: str = "walk-hallway",
    configuration: str = "stock",
    run_id: str = "100",
    created: str = "2026-11-20T06:00:00Z",
    grade: str = "FAIL",
    k: int | None = 12,
    n: int | None = 20,
    required_passes: int | None = 18,
    commit: str = "a" * 40,
    tag: str | None = "v2.3.32",
    spec_tag: str = "v3.1.0",
    spec_revision: str = "0.2.0",
    reporter_tag: str = "v3.15.4",
    finding: str | None = None,
    reproducibility: dict | None = None,
    evaluator_results: list[dict] | None = None,
    integrity_results: dict | None = None,
    evidence_files: dict[str, bytes] | None = None,
    html_override: str | None = None,
    thresholds_bytes: bytes | None = None,
    caller: str | None = None,
) -> Path:
    folder = parent / name
    folder.mkdir(parents=True, exist_ok=True)

    if evaluator_results is None:
        if grade == "UNSUPPORTED":
            results = []
        else:
            ev_doc: dict = {
                "format": "livingproof-evaluator-result/1.1.0",
                "evaluator": {"id": "livingproof-scenario", "display_name": "Scenario"},
                "descriptor": {
                    "title": "Scenario",
                    "purpose": "Evaluates scenario completion.",
                },
            }
            if finding:
                ev_doc["grade"] = "FAIL"
                ev_doc["findings"] = [{"summary": finding}]
            if thresholds_bytes is not None:
                (folder / "thresholds.json").write_bytes(thresholds_bytes)
                ev_doc["inputs"] = [
                    {
                        "role": "thresholds",
                        "uri": "thresholds.json",
                        "sha256": hashlib.sha256(thresholds_bytes).hexdigest(),
                    }
                ]
            results = [{"id": "trial-01", "evaluator_results": [ev_doc]}]
    else:
        results = [{"id": "trial-01", "evaluator_results": evaluator_results}]

    source_bundle: dict = {"results": results}
    if integrity_results is not None:
        source_bundle["assessment"] = {"integrity_results": integrity_results}

    robot_slug = repository.rsplit("/", 1)[-1].removeprefix("livingproof-").lower()
    summary: dict = {
        "capability": capability,
        "spec": {
            "repository": "Living-Proof-Labs/robot-capability-specs",
            "tag": spec_tag,
            "commit": "b" * 40,
            "capability": capability,
            "revision": spec_revision,
        },
        "system": {"id": f"{robot_slug}/{configuration}", "version": "1.1.1"},
        "grade": grade,
        "k": k,
        "n": n,
        "required_passes": required_passes,
    }
    if reproducibility is not None:
        summary["reproducibility"] = reproducibility

    report = {
        "report_version": "1.0.0",
        "source_bundle": source_bundle,
        "summaries": [summary],
    }
    (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    evidence_markup = ""
    if evidence_files:
        ev_dir = folder / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        links_html = []
        for rel_name, payload in evidence_files.items():
            dest = ev_dir / rel_name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(payload)
            if rel_name.endswith(".mp4"):
                links_html.append(f"<video controls src='evidence/{rel_name}'></video>")
            else:
                links_html.append(f"<p><a href='evidence/{rel_name}'>{rel_name}</a></p>")
        evidence_markup = (
            "<section id='evidence'><p class='is-kept'>Evidence kept for 7 days</p>"
            + "".join(links_html)
            + "</section>"
        )

    if html_override is not None:
        html_text = html_override
    else:
        html_text = (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<title>{variant}</title></head><body><main>"
            "<section id='summary'><div id='evaluator-livingproof-scenario'>"
            f"<h1>{variant}: {grade}</h1></div></section>"
            f"{evidence_markup}</main></body></html>\n"
        )
    (folder / "report.html").write_text(html_text, encoding="utf-8")
    (folder / "report.md").write_text(f"# {variant}\n\nGrade: {grade}\n", encoding="utf-8")
    (folder / "report.txt").write_text(f"{variant}: {grade}\n", encoding="utf-8")

    commit_info: dict = {
        "sha": commit,
        "url": f"https://github.com/{repository}/commit/{commit}",
    }
    if tag:
        commit_info["tag"] = tag

    run_record = {
        "variant": variant,
        "verification": f"{robot_slug}-{variant}",
        "run": {
            "id": run_id,
            "url": f"https://github.com/{caller or repository}/actions/runs/{_run_url_id(run_id)}",
            "created": created,
        },
        "commit": commit_info,
        "robot": {
            "repository": repository,
            "commit": commit,
            "configuration": configuration,
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
                "tag": spec_tag,
                "commit": "b" * 40,
                "url": f"https://github.com/Living-Proof-Labs/robot-capability-specs/releases/tag/{spec_tag}",
            },
            "verifier": {
                "repository": "Living-Proof-Labs/capability-verifier",
                "tag": "v2.11.4",
                "commit": "c" * 40,
                "url": "https://github.com/Living-Proof-Labs/capability-verifier/releases/tag/v2.11.4",
            },
            "reporter": {
                "repository": "Living-Proof-Labs/livingproof-capability-report",
                "tag": reporter_tag,
                "commit": "d" * 40,
                "url": f"https://github.com/Living-Proof-Labs/livingproof-capability-report/releases/tag/{reporter_tag}",
            },
        },
    }
    (folder / "run.json").write_text(json.dumps(run_record, indent=2) + "\n", encoding="utf-8")

    summed = list(site_build.REPORT_FILES)
    if (folder / "thresholds.json").is_file():
        summed.append("thresholds.json")
    (folder / "SHA256SUMS").write_text(
        "".join(f"{hashlib.sha256((folder / n).read_bytes()).hexdigest()}  {n}\n" for n in summed),
        encoding="utf-8",
    )
    return folder


def _pages_zip_from_docs_root(docs_root: Path) -> bytes:
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        tar.add(docs_root, arcname=".")
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as archive:
        archive.writestr("artifact.tar", tar_buffer.getvalue())
    return zip_buffer.getvalue()


def _artifact_entry(
    identifier: int,
    branch: str,
    run_id: int,
    created: str,
    *,
    expired: bool = False,
    name: str = "github-pages-1",
    repo_id: int = REPO_ID,
    head_repo_id: object = REPO_ID,
) -> dict:
    return {
        "id": identifier,
        "name": name,
        "created_at": created,
        "expired": expired,
        "archive_download_url": ARCHIVE_URL,
        "workflow_run": {
            "id": run_id,
            "head_branch": branch,
            "head_repository_id": head_repo_id,
            "repository_id": repo_id,
        },
    }


def _workflow_run_payload(
    run_id: int,
    *,
    branch: str = "main",
    event: str = "workflow_run",
    path: str = ".github/workflows/release.yml",
    repo_id: object = REPO_ID,
    head_repo_id: object = REPO_ID,
) -> dict:
    return {
        "id": run_id,
        "event": event,
        "path": path,
        "head_branch": branch,
        "head_repository": {"id": head_repo_id, "full_name": REPO},
        "repository": {"id": repo_id, "full_name": REPO},
    }


# ---------------------------------------------------------------- index & single-robot layout


# ---------------------------------------------------------------- history, restore & immutability


def test_run_n_plus_one_restores_from_pages_artifact_and_keeps_run_n_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Run N is in the deployed Pages artifact; Run N+1 restores it and adds itself."""
    # --- Run N (100) ---
    run1_input = tmp_path / "run1-in"
    site_fixture.write(
        run1_input,
        run_id="100",
        created="2026-11-20T06:00:00Z",
        repository=REPO,
        commit="1" * 40,
        tag="v2.3.31",
    )
    docs_v1 = tmp_path / "docs-v1"
    docs_v1.mkdir()
    (docs_v1 / "index.html").write_text("<!doctype html><title>G1 Manual v2.3.31</title>\n")
    (docs_v1 / "obsolete-manual-page.html").write_text(
        "Old manual page that must not leak into reports/"
    )

    reports_v1 = docs_v1 / "reports"
    site_build.build(reports_v1, run1_input, repository=REPO)
    assert site_fixture.check_reports_tree(reports_v1, repository=REPO) == []

    # Snapshot every file in runs/100/ after Run N to verify byte-for-byte immutability.
    run100_dir_v1 = reports_v1 / "unitree-g1/stock-mujoco-hallway/runs/100"
    run100_bytes_before = {
        p.relative_to(run100_dir_v1).as_posix(): p.read_bytes()
        for p in sorted(run100_dir_v1.rglob("*"))
        if p.is_file()
    }
    assert "index.html" in run100_bytes_before
    assert "evidence/trials.mcap" in run100_bytes_before

    pages_archive = _pages_zip_from_docs_root(docs_v1)

    # Simulate an intervening failed deploy (run 150) above the last successful deploy (run 100).
    artifacts_list = [
        _artifact_entry(9, "main", 150, "2026-11-21T06:00:00Z", name="github-pages-150-1"),
        _artifact_entry(7, "main", 100, "2026-11-20T06:05:00Z", name="github-pages-1"),
    ]

    def fake_request(url: str, token: str | None):
        path = urllib.parse.urlsplit(url).path.rstrip("/")
        if path == f"/repos/{REPO}":
            return io.BytesIO(json.dumps({"id": REPO_ID, "full_name": REPO}).encode())
        if path == f"/repos/{REPO}/actions/runs/150/jobs":
            return io.BytesIO(
                json.dumps(
                    {"jobs": [{"name": "deploy-manual / deploy", "conclusion": "failure"}]}
                ).encode()
            )
        if path == f"/repos/{REPO}/actions/runs/100/jobs":
            return io.BytesIO(
                json.dumps(
                    {
                        "jobs": [
                            {"name": "release / release", "conclusion": "success"},
                            {"name": "deploy-manual / build", "conclusion": "success"},
                            {"name": "deploy-manual / deploy", "conclusion": "success"},
                        ]
                    }
                ).encode()
            )
        if "/actions/runs/" in path:
            rid = int(path.rsplit("/", 1)[-1])
            return io.BytesIO(json.dumps(_workflow_run_payload(rid)).encode())
        return io.BytesIO(json.dumps({"artifacts": artifacts_list}).encode())

    def fake_open_once(url: str, token: str | None):
        if url == ARCHIVE_URL:
            assert token == "ghs_test_token"
            headers = email.message.Message()
            headers["Location"] = BLOB_URL
            raise urllib.error.HTTPError(url, 302, "Found", headers, None)
        if url == BLOB_URL:
            # Token must never be forwarded to blob storage.
            assert token is None
            return io.BytesIO(pages_archive)
        raise AssertionError(f"Unexpected URL {url}")

    monkeypatch.setattr(reports_restore, "_request", fake_request)
    monkeypatch.setattr(reports_restore, "_open_once", fake_open_once)

    # --- Run N+1 (200) ---
    run2_input = tmp_path / "run2-in"
    site_fixture.write(
        run2_input,
        run_id="200",
        created="2026-11-22T06:00:00Z",
        repository=REPO,
        commit="2" * 40,
        tag="v2.3.32",
    )
    docs_v2 = tmp_path / "docs-v2"
    docs_v2.mkdir()
    (docs_v2 / "index.html").write_text("<!doctype html><title>G1 Manual v2.3.32</title>\n")
    reports_v2 = docs_v2 / "reports"

    restored_artifact = reports_restore.restore(
        reports_v2, REPO, branch="main", current_run=200, token="ghs_test_token"
    )
    assert restored_artifact is not None and restored_artifact["id"] == 7
    assert not (reports_v2 / "obsolete-manual-page.html").exists()

    site_build.build(reports_v2, run2_input, repository=REPO)
    assert site_fixture.check_reports_tree(reports_v2, repository=REPO) == []

    # Both runs 100 and 200 are present; history is newest first; latest/ points to 200.
    hallway_base = reports_v2 / "unitree-g1/stock-mujoco-hallway"
    assert sorted(p.name for p in (hallway_base / "runs").iterdir()) == ["100", "200"]
    history_html = (hallway_base / "index.html").read_text(encoding="utf-8")
    assert "2 runs, newest first." in history_html
    assert history_html.index('href="runs/200/"') < history_html.index('href="runs/100/"')
    assert '<a href="latest/">Latest report</a>' in history_html
    latest_html = (hallway_base / "latest/index.html").read_text(encoding="utf-8")
    assert "url=../runs/200/" in latest_html

    index_html = (reports_v2 / "index.html").read_text(encoding="utf-8")
    assert "History (2)" in index_html

    # Run 100 remains completely immutable byte-for-byte.
    run100_dir_v2 = hallway_base / "runs/100"
    run100_bytes_after = {
        p.relative_to(run100_dir_v2).as_posix(): p.read_bytes()
        for p in sorted(run100_dir_v2.rglob("*"))
        if p.is_file()
    }
    assert run100_bytes_after == run100_bytes_before

    # Attempting to overwrite historical run 100 in a subsequent run 300 is refused.
    collide_input = tmp_path / "collide"
    site_fixture.write(collide_input, run_id="100", created="2026-11-23T06:00:00Z", repository=REPO)
    with pytest.raises(site_build.SiteError, match="already published in history and is immutable"):
        site_build.build(reports_v2, collide_input, repository=REPO)


def test_restore_handles_manual_only_pages_artifact_before_first_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A pre-reports manual Pages artifact with no reports/ directory restores 0 files cleanly."""
    manual_only = tmp_path / "manual-only"
    manual_only.mkdir()
    (manual_only / "index.html").write_text("<!doctype html><title>Manual only</title>\n")
    archive = _pages_zip_from_docs_root(manual_only)

    artifacts_list = [_artifact_entry(5, "main", 90, "2026-11-19T00:00:00Z")]

    def fake_request(url: str, token: str | None):
        path = urllib.parse.urlsplit(url).path.rstrip("/")
        if path == f"/repos/{REPO}":
            return io.BytesIO(json.dumps({"id": REPO_ID, "full_name": REPO}).encode())
        if path.endswith("/jobs"):
            return io.BytesIO(
                json.dumps({"jobs": [{"name": "deploy", "conclusion": "success"}]}).encode()
            )
        if "/actions/runs/" in path:
            return io.BytesIO(json.dumps(_workflow_run_payload(90, event="push")).encode())
        return io.BytesIO(json.dumps({"artifacts": artifacts_list}).encode())

    monkeypatch.setattr(reports_restore, "_request", fake_request)
    monkeypatch.setattr(reports_restore, "_open_once", lambda url, token: io.BytesIO(archive))

    dest = tmp_path / "reports"
    found = reports_restore.restore(dest, REPO, "main", 100, "tok")
    assert found is not None and found["id"] == 5
    assert list(dest.iterdir()) == []


def test_restore_security_and_failure_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 1. Unsafe tar path is rejected.
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tar:
        info = tarfile.TarInfo("../escape.html")
        tar.addfile(info, io.BytesIO(b"bad"))
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("artifact.tar", tar_buf.getvalue())
    with pytest.raises(reports_restore.SiteError, match="Unsafe path"):
        reports_restore.extract_pages_reports(zip_buf.getvalue(), tmp_path / "out")

    # 2. Symlink inside tar is rejected.
    tar_sym = io.BytesIO()
    with tarfile.open(fileobj=tar_sym, mode="w") as tar:
        sym = tarfile.TarInfo("reports/link")
        sym.type = tarfile.SYMTYPE
        sym.linkname = "/etc/passwd"
        tar.addfile(sym)
    zip_sym = io.BytesIO()
    with zipfile.ZipFile(zip_sym, "w") as zf:
        zf.writestr("artifact.tar", tar_sym.getvalue())
    with pytest.raises(reports_restore.SiteError, match="Unexpected entry"):
        reports_restore.extract_pages_reports(zip_sym.getvalue(), tmp_path / "out2")

    # 3. Fork artifact & pull_request event artifact are ignored; boolean repo ID is rejected.
    assert reports_restore._github_id(True) is None
    fork_artifact = _artifact_entry(1, "main", 10, "2026-11-20T00:00:00Z", head_repo_id=999999)
    assert not reports_restore._listed_run_is_this_repository(fork_artifact, REPO_ID)
    pr_run = _workflow_run_payload(11, event="pull_request")
    assert not reports_restore._fetched_run_is_pages_deploy(pr_run, REPO_ID, "main")

    # 4. Expired newest deployed artifact raises SiteError unless reset_history=True.
    expired_list = [_artifact_entry(8, "main", 100, "2026-08-01T00:00:00Z", expired=True)]

    def fake_expired_req(url: str, token: str | None):
        path = urllib.parse.urlsplit(url).path.rstrip("/")
        if path == f"/repos/{REPO}":
            return io.BytesIO(json.dumps({"id": REPO_ID}).encode())
        if path.endswith("/jobs"):
            return io.BytesIO(
                json.dumps({"jobs": [{"name": "pages", "conclusion": "success"}]}).encode()
            )
        if "/actions/runs/" in path:
            return io.BytesIO(json.dumps(_workflow_run_payload(100)).encode())
        return io.BytesIO(json.dumps({"artifacts": expired_list}).encode())

    monkeypatch.setattr(reports_restore, "_request", fake_expired_req)
    with pytest.raises(reports_restore.SiteError, match="expired"):
        reports_restore.restore(tmp_path / "exp", REPO, "main", 200, "tok", site_url=SITE_URL)
    assert (
        reports_restore.restore(
            tmp_path / "exp", REPO, "main", 200, "tok", site_url=SITE_URL, reset_history=True
        )
        is None
    )

    # 5. Live site check probes reports/site.json and reports/index.html, not root index.html.
    monkeypatch.setattr(reports_restore, "_request", lambda u, t: io.BytesIO(b'{"artifacts": []}'))
    probed: list[str] = []

    def fake_status_404(url: str) -> int:
        probed.append(url)
        return 404

    monkeypatch.setattr(reports_restore, "_fetch_status", fake_status_404)
    assert (
        reports_restore.restore(tmp_path / "fresh", REPO, "main", 1, "tok", site_url=SITE_URL)
        is None
    )
    assert probed == [SITE_URL + "reports/site.json", SITE_URL + "reports/index.html"]

    monkeypatch.setattr(reports_restore, "_fetch_status", lambda url: 200)
    with pytest.raises(reports_restore.SiteError, match="reset-history"):
        reports_restore.restore(tmp_path / "live", REPO, "main", 1, "tok", site_url=SITE_URL)


def test_restore_retries_transient_503_on_blob_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    docs = tmp_path / "docs"
    runs_in = tmp_path / "in"
    site_fixture.write(runs_in, run_id="100", created="2026-11-20T00:00:00Z")
    site_build.build(docs / "reports", runs_in, repository=REPO)
    archive = _pages_zip_from_docs_root(docs)

    artifacts_list = [_artifact_entry(7, "main", 100, "2026-11-20T00:05:00Z")]

    def fake_request(url: str, token: str | None):
        path = urllib.parse.urlsplit(url).path.rstrip("/")
        if path == f"/repos/{REPO}":
            return io.BytesIO(json.dumps({"id": REPO_ID}).encode())
        if path.endswith("/jobs"):
            return io.BytesIO(
                json.dumps({"jobs": [{"name": "deploy", "conclusion": "success"}]}).encode()
            )
        if "/actions/runs/" in path:
            return io.BytesIO(json.dumps(_workflow_run_payload(100)).encode())
        return io.BytesIO(json.dumps({"artifacts": artifacts_list}).encode())

    headers = email.message.Message()
    headers["Location"] = BLOB_URL
    redirect = urllib.error.HTTPError(ARCHIVE_URL, 302, "Found", headers, None)
    blob_503 = urllib.error.HTTPError(
        BLOB_URL, 503, "Service Unavailable", email.message.Message(), None
    )
    queue = [redirect, blob_503, redirect, archive]
    calls: list[tuple[str, str | None]] = []
    sleeps: list[float] = []

    def fake_open(url: str, token: str | None):
        calls.append((url, token))
        nxt = queue.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return io.BytesIO(nxt)

    monkeypatch.setattr(reports_restore, "_request", fake_request)
    monkeypatch.setattr(reports_restore, "_open_once", fake_open)
    monkeypatch.setattr(reports_restore.time, "sleep", sleeps.append)
    monkeypatch.setattr(reports_restore.random, "uniform", lambda _a, _b: 1.0)

    dest = tmp_path / "restored"
    found = reports_restore.restore(dest, REPO, "main", 200, "secret_tok")
    assert found is not None and found["id"] == 7
    assert calls == [(ARCHIVE_URL, "secret_tok"), (BLOB_URL, None)] * 2
    assert sleeps == [10]
    assert (dest / "unitree-g1/stock-mujoco-hallway/runs/100/run.json").is_file()


# ---------------------------------------------------------------- configurable MCAP & video pruning


# ---------------------------------------------------------------- chips, integrity & scrubbing


# ---------------------------------------------------------------- client timezone script


# ---------------------------------------------------------------- site_action PR vs main behavior


def test_site_action_pull_request_builds_artifact_only_without_restoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def forbid_restore(*args, **kwargs):
        raise AssertionError("restore() must not run on pull_request with restore-history=auto")

    monkeypatch.setattr(reports_restore, "restore", forbid_restore)

    runs_in = tmp_path / "runs"
    site_fixture.write(runs_in, run_id="42", created="2026-11-23T01:00:00Z")
    out_dir = tmp_path / "reports"
    gh_out = tmp_path / "github_output.txt"

    outputs = site_action.run_site_action(
        {
            "INPUT_RUNS_DIR": str(runs_in),
            "INPUT_OUTPUT_DIR": str(out_dir),
            "INPUT_EVIDENCE_RETENTION_DAYS": "7",
            "INPUT_RESTORE_HISTORY": "auto",
            "INPUT_UPLOAD_ARTIFACT": "auto",
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_RUN_ID": "999",
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_WORKSPACE": str(tmp_path),
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_OUTPUT": str(gh_out),
        }
    )
    assert outputs["artifact-only"] == "true"
    assert outputs["should-upload-artifact"] == "true"
    assert outputs["runs-count"] == "3"
    assert (out_dir / "index.html").is_file()
    written_out = gh_out.read_text(encoding="utf-8")
    assert "artifact-only=true\n" in written_out
    assert "should-upload-artifact=true\n" in written_out


def test_site_action_main_push_restores_history_and_skips_pr_upload_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    called_restore = []

    def track_restore(dest, repo, **kwargs):
        called_restore.append((dest, repo, kwargs))
        return None

    monkeypatch.setattr(reports_restore, "restore", track_restore)

    runs_in = tmp_path / "runs"
    site_fixture.write(runs_in, run_id="500", created="2026-11-23T02:00:00Z")
    out_dir = tmp_path / "docs/html/reports"

    outputs = site_action.run_site_action(
        {
            "INPUT_RUNS_DIR": str(runs_in),
            "INPUT_OUTPUT_DIR": str(out_dir),
            "INPUT_RESTORE_HISTORY": "auto",
            "INPUT_UPLOAD_ARTIFACT": "auto",
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_RUN_ID": "500",
            "GITHUB_WORKSPACE": str(tmp_path),
            "GITHUB_TOKEN": "tok",
        }
    )
    assert len(called_restore) == 1
    assert called_restore[0][1] == REPO
    assert outputs["artifact-only"] == "false"
    assert outputs["should-upload-artifact"] == "false"
    assert (out_dir / "index.html").is_file()


# ---------------------------------------------------------------- takeover hardening (Rivet)


def _zip_of_tar(entries: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tar:
        for info, data in entries:
            if data is not None:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            else:
                tar.addfile(info)
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("artifact.tar", tar_buf.getvalue())
    return zip_buf.getvalue()


def test_restore_refuses_hardlinks_duplicates_and_symlinks_already_in_the_destination(
    tmp_path: Path,
):
    hard = tarfile.TarInfo("reports/a/b/runs/1/run.json")
    hard.type = tarfile.LNKTYPE
    hard.linkname = "etc/passwd"
    with pytest.raises(reports_restore.SiteError, match="Unexpected entry"):
        reports_restore.extract_pages_reports(_zip_of_tar([(hard, None)]), tmp_path / "hard")

    dup = [
        (tarfile.TarInfo("reports/index.html"), b"one"),
        (tarfile.TarInfo("reports/index.html"), b"two"),
    ]
    with pytest.raises(reports_restore.SiteError, match="more than once"):
        reports_restore.extract_pages_reports(_zip_of_tar(dup), tmp_path / "dup")

    # A symlink that already sits in the output directory (for example committed in the
    # caller's checkout) must not let a restored file land outside it.
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "unitree-g1").symlink_to(outside, target_is_directory=True)
    archive = _zip_of_tar([(tarfile.TarInfo("reports/unitree-g1/x/runs/1/run.json"), b"{}")])
    with pytest.raises(reports_restore.SiteError, match="symlink"):
        reports_restore.extract_pages_reports(archive, dest)
    assert list(outside.rglob("*")) == []


def test_restore_refuses_an_oversized_reports_subtree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(reports_restore, "MAX_RESTORED_BYTES", 4)
    archive = _zip_of_tar([(tarfile.TarInfo("reports/index.html"), b"12345")])
    with pytest.raises(reports_restore.SiteError, match="too large"):
        reports_restore.extract_pages_reports(archive, tmp_path / "big")
    assert not (tmp_path / "big" / "index.html").exists()


def test_site_action_restores_only_from_the_calling_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    seen: list[str] = []
    monkeypatch.setattr(reports_restore, "restore", lambda dest, repo, **kw: seen.append(repo))
    runs_in = tmp_path / "runs"
    site_fixture.write(runs_in, run_id="501", created="2026-11-23T02:00:00Z")
    site_action.run_site_action(
        {
            "INPUT_RUNS_DIR": str(runs_in),
            "INPUT_OUTPUT_DIR": str(tmp_path / "reports"),
            "INPUT_REPOSITORY": "Other-Org/attacker-site",
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_RUN_ID": "501",
            "GITHUB_WORKSPACE": str(tmp_path),
        }
    )
    assert seen == [REPO]


@pytest.mark.parametrize(
    "bad_output_dir",
    ["", "   ", ".", "..", "../escape", "sub/../../escape", "a\\b", "/"],
)
def test_site_action_rejects_unsafe_output_dir(tmp_path: Path, bad_output_dir: str):
    runs_in = tmp_path / "runs"
    site_fixture.write(runs_in, run_id="77", created="2026-11-23T03:00:00Z")
    with pytest.raises(reports_restore.SiteError, match="output-dir"):
        site_action.run_site_action(
            {
                "INPUT_RUNS_DIR": str(runs_in),
                "INPUT_OUTPUT_DIR": bad_output_dir,
                "INPUT_RESTORE_HISTORY": "false",
                "INPUT_UPLOAD_ARTIFACT": "false",
                "GITHUB_REPOSITORY": REPO,
                "GITHUB_WORKSPACE": str(tmp_path),
            }
        )


def test_site_action_constrains_output_dir_to_github_workspace_and_ignores_input_now(
    tmp_path: Path,
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "link-out").symlink_to(outside, target_is_directory=True)

    runs_in = tmp_path / "runs"
    site_fixture.write(runs_in, run_id="77", created="2026-11-23T03:00:00Z")

    for escaped in (str(outside / "reports"), str(workspace), "link-out/reports"):
        with pytest.raises(reports_restore.SiteError, match="GITHUB_WORKSPACE"):
            site_action.run_site_action(
                {
                    "INPUT_RUNS_DIR": str(runs_in),
                    "INPUT_OUTPUT_DIR": escaped,
                    "INPUT_RESTORE_HISTORY": "false",
                    "INPUT_UPLOAD_ARTIFACT": "false",
                    "GITHUB_REPOSITORY": REPO,
                    "GITHUB_WORKSPACE": str(workspace),
                }
            )

    # INPUT_NOW is removed and ignored: setting it far in the future must not prune evidence.
    rel_out = "docs/reports"
    prev_cwd = Path.cwd()
    try:
        os.chdir(workspace)
        outputs = site_action.run_site_action(
            {
                "INPUT_RUNS_DIR": str(runs_in),
                "INPUT_OUTPUT_DIR": rel_out,
                "INPUT_EVIDENCE_RETENTION_DAYS": "7",
                "INPUT_NOW": "2099-01-01T00:00:00Z",
                "INPUT_RESTORE_HISTORY": "false",
                "INPUT_UPLOAD_ARTIFACT": "false",
                "GITHUB_REPOSITORY": REPO,
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_WORKSPACE": str(workspace),
                "GITHUB_RUN_ID": "77",
            }
        )
    finally:
        os.chdir(prev_cwd)

    assert outputs["reports-dir"] == rel_out
    assert (
        workspace / "docs/reports/unitree-g1/stock-mujoco-hallway/runs/77/evidence/trials.mcap"
    ).is_file()


# ---------------------------------------------------------------- va#14 takeover checks (Rivet)

ROOT = Path(__file__).resolve().parents[1]


def test_site_action_from_another_checkout_writes_the_reporter_assets(tmp_path: Path):
    # Mirrors `uses: Living-Proof-Labs/verification-reports-action/site@<sha>`: the runner
    # unpacks the action somewhere else and runs it with the caller's workspace as cwd.
    action = tmp_path / "_actions/verification-reports-action/sha"
    for part in ("src", "tools", "site"):
        shutil.copytree(ROOT / part, action / part, ignore=shutil.ignore_patterns("__pycache__"))
    workspace = tmp_path / "caller"
    workspace.mkdir()
    site_fixture.write(
        workspace / "verification-report", run_id="9", created="2026-11-20T06:00:00Z"
    )
    env = {
        "PATH": os.environ["PATH"],
        "INPUT_RUNS_DIR": "verification-report",
        "INPUT_OUTPUT_DIR": "docs/html/reports",
        "INPUT_RESTORE_HISTORY": "false",
        "INPUT_UPLOAD_ARTIFACT": "false",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(tmp_path / "out.txt"),
    }
    subprocess.run(
        [sys.executable, str(action / "site/../src/site_action.py")],
        cwd=workspace,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    built = workspace / "docs/html/reports"
    # The styles and script ship in the pinned reporter (#75), not in the action checkout.
    assert not (action / "src/assets").exists()
    assets = resources.files("livingproof_capability_report.site") / "assets"
    packaged = (assets / "styles.css").read_text(encoding="utf-8")
    assert (built / "styles.css").read_text(encoding="utf-8") == packaged.split("\n", 1)[1]
    packaged_js = (assets / "open.js").read_text(encoding="utf-8")
    assert (built / "open.js").read_text(encoding="utf-8") == packaged_js.split("\n", 1)[1]
    assert "reports-dir=docs/html/reports\n" in (tmp_path / "out.txt").read_text()


def test_site_action_yml_runs_the_script_from_the_action_path():
    text = (ROOT / "site/action.yml").read_text(encoding="utf-8")
    assert "SITE_ACTION_PATH: ${{ github.action_path }}" in text
    assert 'run: python3 "$SITE_ACTION_PATH/../src/site_action.py"' in text
    assert "github.workspace" not in text


def test_output_dir_is_resolved_in_the_workspace_not_the_cwd(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    runs_in = tmp_path / "runs"
    site_fixture.write(runs_in, run_id="5", created="2026-11-23T03:00:00Z")
    base = {
        "INPUT_RUNS_DIR": str(runs_in),
        "INPUT_RESTORE_HISTORY": "false",
        "INPUT_UPLOAD_ARTIFACT": "false",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REPOSITORY": REPO,
    }
    outputs = site_action.run_site_action(
        base | {"INPUT_OUTPUT_DIR": "site/reports", "GITHUB_WORKSPACE": str(workspace)}
    )
    assert outputs["reports-dir"] == "site/reports"
    assert (workspace / "site/reports/index.html").is_file()
    assert not (elsewhere / "site").exists()

    for bad in (".git/reports", "docs/.git/x", "./"):
        with pytest.raises(reports_restore.SiteError, match="output-dir"):
            site_action.run_site_action(
                base | {"INPUT_OUTPUT_DIR": bad, "GITHUB_WORKSPACE": str(workspace)}
            )

    # With GITHUB_WORKSPACE unset, the current directory is the boundary; the check stays on.
    with pytest.raises(reports_restore.SiteError, match="GITHUB_WORKSPACE"):
        site_action.run_site_action(base | {"INPUT_OUTPUT_DIR": str(workspace / "reports")})


def test_output_dir_symlink_to_a_parent_of_the_workspace_is_refused(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "up").symlink_to(tmp_path, target_is_directory=True)
    runs_in = tmp_path / "runs"
    site_fixture.write(runs_in, run_id="6", created="2026-11-23T03:00:00Z")
    with pytest.raises(reports_restore.SiteError, match="GITHUB_WORKSPACE"):
        site_action.run_site_action(
            {
                "INPUT_RUNS_DIR": str(runs_in),
                "INPUT_OUTPUT_DIR": "up/escaped",
                "INPUT_RESTORE_HISTORY": "false",
                "INPUT_UPLOAD_ARTIFACT": "false",
                "GITHUB_REPOSITORY": REPO,
                "GITHUB_WORKSPACE": str(workspace),
            }
        )
    assert not (tmp_path / "escaped").exists()


# ---------------------------------------------------------------- Vera's PR 16 findings (Rivet)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("INPUT_OUTPUT_DIR", "docs/reports\nartifact-only=false"),
        ("INPUT_OUTPUT_DIR", "docs/reports\r"),
        ("INPUT_OUTPUT_DIR", "docs/\x00reports"),
        ("INPUT_OUTPUT_DIR", "docs/\x1breports"),
        ("INPUT_ARTIFACT_NAME", "site\nreports-dir=/etc"),
        ("INPUT_ARTIFACT_NAME", "site\n"),
        # The default artifact-name is built from GITHUB_RUN_ID, so it is checked too.
        ("GITHUB_RUN_ID", "8\nreports-dir=/etc"),
    ],
)
def test_site_action_refuses_control_characters_before_writing_github_output(
    tmp_path: Path, name: str, value: str
):
    runs_in = tmp_path / "runs"
    site_fixture.write(runs_in, run_id="8", created="2026-11-23T03:00:00Z")
    gh_out = tmp_path / "github_output.txt"
    env = {
        "INPUT_RUNS_DIR": str(runs_in),
        "INPUT_OUTPUT_DIR": "docs/reports",
        "INPUT_RESTORE_HISTORY": "false",
        "INPUT_UPLOAD_ARTIFACT": "false",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_WORKSPACE": str(tmp_path),
        "GITHUB_OUTPUT": str(gh_out),
    }
    env[name] = value
    # Removing the control-character check lets the newline forge a GITHUB_OUTPUT line,
    # and validating artifact-name after build() would still write to docs/reports.
    with pytest.raises(reports_restore.SiteError, match="control characters"):
        site_action.run_site_action(env)
    assert not (tmp_path / "docs/reports").exists()
    assert not gh_out.exists()


# ---------------------------------------------------------------- va#20 N1: published report HTML


# Cases the html.parser layer is not there for: the control-character check comes first, and
# html.parser alone accepts the rest on some Python releases, which is what the scan is for.


def _page(markup: str) -> str:
    return f"<!doctype html><html lang='en'><body><main>{markup}</main></body></html>\n"


# The browser-rule scan (B1). Each case is refused by the scan on its own, on every Python.


# Evidence a browser could run as a document on the Pages origin. GitHub Pages serves .xsd
# and .rng as application/xml, and Chrome runs the XHTML script inside (B2).
# What reporters emit, and the plain formats a browser only displays.


# ---------------------------------------------------------------- va#20 N1: restored runs


# ---------------------------------------------------------------- va#24: API path traversal


@pytest.mark.parametrize(
    "value",
    [
        "..",
        "Owner/..",
        "../user",
        ".",
        "Owner/.",
        "",
        "Owner//Name",
        "Owner/",
        "/Owner/Name",
        "Owner\\Name",
        "Owner/%2e%2e",
        "%2E%2E%2Fuser",
        "Owner/Na\x00me",
        "Owner/Na\nme",
        "Owner/Na\x7fme",
        "Owner/Name?x=1",
        "Owner/Name#x",
        None,
        True,
        -1,
    ],
)
def test_api_path_segments_refuse_unsafe_forms(value: object):
    with pytest.raises(reports_restore.SiteError, match="not a safe API path"):
        reports_restore._api_path_segments(value, "probe")


def test_api_url_encodes_each_segment_and_the_query():
    url = reports_restore._api_url(
        "https://api.github.com", "repos", "O/N", "actions/runs", 42, query={"name": "a&b=c"}
    )
    assert url == "https://api.github.com/repos/O/N/actions/runs/42?name=a%26b%3Dc"
    spaced = reports_restore._api_url("https://api.github.com", "repos", "O/a b&c=d@e")
    assert spaced == "https://api.github.com/repos/O/a%20b%26c%3Dd%40e"
    assert reports_restore._repository_parts("Living-Proof-Labs/livingproof-unitree-g1") == [
        "Living-Proof-Labs",
        "livingproof-unitree-g1",
    ]
    with pytest.raises(reports_restore.SiteError, match="OWNER/NAME"):
        reports_restore._repository_parts("a/b/c")


@pytest.mark.parametrize("repository", ["Owner/..", "../../user", "Owner/Name/../../x", "O/N?x"])
def test_restore_with_a_traversal_repository_makes_no_request(
    tmp_path: Path, monkeypatch, repository: str
):
    calls: list[str] = []

    def record(url, token):
        calls.append(url)
        raise AssertionError(f"unexpected request {url}")

    monkeypatch.setattr(reports_restore, "_request", record)
    monkeypatch.setattr(reports_restore, "_open_once", record)
    with pytest.raises(reports_restore.SiteError, match="not a safe API path|OWNER/NAME"):
        reports_restore.restore(tmp_path / "out", repository, token="ghs_testtoken")
    assert calls == []


def test_workflow_run_id_from_the_api_cannot_change_the_path(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(reports_restore, "_request", lambda url, token: calls.append(url))
    with pytest.raises(reports_restore.SiteError, match="not a safe API path"):
        reports_restore._workflow_run(
            "O/N", "1/../../../user", "ghs_testtoken", "https://api.github.com"
        )
    assert calls == []


# ---------------------------------------------------------------- va#32: nesting depth and size


# Hand-written stack edge cases: implicit and mismatched closes, void elements, foreign
# svg/math content and end tags for elements that are not open.


# ---------------------------------------------------------------- issue #31: exclude-runs


@pytest.mark.parametrize(
    "raw_value",
    [
        "a/b",
        "920/..",
        "..",
        "../920",
        "a\\b",
        ",",
        "920,",
        ",920",
        "920,,921",
        "920, ,921",
        "920\x00",
        "920\x1b",
        "920\x7f",
        "920\r921",
        ".hidden",
        "C:\\runs\\920",
        "920:1",
        "\uff19\uff12\uff10",
        "920\u200b",
        "92\u00e90",
    ],
)
def test_exclude_runs_invalid_entry_is_refused_before_anything_is_written(
    tmp_path: Path, monkeypatch, raw_value: str
):
    _make_run_folder(tmp_path / "in1", "hallway", run_id="921", created="2026-11-21T06:00:00Z")
    reports = tmp_path / "reports"
    restore_calls: list[object] = []

    def fail_if_called(*args, **kwargs):
        restore_calls.append((args, kwargs))
        raise AssertionError("restore() must not be called when exclude-runs is invalid")

    monkeypatch.setattr(reports_restore, "restore", fail_if_called)

    with pytest.raises(reports_restore.SiteError):
        site_action.run_site_action(
            {
                "INPUT_RUNS_DIR": str(tmp_path / "in1"),
                "INPUT_OUTPUT_DIR": "reports",
                "INPUT_RESTORE_HISTORY": "true",
                "INPUT_EXCLUDE_RUNS": raw_value,
                "GITHUB_WORKSPACE": str(tmp_path),
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_REPOSITORY": "Living-Proof-Labs/livingproof-unitree-g1",
                "GITHUB_RUN_ID": "921",
            }
        )

    assert restore_calls == []
    assert not reports.exists()


def test_site_action_parses_whitespace_and_comma_separated_exclude_runs(tmp_path: Path, capsys):
    _make_run_folder(tmp_path / "in1/r1", "hallway", run_id="918", created="2026-11-18T06:00:00Z")
    _make_run_folder(tmp_path / "in1/r2", "hallway", run_id="919", created="2026-11-19T06:00:00Z")
    _make_run_folder(tmp_path / "in1/r3", "hallway", run_id="920", created="2026-11-20T06:00:00Z")
    reports = tmp_path / "reports"
    site_build.build(reports, tmp_path / "in1", repository=REPO)

    _make_run_folder(tmp_path / "in2", "hallway", run_id="921", created="2026-11-21T06:00:00Z")
    capsys.readouterr()
    outputs = site_action.run_site_action(
        {
            "INPUT_RUNS_DIR": str(tmp_path / "in2"),
            "INPUT_OUTPUT_DIR": "reports",
            "INPUT_RESTORE_HISTORY": "false",
            "INPUT_EXCLUDE_RUNS": "918, 920\n999999",
            "GITHUB_WORKSPACE": str(tmp_path),
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_RUN_ID": "921",
        }
    )
    err = capsys.readouterr().err

    assert outputs["runs-count"] == "2"
    assert not (reports / "unitree-g1/stock-mujoco-hallway/runs/918").exists()
    assert (reports / "unitree-g1/stock-mujoco-hallway/runs/919").is_dir()
    assert not (reports / "unitree-g1/stock-mujoco-hallway/runs/920").exists()
    assert (reports / "unitree-g1/stock-mujoco-hallway/runs/921").is_dir()
    assert "918" in err
    assert "920" in err
    assert "999999" in err


# ---------------------------------------------------------------- issue #40: exclude-runs follow-up


def test_unsafe_archive_path_stays_fail_closed_even_with_exclude_runs(tmp_path: Path, monkeypatch):
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tar:
        info = tarfile.TarInfo("reports/unitree-g1/stock-mujoco-hallway/runs/920/../escape.html")
        tar.addfile(info, io.BytesIO(b"bad"))
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("artifact.tar", tar_buf.getvalue())
    archive = zip_buf.getvalue()

    artifacts_list = [_artifact_entry(5, "main", 90, "2026-11-20T00:00:00Z")]

    def fake_request(url: str, token: str | None):
        path = urllib.parse.urlsplit(url).path.rstrip("/")
        if path == f"/repos/{REPO}":
            return io.BytesIO(json.dumps({"id": REPO_ID, "full_name": REPO}).encode())
        if path.endswith("/jobs"):
            return io.BytesIO(
                json.dumps({"jobs": [{"name": "deploy", "conclusion": "success"}]}).encode()
            )
        if "/actions/runs/" in path:
            return io.BytesIO(json.dumps(_workflow_run_payload(90, event="push")).encode())
        return io.BytesIO(json.dumps({"artifacts": artifacts_list}).encode())

    monkeypatch.setattr(reports_restore, "_request", fake_request)
    monkeypatch.setattr(reports_restore, "_open_once", lambda url, token: io.BytesIO(archive))

    _make_run_folder(tmp_path / "in1", "hallway", run_id="921", created="2026-11-21T06:00:00Z")
    with pytest.raises(reports_restore.SiteError, match="Unsafe path"):
        site_action.run_site_action(
            {
                "INPUT_RUNS_DIR": str(tmp_path / "in1"),
                "INPUT_OUTPUT_DIR": "reports",
                "INPUT_RESTORE_HISTORY": "true",
                "INPUT_EXCLUDE_RUNS": "920",
                "INPUT_GITHUB_TOKEN": "tok",
                "GITHUB_WORKSPACE": str(tmp_path),
                "GITHUB_EVENT_NAME": "push",
                "GITHUB_REPOSITORY": REPO,
                "GITHUB_RUN_ID": "921",
            }
        )

    assert not (tmp_path / "reports/index.html").exists()
    assert not (tmp_path / "escape.html").exists()

    outputs = site_action.run_site_action(
        {
            "INPUT_RUNS_DIR": str(tmp_path / "in1"),
            "INPUT_OUTPUT_DIR": "reports",
            "INPUT_RESTORE_HISTORY": "true",
            "INPUT_RESET_HISTORY": "true",
            "INPUT_GITHUB_TOKEN": "tok",
            "GITHUB_WORKSPACE": str(tmp_path),
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_RUN_ID": "921",
        }
    )
    assert outputs["runs-count"] == "1"
    assert (tmp_path / "reports/unitree-g1/stock-mujoco-hallway/runs/921/index.html").is_file()


# ---------------------------------------------------------------- va#32 B1: many unknown scripts


# ---------------------------------------------------------------- va#29 N2 & N3
