"""Successive ./site publications with retained recording, no new simulation."""

import json
import os
import shutil
from pathlib import Path

import pytest

import trusted_viewer
from reports_common import SiteError
from reports_site import write_published_checksums
from site_action import run_site_action


def snap(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


@pytest.fixture
def publication(tmp_path, monkeypatch):
    fixture = os.environ.get("VIEWER_SITE_FIXTURE")
    asset = os.environ.get("VIEWER_RELEASE_ASSET")
    report = os.environ.get("VIEWER_REPORT_FIXTURE")
    if not all((fixture, asset, report)):
        pytest.skip("explicit verified local G1 fixture required")
    declaration = json.loads(
        (Path(__file__).parents[1] / "config/viewers/evaluators.json").read_text()
    )
    declaration[0]["bytes"] = Path(asset).read_bytes()
    monkeypatch.setattr(trusted_viewer, "load_viewers", lambda path: declaration if path else [])

    def publish(number, date, enabled=True):
        inputs = tmp_path / f"input-{number}" / "run"
        shutil.copytree(fixture, inputs)
        shutil.copyfile(Path(report) / "regenerated-report.json", inputs / "report.json")
        record = json.loads((inputs / "run.json").read_bytes())
        record["run"]["id"] = str(number)
        record["run"]["created"] = date
        (inputs / "run.json").write_text(json.dumps(record))
        write_published_checksums(inputs)
        run_site_action(
            dict(
                GITHUB_WORKSPACE=str(tmp_path),
                INPUT_RUNS_DIR=str(inputs.parent),
                INPUT_OUTPUT_DIR="published",
                INPUT_TRUSTED_VIEWERS="test" if enabled else "",
                INPUT_RESTORE_HISTORY="false",
                INPUT_UPLOAD_ARTIFACT="false",
                GITHUB_EVENT_NAME="pull_request",
                GITHUB_RUN_ID=str(number),
            )
        )
        return tmp_path / f"published/unitree-g1/stock-mujoco-hallway/runs/{number}"

    return tmp_path, publish


def test_successive_publications_and_normal_media_expiry(publication):
    root, publish = publication
    first = publish(1, "2026-11-28T06:00:00Z")
    before = snap(first)
    second = publish(2, "2026-11-29T06:00:00Z")
    assert snap(first) == before
    assert "<iframe " in (second / "index.html").read_text()
    assert 'id="pinned-inputs"' in (second / "index.html").read_text()
    third = publish(3, "2027-02-01T06:00:00Z")
    assert third.is_dir()
    assert (first / "health.json").read_bytes() == before["health.json"]
    assert (first / "trusted-viewer/data/recording.json").read_bytes() == before["health.json"]
    assert "<iframe " in (first / "index.html").read_text()
    assert "Recorded data" in (first / "index.html").read_text()
    assert json.loads((first / "report.json").read_bytes())["summaries"][0]["grade"] == "FAIL"
    assert not list((first / "evidence").rglob("*.mcap"))
    # No hidden media-specific re-signing: restored manifest and binding verify again.
    trusted_viewer.validate_history(root / "published", trusted_viewer.load_viewers("test"))


def test_missing_expired_payload_refuses_before_publication(publication):
    root, publish = publication
    first = publish(1, "2026-11-28T06:00:00Z")
    (first / "health.json").unlink()
    write_published_checksums(first)  # Even a complete manifest cannot authorize missing data.
    before = snap(first)
    with pytest.raises((SiteError, ValueError)):
        publish(2, "2027-02-01T06:00:00Z")
    assert snap(first) == before
    assert not (root / "published/unitree-g1/stock-mujoco-hallway/runs/2").exists()


def test_no_declaration_leaves_fresh_report_without_player(publication):
    _, publish = publication
    first = publish(1, "2026-11-28T06:00:00Z", False)
    assert "<iframe " not in (first / "index.html").read_text()
    assert not (first / "trusted-viewer").exists()


def test_extra_executable_refuses_before_publication(publication):
    root, publish = publication
    first = publish(1, "2026-11-28T06:00:00Z")
    (first / "trusted-viewer/extra.html").write_text("<script>untrusted</script>")
    write_published_checksums(first)
    before = snap(first)
    with pytest.raises(SiteError, match="Unexpected file"):
        publish(2, "2026-11-29T06:00:00Z")
    assert snap(first) == before
    assert not (root / "published/unitree-g1/stock-mujoco-hallway/runs/2").exists()
