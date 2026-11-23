# SPDX-License-Identifier: Apache-2.0
"""Tests for single-robot verification reports (`src/reports_site.py` and `src/site_action.py`)."""

from __future__ import annotations

import email.message
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import pytest

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

    monkeypatch.setattr(site_tool, "_request", fake_request)
    monkeypatch.setattr(site_tool, "_open_once", fake_open_once)

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

    monkeypatch.setattr(site_tool, "_request", fake_request)
    monkeypatch.setattr(site_tool, "_open_once", lambda url, token: io.BytesIO(archive))

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
    assert site_tool._github_id(True) is None
    fork_artifact = _artifact_entry(1, "main", 10, "2026-11-20T00:00:00Z", head_repo_id=999999)
    assert not site_tool._listed_run_is_this_repository(fork_artifact, REPO_ID)
    pr_run = _workflow_run_payload(11, event="pull_request")
    assert not site_tool._fetched_run_is_pages_deploy(pr_run, REPO_ID, "main")

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

    monkeypatch.setattr(site_tool, "_request", fake_expired_req)
    with pytest.raises(site_tool.SiteError, match="expired"):
        site_tool.restore(tmp_path / "exp", REPO, "main", 200, "tok", site_url=SITE_URL)
    assert (
        site_tool.restore(
            tmp_path / "exp", REPO, "main", 200, "tok", site_url=SITE_URL, reset_history=True
        )
        is None
    )

    # 5. Live site check probes reports/site.json and reports/index.html, not root index.html.
    monkeypatch.setattr(site_tool, "_request", lambda u, t: io.BytesIO(b'{"artifacts": []}'))
    probed: list[str] = []

    def fake_status_404(url: str) -> int:
        probed.append(url)
        return 404

    monkeypatch.setattr(site_tool, "_fetch_status", fake_status_404)
    assert site_tool.restore(tmp_path / "fresh", REPO, "main", 1, "tok", site_url=SITE_URL) is None
    assert probed == [SITE_URL + "reports/site.json", SITE_URL + "reports/index.html"]

    monkeypatch.setattr(site_tool, "_fetch_status", lambda url: 200)
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

    monkeypatch.setattr(site_tool, "_request", fake_request)
    monkeypatch.setattr(site_tool, "_open_once", fake_open)
    monkeypatch.setattr(site_tool.time, "sleep", sleeps.append)
    monkeypatch.setattr(site_tool.random, "uniform", lambda _a, _b: 1.0)

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
