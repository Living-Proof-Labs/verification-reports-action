# SPDX-License-Identifier: Apache-2.0
"""The public reports index is the G1 example, and fixture smoke is refused."""

from pathlib import Path

import pytest

import reports_site as site
import require_reports
import site_fixture

BADGE = '<span class="badge badge-fail">Fail</span>'


def test_public_index_links_g1_paths(tmp_path: Path):
    runs = tmp_path / "runs"
    site_fixture.write(runs, repository="Living-Proof-Labs/livingproof-unitree-g1")
    reports = tmp_path / "reports"
    site.build(reports / "g1", runs, now="2026-11-25T00:00:00Z")
    require_reports.publish_public_index(reports)
    text = (reports / "index.html").read_text(encoding="utf-8")
    assert "unitree-g1" in text
    assert require_reports.GRADE_BADGE.search(text)
    assert 'href="g1/unitree-g1/stock-mujoco-hallway/latest/"' in text
    assert 'href="unitree-g1/' not in text
    assert "fixture-scenario-stock" not in text
    assert "fixture-hallway-stock" not in text
    assert "verification-reports-action" not in text
    require_reports.require_published(reports, "success")


def test_public_index_rejects_fixture_paths(tmp_path: Path):
    reports = _write_publishable_reports(tmp_path / "reports")
    index = reports / "index.html"
    original = index.read_text(encoding="utf-8")
    for name in ("fixture-scenario-stock", "fixture-hallway-stock"):
        index.write_text(original + name, encoding="utf-8")
        with pytest.raises(SystemExit, match=f"public index lists {name}"):
            require_reports.require_published(reports, "success")
        index.write_text(original, encoding="utf-8")
        folder = reports / "verification-reports-action" / name / "runs" / "1"
        folder.mkdir(parents=True)
        with pytest.raises(SystemExit, match="fixture path"):
            require_reports.require_published(reports, "success")
        folder.rmdir()
        folder.parent.rmdir()
        folder.parent.parent.rmdir()
        folder.parent.parent.parent.rmdir()


def test_public_index_rejects_fixture_phrase(tmp_path: Path):
    reports = _write_publishable_reports(tmp_path / "reports")
    note = reports / "g1" / "note.html"
    note.write_text("fixture (this repository's fixture; release not checked)", encoding="utf-8")
    with pytest.raises(SystemExit, match="fixture phrase"):
        require_reports.require_published(reports, "success")
    note.unlink()
    require_reports.require_published(reports, "success")


def test_public_index_requires_g1_report(tmp_path: Path):
    reports = _write_publishable_reports(tmp_path / "reports")
    index = reports / "index.html"
    original = index.read_text(encoding="utf-8")
    index.write_text(original.replace("unitree-g1", "other-robot"), encoding="utf-8")
    with pytest.raises(SystemExit, match="missing the G1 report"):
        require_reports.require_published(reports, "success")
    index.write_text(original.replace(BADGE, "grade"), encoding="utf-8")
    with pytest.raises(SystemExit, match="missing the G1 report"):
        require_reports.require_published(reports, "success")
    index.write_text(original, encoding="utf-8")
    with pytest.raises(SystemExit, match="refused site build"):
        require_reports.require_published(reports, "failure")


def test_public_index_requires_stable_g1_page(tmp_path: Path):
    reports = _write_publishable_reports(tmp_path / "reports")
    page = reports / "g1/unitree-g1/stock-mujoco-hallway/latest/index.html"
    page.unlink()
    with pytest.raises(SystemExit, match="built site is missing"):
        require_reports.require_published(reports, "success")


def test_prefix_g1_links_leaves_root_absolute_and_data_urls():
    text = (
        '<a href="/overview">home</a>'
        '<img src="data:image/gif;base64,AAAA">'
        '<a href="unitree-g1/latest/">hall</a>'
    )
    rewritten = require_reports._prefix_g1_links(text)
    assert 'href="/overview"' in rewritten
    assert 'src="data:image/gif;base64,AAAA"' in rewritten
    assert 'href="g1/unitree-g1/latest/"' in rewritten
    assert "g1//overview" not in rewritten
    assert "g1/data:" not in rewritten


def test_publish_index_drops_mock_root_files(tmp_path: Path):
    reports = tmp_path / "reports"
    g1 = reports / "g1"
    g1.mkdir(parents=True)
    (g1 / "index.html").write_text("<h1>unitree-g1</h1>", encoding="utf-8")
    (g1 / "styles.css").write_text("g1", encoding="utf-8")
    (g1 / "open.js").write_text("g1", encoding="utf-8")
    (reports / "site.json").write_text(
        '{"robot": "verification-reports-action", "scenarios": 2, "runs": 6}\n',
        encoding="utf-8",
    )
    (reports / "styles.css").write_text("mock", encoding="utf-8")
    (reports / "open.js").write_text("mock", encoding="utf-8")
    require_reports.publish_public_index(reports)
    assert not (reports / "site.json").exists()
    assert not (reports / "styles.css").exists()
    assert not (reports / "open.js").exists()
    assert (g1 / "styles.css").read_text(encoding="utf-8") == "g1"
    assert (g1 / "open.js").read_text(encoding="utf-8") == "g1"


def test_public_index_accepts_g1_without_the_mock_report(tmp_path: Path):
    reports = _write_publishable_reports(tmp_path / "reports")
    text = (reports / "index.html").read_text(encoding="utf-8")
    assert "verification-reports-action" not in text
    require_reports.require_published(reports, "success")


def test_drop_published_fixtures_removes_fixture_smoke(tmp_path: Path):
    reports = _write_publishable_reports(tmp_path / "reports")
    for name in ("fixture-scenario-stock", "fixture-hallway-stock"):
        run = reports / "verification-reports-action" / name / "runs" / "9"
        run.mkdir(parents=True)
        (run / "report.html").write_text(
            "fixture (this repository's fixture; release not checked)",
            encoding="utf-8",
        )
        (run / "trial.mp4").write_bytes(b"mp4-bytes")
    stray = reports / "stray.html"
    stray.write_text("this repository's fixture", encoding="utf-8")
    with pytest.raises(SystemExit, match="fixture"):
        require_reports.require_published(reports, "success")
    require_reports.drop_published_fixtures(reports)
    assert not (reports / "verification-reports-action").exists()
    assert not stray.exists()
    require_reports.require_published(reports, "success")


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
    require_reports.require_readme_sample(reports.parent, repo / "README.md")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("G1_STATUS", "success")
    require_reports.main(["require_reports.py", "check", str(reports)])


def test_readme_run_id_link_is_refused(tmp_path: Path):
    site_root = tmp_path / "site"
    page = site_root / "reports/g1/unitree-g1/stock-mujoco-hallway/latest/index.html"
    page.parent.mkdir(parents=True)
    page.write_text("latest hallway", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(
        "https://docs.livingprooflabs.com/verification-reports-action/"
        "reports/g1/unitree-g1/stock-mujoco-hallway/runs/46062882502/\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="run id"):
        require_reports.require_readme_sample(site_root, readme)
    readme.write_text(
        "https://docs.livingprooflabs.com/verification-reports-action/"
        "reports/g1/unitree-g1/stock-mujoco-hallway/latest/\n",
        encoding="utf-8",
    )
    page.unlink()
    with pytest.raises(SystemExit, match="built site is missing"):
        require_reports.require_readme_sample(site_root, readme)


def _write_publishable_reports(reports: Path) -> Path:
    latest = reports / "g1/unitree-g1/stock-mujoco-hallway/latest"
    latest.mkdir(parents=True)
    (latest / "index.html").write_text("latest hallway", encoding="utf-8")
    (reports / "g1" / "index.html").write_text(f"unitree-g1 {BADGE}", encoding="utf-8")
    (reports / "index.html").write_text(
        "<h1>Verification reports</h1>"
        f"unitree-g1 {BADGE}"
        '<a href="g1/unitree-g1/stock-mujoco-hallway/latest/">hallway</a>',
        encoding="utf-8",
    )
    return reports


RUN = "https://github.com/Living-Proof-Labs/verification-reports-action/actions/runs/46110994356"


def _write_run(reports: Path, run_id: str, created: str, page: str, files=()) -> Path:
    folder = reports / "g1/unitree-g1/stock-mujoco-hallway/runs" / run_id
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(
        f'{{"run": {{"id": "{run_id}", "created": "{created}"}}}}\n', encoding="utf-8"
    )
    (folder / "index.html").write_text(page, encoding="utf-8")
    for name in files:
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"x")
    return folder


LINKED_PAGE = (
    f"<ul><li><a href='{RUN}'>CI run</a></li>"
    "<li><a href='evidence/mcap/mcap/trial-01.mcap'>MCAP, all 1 trials</a></li>"
    "<li class='evidence-video'><video controls src='evidence/video/trial-01-fail.mp4'></video>"
    "</li></ul><table><tr><td><a href='evidence/video/trial-01-fail.mp4'>video</a></td></tr>"
    "</table>"
)
MEDIA = ("evidence/video/trial-01-fail.mp4", "evidence/mcap/mcap/trial-01.mcap")


def test_the_newest_run_page_must_link_the_ci_run(tmp_path: Path):
    reports = _write_publishable_reports(tmp_path / "reports")
    _write_run(reports, "2", "2026-11-28T05:19:19Z", LINKED_PAGE.replace(RUN, "#"), MEDIA)
    with pytest.raises(SystemExit, match="CI run link"):
        require_reports.require_published(reports, "success")
    with pytest.raises(SystemExit, match="CI run link"):
        require_reports.require_g1_build(reports, "success")


def test_the_newest_run_page_must_link_the_video_and_recording_it_published(tmp_path: Path):
    reports = _write_publishable_reports(tmp_path / "reports")
    page = LINKED_PAGE.replace("evidence/video/trial-01-fail.mp4", "#")
    folder = _write_run(reports, "2", "2026-11-28T05:19:19Z", page, MEDIA)
    with pytest.raises(SystemExit, match="evidence/video/trial-01-fail.mp4"):
        require_reports.require_published(reports, "success")
    (folder / "index.html").write_text(
        LINKED_PAGE.replace("evidence/mcap/mcap/trial-01.mcap", "#"), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="evidence/mcap/mcap/trial-01.mcap"):
        require_reports.require_g1_build(reports, "success")
    (folder / "index.html").write_text(LINKED_PAGE, encoding="utf-8")
    require_reports.require_published(reports, "success")
    require_reports.require_g1_build(reports, "success")


def test_a_run_without_published_media_only_needs_the_ci_run_link(tmp_path: Path):
    reports = _write_publishable_reports(tmp_path / "reports")
    _write_run(reports, "2", "2026-11-28T05:19:19Z", f"<a href='{RUN}'>CI run</a>")
    require_reports.require_published(reports, "success")


def test_only_the_newest_run_is_held_to_the_link_check(tmp_path: Path):
    reports = _write_publishable_reports(tmp_path / "reports")
    _write_run(reports, "1", "2026-11-27T05:19:19Z", "plain text", MEDIA)
    _write_run(reports, "2", "2026-11-28T05:19:19Z", LINKED_PAGE, MEDIA)
    require_reports.require_published(reports, "success")
