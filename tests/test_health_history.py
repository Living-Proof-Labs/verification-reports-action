"""Legacy genuine Health archives survive ingestion/restore without invented peers."""

import io
import json
import shutil
import urllib.parse

import pytest

import reports_restore
import reports_site
import site_action
from test_health_display import checksums, unpack
from test_site import (
    REPO,
    REPO_ID,
    _artifact_entry,
    _pages_zip_from_docs_root,
    _workflow_run_payload,
)


def invoke(tmp_path, inputs, output, restore=False):
    return site_action.run_site_action(
        {
            "INPUT_RUNS_DIR": str(inputs),
            "INPUT_OUTPUT_DIR": str(output),
            "INPUT_RESTORE_HISTORY": str(restore).lower(),
            "INPUT_RESET_HISTORY": "false",
            "GITHUB_WORKSPACE": str(tmp_path),
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_RUN_ID": "99999999999",
        }
    )


def transport(monkeypatch, docs):
    archive = _pages_zip_from_docs_root(docs)

    def request(url, token):
        path = urllib.parse.urlsplit(url).path.rstrip("/")
        if path == f"/repos/{REPO}":
            value = {"id": REPO_ID, "full_name": REPO}
        elif path.endswith("/jobs"):
            value = {"jobs": [{"name": "deploy", "conclusion": "success"}]}
        elif "/actions/runs/" in path:
            value = _workflow_run_payload(90, event="push")
        else:
            value = {"artifacts": [_artifact_entry(5, "main", 90, "2026-11-26T00:00:00Z")]}
        return io.BytesIO(json.dumps(value).encode())

    monkeypatch.setattr(reports_restore, "_request", request)
    monkeypatch.setattr(reports_restore, "_open_once", lambda url, token: io.BytesIO(archive))


def snapshot(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def next_input(tmp_path, source):
    """Synthetic later CI entry using the same recorded bytes; never a new recording."""
    target = tmp_path / "next-input"
    shutil.copytree(source, target)
    record = json.loads((target / "run.json").read_text())
    record["run"]["id"] = "99999999999"
    (target / "run.json").write_text(json.dumps(record))
    checksums(target)
    return target


@pytest.mark.parametrize("health_on", [True, False])
def test_real_site_ingestion_then_real_restore_preserves_legacy_bytes(
    tmp_path, monkeypatch, health_on
):
    source = unpack(tmp_path)
    if not health_on:
        record = json.loads((source / "run.json").read_text())
        record.pop("health")
        (source / "run.json").write_text(json.dumps(record))
        (source / "health.json").unlink()
        checksums(source)
    original = snapshot(source)
    docs = tmp_path / "docs"
    invoke(tmp_path, tmp_path / "input", docs / "reports")
    run = next((docs / "reports").glob("*/*/runs/*"))
    before = snapshot(run)
    assert ('id="health-advisory"' in before["index.html"].decode()) == health_on
    assert "evaluator-livingproof-health" not in before["index.html"].decode()
    assert snapshot(source) == original
    transport(monkeypatch, docs)
    empty = next_input(tmp_path, source)
    invoke(tmp_path, empty, tmp_path / "restored", restore=True)
    after = tmp_path / "restored" / run.relative_to(docs / "reports")
    assert snapshot(after) == before


@pytest.mark.parametrize("change", ["missing", "tampered", "missing-peer"])
def test_restore_refuses_unvalidated_health_displays(tmp_path, monkeypatch, change):
    source = unpack(tmp_path)
    docs = tmp_path / "docs"
    invoke(tmp_path, tmp_path / "input", docs / "reports")
    run = next((docs / "reports").glob("*/*/runs/*"))
    if change == "tampered":
        (run / "health.json").write_text("{}")
    else:
        (run / "health.json").unlink()
        if change == "missing-peer":
            page = run / "index.html"
            page.write_text(
                page.read_text().replace(
                    'id="health-advisory"', "id='evaluator-livingproof-health'"
                )
            )
    transport(monkeypatch, docs)
    empty = next_input(tmp_path, source)
    with pytest.raises(reports_site.SiteError, match="Health"):
        invoke(tmp_path, empty, tmp_path / "restored", restore=True)


def test_declared_but_missing_replay_input_is_not_legacy(tmp_path):
    source = unpack(tmp_path)
    record = json.loads((source / "run.json").read_text())
    record["reporter_input"] = {"format": "livingproof-reporter-input/1"}
    (source / "run.json").write_text(json.dumps(record))
    checksums(source)
    with pytest.raises(reports_site.SiteError, match="original Reporter input"):
        invoke(tmp_path, tmp_path / "input", tmp_path / "reports")


def test_expiry_republishes_without_health_and_never_requires_replay_inputs(tmp_path):
    unpack(tmp_path)
    docs = tmp_path / "docs"
    invoke(tmp_path, tmp_path / "input", docs / "reports")
    reports_site.render(docs / "reports", now="2027-02-01T00:00:00Z")
    run = next((docs / "reports").glob("*/*/runs/*"))
    assert not (run / "health.json").exists()
    assert 'id="health-advisory"' not in (run / "index.html").read_text()
    reports_site.recheck_restored_run(run)
