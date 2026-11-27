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
    for badge in ('class="badge badge-fail"', 'class="badge badge-pass"'):
        (reports / "index.html").write_text(
            f"unitree-g1 <span {badge}>grade</span>",
            encoding="utf-8",
        )
        with pytest.raises(SystemExit, match="refused site build"):
            require_reports.require_g1_build(reports, "failure")
        require_reports.require_g1_build(reports, "success")
    (reports / "index.html").write_text("unitree-g1", encoding="utf-8")
    with pytest.raises(SystemExit, match="missing the expected report"):
        require_reports.require_g1_build(reports, "success")


def test_readme_sample_link_exists_in_the_built_site(tmp_path: Path, monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    reports = tmp_path / "site" / "reports"
    _write_publishable_reports(reports)
    page = reports / "g1/unitree-g1/stock-mujoco-hallway/latest/index.html"
    page.parent.mkdir(parents=True)
    page.write_text("latest hallway", encoding="utf-8")
    require_reports.require_readme_sample(reports.parent, repo / "README.md")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("MOCK_STATUS", "success")
    monkeypatch.setenv("G1_STATUS", "success")
    require_reports.main(["require_reports.py", "check", str(reports)])


def test_readme_run_id_link_is_refused(tmp_path: Path):
    site = tmp_path / "site"
    page = site / "reports/g1/unitree-g1/stock-mujoco-hallway/latest/index.html"
    page.parent.mkdir(parents=True)
    page.write_text("latest hallway", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(
        "https://docs.livingprooflabs.com/verification-reports-action/"
        "reports/g1/unitree-g1/stock-mujoco-hallway/runs/46062882502/\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="run id"):
        require_reports.require_readme_sample(site, readme)
    readme.write_text(
        "https://docs.livingprooflabs.com/verification-reports-action/"
        "reports/g1/unitree-g1/stock-mujoco-hallway/latest/\n",
        encoding="utf-8",
    )
    page.unlink()
    with pytest.raises(SystemExit, match="built site is missing"):
        require_reports.require_readme_sample(site, readme)


def _write_publishable_reports(reports: Path) -> None:
    (reports / "g1").mkdir(parents=True)
    (reports / "index.html").write_text(
        "<h1>Verification reports</h1>"
        '<p class="sibling-report"><a href="g1/">Unitree G1 report</a></p>'
        "verification-reports-action",
        encoding="utf-8",
    )
    (reports / "g1" / "index.html").write_text("unitree-g1", encoding="utf-8")
