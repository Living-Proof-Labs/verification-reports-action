# SPDX-License-Identifier: Apache-2.0
"""Exercise the ./site production entry point with the genuine retained G1 bundle."""

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import reports_site

ARCHIVE = Path(__file__).parent / "fixtures/health-production/g1-report.zip"
ROOT = Path(__file__).resolve().parents[1]


def unpack(tmp_path):
    assert hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() == (
        "468a203ff4cfbe3a9e1b37097779694c399a6cd2b2667f1cb578cf038be21cb4"
    )
    with zipfile.ZipFile(ARCHIVE) as archive:
        archive.extractall(tmp_path / "input")
    return next((tmp_path / "input").rglob("run.json")).parent


def checksums(folder):
    reports_site.write_published_checksums(folder)


def run_site(tmp_path, name="site"):
    result = subprocess.run(
        [sys.executable, str(ROOT / "src/site_action.py")],
        env={
            **os.environ,
            "GITHUB_WORKSPACE": str(tmp_path),
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_RUN_ID": "local-display-test",
            "INPUT_RUNS_DIR": str(tmp_path / "input"),
            "INPUT_OUTPUT_DIR": str(tmp_path / name),
            "INPUT_RESET_HISTORY": "true",
            "INPUT_RESTORE_HISTORY": "false",
            "GITHUB_OUTPUT": str(tmp_path / (name + "-outputs")),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result


def test_genuine_production_on_off_through_site_entry_point(tmp_path):
    source = unpack(tmp_path)
    original = {
        str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }
    result = run_site(tmp_path, "on")
    assert result.returncode == 0, result.stderr
    assert "artifact-only=true" in result.stdout
    on = next((tmp_path / "on").glob("*/*/runs/*"))
    page = (on / "index.html").read_text()
    assert "Health observations — advisory" in page
    assert "574 recorded simulator samples" in page
    assert "0.74814 m at 8.46 s" in page
    assert "No robot health telemetry" in page
    assert "not a healthy verdict" in page
    assert (on / "health.json").read_bytes() == original["health.json"]
    assert "health.json" in (on / "SHA256SUMS").read_text()
    for relative, data in original.items():
        assert (source / relative).read_bytes() == data
    record = json.loads((source / "run.json").read_text())
    record.pop("health")
    (source / "run.json").write_text(json.dumps(record))
    (source / "health.json").unlink()
    checksums(source)
    result = run_site(tmp_path, "off")
    assert result.returncode == 0, result.stderr
    off = next((tmp_path / "off").glob("*/*/runs/*"))
    assert "health-advisory" not in (off / "index.html").read_text()
    assert not (off / "health.json").exists()
    for name in reports_site.REPORT_FILES:
        assert (off / name).read_bytes() == (on / name).read_bytes()
    assert (
        json.loads((on / "run.json").read_text())["outcome"]
        == json.loads((off / "run.json").read_text())["outcome"]
    )


@pytest.mark.parametrize(
    "change",
    [
        "digest",
        "missing",
        "unsafe",
        "absolute",
        "url",
        "symlink",
        "trial",
        "source",
        "nan",
        "configuration",
    ],
)
def test_invalid_sidecar_is_never_published(tmp_path, change):
    source = unpack(tmp_path)
    record = json.loads((source / "run.json").read_text())
    sidecar = source / "health.json"
    if change == "digest":
        record["health"]["sha256"] = "0" * 64
    elif change == "missing":
        sidecar.unlink()
    elif change in {"unsafe", "absolute", "url"}:
        record["health"]["path"] = {
            "unsafe": "../health.json",
            "absolute": "/tmp/health.json",
            "url": "https://example.com/health.json",
        }[change]
    elif change == "symlink":
        target = tmp_path / "elsewhere.json"
        sidecar.rename(target)
        sidecar.symlink_to(target)
    elif change == "trial":
        record["health"]["trial"]["index"] = 2
    elif change == "source":
        record["health"]["source"]["repository"] = "Other/evaluator"
    elif change == "configuration":
        record["robot"]["configuration"] = "other"
    elif change == "nan":
        data = json.loads(sidecar.read_text())
        data["simulator"]["minimum_recorded_base_height_by_frame"]["world"]["height_m"] = float(
            "nan"
        )
        sidecar.write_text(json.dumps(data))
        record["health"]["sha256"] = hashlib.sha256(sidecar.read_bytes()).hexdigest()
    (source / "run.json").write_text(json.dumps(record))
    checksums(source)
    result = run_site(tmp_path)
    assert result.returncode != 0
    assert not list((tmp_path / "site").rglob("index.html"))


def test_expired_sidecar_and_old_restored_reference_do_not_display(tmp_path):
    source = unpack(tmp_path)
    out = tmp_path / "expired"
    reports_site.build(out, source, now="2027-01-01T00:00:00Z")
    folder = next(out.glob("*/*/runs/*"))
    assert not (folder / "health.json").exists()
    assert "health-advisory" not in (folder / "index.html").read_text()
    reports_site.recheck_restored_run(folder)


@pytest.mark.parametrize("change", ["manifest", "report-trial", "robot-counts", "frame", "fixture"])
def test_matching_sidecar_digest_does_not_replace_semantic_validation(tmp_path, change):
    source = unpack(tmp_path)
    record = json.loads((source / "run.json").read_text())
    data = json.loads((source / "health.json").read_text())
    if change == "manifest":
        data["run_manifest_sha256"] = "0" * 64
    elif change == "report-trial":
        record["health"]["report_trial"] = "foreign"
    elif change == "robot-counts":
        data["robot"]["message_counts"] = {"/robot": 1}
    elif change == "frame":
        data["simulator"]["minimum_recorded_base_height_by_frame"] = {
            "<script>": {"height_m": 1, "time_ns": "1"}
        }
    elif change == "fixture":
        record["fixture"] = True
    raw = json.dumps(data).encode()
    (source / "health.json").write_bytes(raw)
    record["health"]["sha256"] = hashlib.sha256(raw).hexdigest()
    (source / "run.json").write_text(json.dumps(record))
    checksums(source)
    assert run_site(tmp_path).returncode != 0


def test_unreferenced_sidecar_is_not_published(tmp_path):
    source = unpack(tmp_path)
    record = json.loads((source / "run.json").read_text())
    record.pop("health")
    (source / "run.json").write_text(json.dumps(record))
    checksums(source)
    assert run_site(tmp_path).returncode == 0
    assert not list((tmp_path / "site").rglob("health.json"))


def test_restored_health_tampering_is_refused(tmp_path):
    unpack(tmp_path)
    assert run_site(tmp_path).returncode == 0
    folder = next((tmp_path / "site").glob("*/*/runs/*"))
    (folder / "health.json").write_text("{}")
    with pytest.raises(reports_site.SiteError, match="digest mismatch"):
        reports_site.recheck_restored_run(folder)


def test_restored_unreferenced_sidecar_is_refused(tmp_path):
    unpack(tmp_path)
    assert run_site(tmp_path).returncode == 0
    folder = next((tmp_path / "site").glob("*/*/runs/*"))
    record = json.loads((folder / "run.json").read_text())
    record.pop("health")
    (folder / "run.json").write_text(json.dumps(record))
    with pytest.raises(reports_site.SiteError, match="unreferenced restored"):
        reports_site.recheck_restored_run(folder)


def test_restored_display_without_its_sidecar_is_refused(tmp_path):
    unpack(tmp_path)
    assert run_site(tmp_path).returncode == 0
    folder = next((tmp_path / "site").glob("*/*/runs/*"))
    (folder / "health.json").unlink()
    with pytest.raises(reports_site.SiteError, match="no validated sidecar"):
        reports_site.recheck_restored_run(folder)
