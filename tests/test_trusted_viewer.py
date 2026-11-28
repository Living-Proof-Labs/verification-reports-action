# SPDX-License-Identifier: Apache-2.0
import copy
import hashlib
import json

import pytest

from reports_common import SiteError
from reports_render import checked_report_html
from trusted_viewer import install, revalidate


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def case(tmp_path):
    data = b'{"trials":[]}'
    viewer = b"<!doctype html><script>/* reviewed caller code */</script>"
    advisory = {
        "evaluator": {"id": "generic-example"},
        "advisory": {
            "details_file": {"uri": "recording.json", "sha256": sha(data)},
            "trials": [{"index": 1, "observation_sha256": "a" * 64}],
        },
    }
    report = {
        "source_bundle": {
            "results": [
                {
                    "id": "trial-one",
                    "system": {"id": "robot/stock"},
                    "provenance": {"run_id": "recorded-run"},
                    "evidence": [{"sha256": "a" * 64}],
                }
            ]
        },
        "advisories": [advisory],
        "grade": "FAIL",
        "assessments": [{"evaluator": "named-check"}],
    }
    raw = json.dumps(report).encode()
    (tmp_path / "report.json").write_bytes(raw)
    (tmp_path / "recording.json").write_bytes(data)
    (tmp_path / "index.html").write_text(
        "<html><body><p>FAIL</p><a href='recording.json'>Recorded data</a></body></html>"
    )
    pin = {
        "viewer_sha256": sha(viewer),
        "report_sha256": sha(raw),
        "viewer_commit": "b" * 40,
        "viewer_tag": "v1.0.0",
        "result_id": "trial-one",
        "run": "recorded-run",
        "system": "robot/stock",
        "configuration": "stock",
        "trial": 1,
        "evaluator_id": "generic-example",
    }
    return tmp_path, pin, viewer


def test_install_restore_preserves_report_data_and_grade(case):
    folder, pin, viewer = case
    original = (folder / "index.html").read_text()
    raw = (folder / "report.json").read_bytes()
    data = (folder / "recording.json").read_bytes()
    install(folder, pin, viewer)
    assert revalidate(folder, pin) == original
    assert (folder / "report.json").read_bytes() == raw
    assert (folder / "recording.json").read_bytes() == data
    assert "Recorded data" in (folder / "index.html").read_text()
    with pytest.raises(SiteError):
        checked_report_html((folder / "index.html").read_text(), "untrusted input")


@pytest.mark.parametrize(
    "key,value",
    [
        ("viewer_sha256", "0" * 64),
        ("report_sha256", "0" * 64),
        ("trial", 2),
        ("run", "other"),
        ("configuration", "other"),
    ],
)
def test_invalid_pin_refused(case, key, value):
    folder, pin, viewer = case
    altered = copy.deepcopy(pin)
    altered[key] = value
    with pytest.raises(SiteError):
        install(folder, altered, viewer)
    assert not (folder / "trusted-viewer").exists()


@pytest.mark.parametrize("target", ["viewer.html", "data/recording.json", "sanitized-source.html"])
def test_restored_tampering_refused(case, target):
    folder, pin, viewer = case
    install(folder, pin, viewer)
    (folder / "trusted-viewer" / target).write_text("tampered")
    with pytest.raises(SiteError):
        revalidate(folder, pin)


def test_incoming_iframe_never_accepted(case):
    folder, pin, viewer = case
    (folder / "index.html").write_text('<iframe src="evil.html"></iframe>')
    with pytest.raises(SiteError):
        install(folder, pin, viewer)


def test_wrong_report_cited_bytes_refused(case):
    folder, pin, viewer = case
    (folder / "recording.json").write_text("{}")
    with pytest.raises(SiteError):
        install(folder, pin, viewer)


def test_extra_executable_and_changed_iframe_refused(case):
    folder, pin, viewer = case
    install(folder, pin, viewer)
    extra = folder / "trusted-viewer/extra.js"
    extra.write_text("alert(1)")
    with pytest.raises(SiteError):
        revalidate(folder, pin)
    extra.unlink()
    p = folder / "index.html"
    p.write_text(
        p.read_text().replace(
            "allow-scripts allow-same-origin", "allow-scripts allow-top-navigation"
        )
    )
    with pytest.raises(SiteError):
        revalidate(folder, pin)
