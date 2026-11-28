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
    import viewer_release

    monkeypatch.setattr(
        viewer_release, "fetch_release_viewer", lambda entry: Path(asset).read_bytes()
    )
    config = Path(__file__).parents[1] / "config/viewers/evaluators.json"
    evaluator_file = tmp_path / "evaluators.yml"

    import copy

    history = copy.deepcopy(declaration)
    history[0]["evaluator"]["version"] = "1.2.0"
    recording = json.loads((Path(report) / "health.json").read_bytes())
    history[0]["approved_trials"] = [recording["trials"][0]["trial"]]
    monkeypatch.setattr(
        trusted_viewer, "load_history_viewers", lambda path: history if path else []
    )

    def publish(number, date, enabled=True, version="1.2.0"):
        line = (
            f"Living-Proof-Labs/livingproof-evaluator-health@v{version}/livingproof-evaluator.json"
        )
        evaluator_file.write_text("- " + line + "\n")
        inputs = tmp_path / f"input-{number}" / "run"
        shutil.copytree(fixture, inputs)
        shutil.copyfile(Path(report) / "regenerated-report.json", inputs / "report.json")
        if version != "1.2.0":
            # Compatible producer-upgrade fixture, not a claimed released version.
            from livingproof_capability_report import render
            from livingproof_capability_report.core import digest

            doc = json.loads((inputs / "report.json").read_bytes())
            assessment = doc["source_bundle"]["assessment"]
            result = assessment["advisory_results"]["livingproof-health"]
            result["evaluator"]["version"] = version
            result["advisory"]["summary"] = "Compatible new sampled metric is visible."
            prov = assessment["advisory_provenance"]["livingproof-health"]
            prov.update(line=line, ref="v" + version, installed_version=version)
            doc["source_bundle_sha256"] = digest(doc["source_bundle"])
            (inputs / "report.json").write_text(json.dumps(doc))
            (inputs / "report.html").write_text(render(doc, "html"))
        record = json.loads((inputs / "run.json").read_bytes())
        record["run"]["id"] = str(number)
        record["run"]["created"] = date
        (inputs / "run.json").write_text(json.dumps(record))
        write_published_checksums(inputs)
        run_site_action(
            dict(
                GITHUB_WORKSPACE=str(tmp_path),
                INPUT_RUNS_DIR=str(inputs.parent),
                INPUT_OUTPUT_DIR=publish.output,
                INPUT_TRUSTED_VIEWERS=str(config) if enabled else "",
                INPUT_EVALUATORS="evaluators.yml",
                INPUT_HISTORICAL_VIEWERS="reviewed-history",
                INPUT_RESTORE_HISTORY="false",
                INPUT_UPLOAD_ARTIFACT="false",
                GITHUB_EVENT_NAME="pull_request",
                GITHUB_RUN_ID=str(number),
            )
            | publish.overrides
        )
        return tmp_path / publish.output / f"unitree-g1/stock-mujoco-hallway/runs/{number}"

    publish.overrides = {}
    publish.output = "published"
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
    trusted_viewer.validate_history(
        root / "published", trusted_viewer.load_history_viewers("reviewed-history")
    )


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


def test_first_embedded_second_undeclared_preserves_history(publication):
    _, publish = publication
    first = publish(1, "2026-11-28T06:00:00Z")
    before = snap(first)
    second = publish(2, "2026-11-29T06:00:00Z", False)
    assert snap(first) == before
    assert "<iframe " not in (second / "index.html").read_text()
    assert "Recorded data" in (second / "index.html").read_text()


@pytest.mark.parametrize("fault", ["unrelated", "wrong-asset", "wrong-trial", "plain-shell"])
def test_unapproved_history_refused_without_mutation(publication, monkeypatch, fault):
    root, publish = publication
    first = publish(1, "2026-11-28T06:00:00Z")
    import copy

    approved = copy.deepcopy(trusted_viewer.load_history_viewers("reviewed-history"))
    if fault == "unrelated":
        approved[0]["evaluator"]["id"] = "unrelated"
    if fault == "wrong-asset":
        approved[0]["viewer_sha256"] = "0" * 64
    if fault == "wrong-trial":
        approved[0]["approved_trials"][0]["run_id"] = "other"
    if fault == "plain-shell":
        (first / "index.html").write_text((first / "report.html").read_text())
        write_published_checksums(first)
    monkeypatch.setattr(trusted_viewer, "load_history_viewers", lambda path: approved)
    before = snap(first)
    with pytest.raises((SiteError, ValueError)):
        publish(2, "2026-11-29T06:00:00Z", False)
    assert snap(first) == before
    assert not (root / "published/unitree-g1/stock-mujoco-hallway/runs/2").exists()


def test_active_selection_cannot_substitute_for_history_approval(publication, monkeypatch):
    root, publish = publication
    first = publish(1, "2026-11-28T06:00:00Z")
    before = snap(first)
    monkeypatch.setattr(trusted_viewer, "load_history_viewers", lambda path: [])
    with pytest.raises(SiteError, match="trusted caller pins"):
        publish(2, "2026-11-29T06:00:00Z", True)
    assert snap(first) == before
    assert not (root / "published/unitree-g1/stock-mujoco-hallway/runs/2").exists()


def test_compatible_metric_upgrade_needs_only_declaration_pin(publication):
    root, publish = publication
    config = Path(__file__).parents[1] / "config/viewers/evaluators.json"
    before_config = config.read_bytes()
    assert "version" not in json.loads(before_config)[0]["evaluator"]
    first = publish(1, "2026-11-28T06:00:00Z")
    before = snap(first)
    second = publish(2, "2026-11-29T06:00:00Z", version="1.2.1")
    assert "@v1.2.1/" in (root / "evaluators.yml").read_text()
    assert config.read_bytes() == before_config
    assert snap(first) == before
    assert "Compatible new sampled metric is visible." in (second / "index.html").read_text()
    assert "<iframe " in (second / "index.html").read_text()
    assert json.loads((second / "report.json").read_bytes())["summaries"][0]["grade"] == "FAIL"
    assert (second / "health.json").read_bytes() == before["health.json"]


def test_later_restore_of_upgrade_requires_external_approval(publication):
    root, publish = publication
    first = publish(1, "2026-11-28T06:00:00Z")
    second = publish(2, "2026-11-29T06:00:00Z", version="1.2.1")
    before_first, before_second = snap(first), snap(second)
    with pytest.raises(SiteError, match="exact evaluator/trial/asset approval"):
        publish(3, "2026-11-30T06:00:00Z", version="1.2.1")
    assert snap(first) == before_first
    assert snap(second) == before_second
    assert not (root / "published/unitree-g1/stock-mujoco-hallway/runs/3").exists()


# Import the mocked authenticated API fixture; production never accepts local logs.
from test_publication_authority import authority_api, log_bytes  # noqa: E402,F401


def _pages_zip(root):
    import io
    import tarfile
    import zipfile

    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w") as stream:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                stream.add(path, arcname="reports/" + path.relative_to(root).as_posix())
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as stream:
        stream.writestr("artifact.tar", tar.getvalue())
    return outer.getvalue()


@pytest.fixture
def authenticated_publication(publication, authority_api, monkeypatch):  # noqa: F811
    import hashlib

    import reports_restore

    root, publish = publication
    publish.output = "published/g1"
    # Production restores reports/ at its parent, then builds reports/g1/. Keep
    # those actual archive/destination shapes and pass authority only in memory.
    import reports_site

    def restore_parent(destination, *args, **kwargs):
        return reports_restore.restore(destination.parent, *args, **kwargs)

    monkeypatch.setattr(reports_site, "restore", restore_parent)
    artifact, _, policy, responses, _ = authority_api
    policy["viewer_registry"] = str(Path(__file__).parents[1] / "config/viewers/evaluators.json")
    policy_file = root / "production-approval.json"
    policy_file.write_text(json.dumps(policy))
    download = {}
    monkeypatch.setattr(reports_restore, "_list_pages_artifacts", lambda *a, **k: ([artifact], ""))
    monkeypatch.setattr(reports_restore, "_repository_id", lambda *a: 9)
    monkeypatch.setattr(
        reports_restore, "_workflow_runs", lambda *a: lambda n: responses[f"/actions/runs/{n}"]
    )
    monkeypatch.setattr(reports_restore, "_conclusion_of", lambda *a, **k: lambda n: "success")
    monkeypatch.setattr(reports_restore, "_download_pages_archive", lambda *a: download["bytes"])
    monkeypatch.setattr(
        reports_restore,
        "_read_following_redirects",
        lambda *a, **k: log_bytes(artifact=artifact["id"]),
    )

    def retain(number):
        # Each call models a new successful production archive, not a mutable receipt.
        previous = root / f"previous-{number}"
        (root / "published").rename(previous)
        download["bytes"] = _pages_zip(previous)
        artifact["digest"] = "sha256:" + hashlib.sha256(download["bytes"]).hexdigest()
        artifact["archive_download_url"] = (
            "https://api.github.com/repos/owner/repo/actions/artifacts/11/zip"
        )
        artifact["created_at"] = "2026-11-29T06:00:00Z"
        old_run = next(
            v for k, v in responses.items() if k.startswith("/actions/runs/") and "jobs" not in k
        )
        job = next(v for k, v in responses.items() if k.startswith("/actions/jobs/"))
        for key in list(responses):
            if key.startswith(("/actions/runs/", "/actions/jobs/", "/actions/artifacts/")):
                del responses[key]
        artifact["id"], old_run["id"], job["id"] = number + 10, number + 20, number + 30
        artifact["workflow_run"]["id"] = old_run["id"]
        job["run_id"] = old_run["id"]
        responses[f"/actions/artifacts/{artifact['id']}"] = artifact
        responses[f"/actions/runs/{old_run['id']}"] = old_run
        responses[f"/actions/runs/{old_run['id']}/attempts/1/jobs"] = {
            "total_count": 1,
            "jobs": [job],
        }
        responses[f"/actions/jobs/{job['id']}"] = job
        publish.overrides.update(
            INPUT_PUBLICATION_APPROVAL=str(policy_file),
            INPUT_RESTORE_HISTORY="true",
            INPUT_HISTORICAL_VIEWERS="",
            GITHUB_REPOSITORY="owner/repo",
            GITHUB_TOKEN="authorized-test-token",
        )
        return previous

    return root, publish, retain, responses, download


def test_authenticated_three_publications_one_pin_upgrade(authenticated_publication):
    root, publish, retain, _, _ = authenticated_publication
    first = publish(1, "2026-11-28T06:00:00Z")
    original = snap(first)
    retain(2)
    second = publish(2, "2026-11-29T06:00:00Z", version="1.2.1")
    upgraded = snap(second)
    assert snap(first) == original
    retain(3)
    third = publish(3, "2026-11-30T06:00:00Z", version="1.2.1")
    assert snap(first) == original
    assert snap(second) == upgraded
    assert "<iframe " in (third / "index.html").read_text()
    assert "Compatible new sampled metric is visible." in (second / "index.html").read_text()
    assert json.loads((third / "report.json").read_bytes())["summaries"][0]["grade"] == "FAIL"
    assert (third / "health.json").read_bytes() == original["health.json"]


@pytest.mark.parametrize(
    "fault",
    [
        "archive",
        "workflow",
        "evidence",
        "unrelated-evidence",
        "unapproved-asset",
        "provenance",
        "unsupported-layout",
    ],
)
def test_authenticated_restore_refuses_without_publishing(authenticated_publication, fault):
    root, publish, retain, responses, download = authenticated_publication
    first = publish(1, "2026-11-28T06:00:00Z")
    if fault == "evidence":
        (first / "health.json").write_bytes(b"modified evidence")
    if fault == "unrelated-evidence":
        unrelated = next(p for p in (first / "evidence").rglob("*") if p.is_file())
        unrelated.write_bytes(unrelated.read_bytes() + b"changed unrelated evidence")
    if fault == "unsupported-layout":
        unexpected = root / "published/unexpected/nested/trusted-viewer"
        unexpected.mkdir(parents=True)
        (unexpected / "viewer.html").write_bytes(b"unexpected")
    if fault == "unapproved-asset":
        (first / "trusted-viewer/viewer.html").write_bytes(b"unapproved code")
        write_published_checksums(first)
    if fault == "provenance":
        doc = json.loads((first / "report.json").read_bytes())
        doc["source_bundle"]["assessment"]["advisory_provenance"]["livingproof-health"]["repo"] = (
            "other/repo"
        )
        (first / "report.json").write_text(json.dumps(doc))
        write_published_checksums(first)
    before = snap(first)
    previous = retain(2)
    if fault == "archive":
        download["bytes"] += b"changed zip digest"
    if fault == "workflow":
        responses["/actions/runs/22"]["path"] = ".github/workflows/unapproved.yml"
    with pytest.raises((SiteError, ValueError)):
        publish(2, "2026-11-29T06:00:00Z", version="1.2.1")
    assert snap(previous / "g1/unitree-g1/stock-mujoco-hallway/runs/1") == before
    assert not list((root / "published").rglob("run.json"))


def test_authenticated_upgrade_then_undeclared_preserves_both(authenticated_publication):
    _, publish, retain, _, _ = authenticated_publication
    first = publish(1, "2026-11-28T06:00:00Z")
    old = snap(first)
    retain(2)
    second = publish(2, "2026-11-29T06:00:00Z", version="1.2.1")
    upgraded = snap(second)
    retain(3)
    third = publish(3, "2026-11-30T06:00:00Z", enabled=False, version="1.2.1")
    assert snap(first) == old and snap(second) == upgraded
    assert "<iframe " not in (third / "index.html").read_text()
    assert "Recorded data" in (third / "index.html").read_text()


def test_authenticated_upgrade_then_expiry_retains_verified_player(authenticated_publication):
    _, publish, retain, _, _ = authenticated_publication
    first = publish(1, "2026-11-28T06:00:00Z")
    original_data = (first / "health.json").read_bytes()
    retain(2)
    second = publish(2, "2026-11-29T06:00:00Z", version="1.2.1")
    retain(3)
    publish(3, "2027-02-01T06:00:00Z", version="1.2.1")
    for folder in [first, second]:
        assert not list((folder / "evidence").rglob("*.mcap"))
        assert (folder / "health.json").read_bytes() == original_data
        assert "<iframe " in (folder / "index.html").read_text()
        assert "Recorded data" in (folder / "index.html").read_text()


def test_undecorated_history_needs_no_publication_authority(authenticated_publication):
    _, publish, retain, responses, _ = authenticated_publication
    first = publish(1, "2026-11-28T06:00:00Z", enabled=False)
    before = snap(first)
    retain(2)
    responses["/actions/runs/22"]["path"] = ".github/workflows/legacy.yml"
    del responses["/actions/artifacts/12"]["digest"]
    second = publish(2, "2026-11-29T06:00:00Z", enabled=False)
    assert snap(first) == before
    assert "<iframe " not in (second / "index.html").read_text()
