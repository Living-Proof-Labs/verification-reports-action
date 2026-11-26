# SPDX-License-Identifier: Apache-2.0
"""The mock index links to the G1 report, and a refused site build is caught."""

from pathlib import Path

import pytest

import reports_site as site
import require_reports
import site_fixture


def test_mock_index_links_to_g1_report(tmp_path: Path):
    runs = tmp_path / "runs"
    site_fixture.write(runs, repository="Living-Proof-Labs/verification-reports-action")
    reports = tmp_path / "reports"
    site.build(reports, runs, now="2026-11-25T00:00:00Z")
    index = reports / "index.html"
    assert require_reports.G1_HREF not in index.read_text(encoding="utf-8")
    require_reports.link_g1_report(index)
    text = index.read_text(encoding="utf-8")
    assert require_reports.G1_HREF in text
    assert "Unitree G1 report" in text


def test_refused_site_build_is_caught_when_index_exists(tmp_path: Path):
    reports = tmp_path / "reports"
    (reports / "g1").mkdir(parents=True)
    (reports / "index.html").write_text(
        "<h1>Verification reports</h1>"
        '<p class="sibling-report"><a href="g1/">Unitree G1 report</a></p>'
        "verification-reports-action",
        encoding="utf-8",
    )
    (reports / "g1" / "index.html").write_text("unitree-g1", encoding="utf-8")
    with pytest.raises(SystemExit, match="refused site build"):
        require_reports.require_published(reports, "failure", "success")
    require_reports.require_published(reports, "success", "success")
    (reports / "index.html").write_text(
        "<h1>Verification reports</h1>verification-reports-action", encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="does not link"):
        require_reports.require_published(reports, "success", "success")


def test_refused_g1_build_is_caught_when_index_exists(tmp_path: Path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "index.html").write_text("unitree-g1 badge-fail FAIL", encoding="utf-8")
    with pytest.raises(SystemExit, match="refused site build"):
        require_reports.require_g1_build(reports, "failure")
    require_reports.require_g1_build(reports, "success")
    (reports / "index.html").write_text("unitree-g1", encoding="utf-8")
    with pytest.raises(SystemExit, match="missing the expected report"):
        require_reports.require_g1_build(reports, "success")
