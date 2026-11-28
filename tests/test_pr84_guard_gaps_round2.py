# SPDX-License-Identifier: Apache-2.0
"""Guards on the #84 run.url, encoded-actions and video-player logic that mutation left unpinned."""

import json
from pathlib import Path

import pytest

import reports_site as site
import require_reports
import site_fixture

OWNER = "Living-Proof-Labs/verification-reports-action"
RUN_ID = "46115314157"
RUN = f"https://github.com/{OWNER}/actions/runs/{RUN_ID}"
VIDEO = "evidence/video/trial-01-fail.mp4"
MCAP = "evidence/mcap/mcap/trial-01.mcap"
CELL = f"<table><tr><td><a href='{VIDEO}'>video</a></td></tr></table>"
PLAYER = f"<video controls src='{VIDEO}'></video>"


def _run_folder(reports: Path, run_id: str, url, created: str = "2026-11-28T05:19:19Z") -> Path:
    folder = reports / f"g1/unitree-g1/stock-mujoco-hallway/runs/{run_id}"
    folder.mkdir(parents=True)
    run = {"id": run_id, "created": created}
    if url is not None:
        run["url"] = url
    (folder / "run.json").write_text(json.dumps({"run": run}), encoding="utf-8")
    return folder


def _publish_media(folder: Path) -> None:
    for name in (VIDEO, MCAP):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"x")


def _page(folder: Path, body: str) -> None:
    html = f"<a href='{RUN}'>CI run</a><a href='{MCAP}'>MCAP</a>{body}"
    (folder / "index.html").write_text(html, encoding="utf-8")


def _problems(reports: Path) -> list[str]:
    return require_reports.run_page_problems(reports, repository=OWNER)


# --- encoded /actions/ in an href: every layer is decoded ---------------------------

ENCODED = [
    "https://github.com/evil/repo/%2561ctions/runs/1",
    "https://github.com/evil/repo/&amp;#97;ctions/runs/1",
    "https://github.com/evil/repo/&amp;#x61;ctions/runs/1",
    "https://github.com/evil/repo/%26%2397;ctions/runs/1",
    "https://github.com/evil/repo/%2526%252397;ctions/runs/1",
    "https://github.com/evil/repo%2Factions%2Fruns%2F1",
    "https://github.com/evil/repo%252Factions%252Fruns%252F1",
    "https://github.com/evil/repo/%41CTIONS/runs/1",
]


@pytest.mark.parametrize("href", ENCODED)
def test_a_multiply_encoded_actions_href_is_unwrapped_and_flagged(href: str):
    raw = f"<a href='{href}'>run</a>"
    assert site.mentions_actions(raw, repository=OWNER)
    assert site.strip_actions_text(raw, repository=OWNER) == "run"


@pytest.mark.parametrize("href", ENCODED)
def test_the_reporter_page_scrub_unwraps_an_encoded_actions_href(href: str):
    raw = f"<p><a href='{href}'>run</a></p>"
    cleaned = site.scrub_reporter_html(
        raw, {"evidence": []}, trial=False, expired=False, days=90, repository=OWNER
    )
    assert "<a " not in cleaned
    assert "evil/repo" not in cleaned
    assert not site.mentions_actions(cleaned, repository=OWNER)


@pytest.mark.parametrize(
    "href",
    [
        "https://github.com/evil/repo/ACTIONS/runs/1",
        "https://github.com/evil/repo/Actions/runs/1/job/2",
    ],
)
def test_an_upper_case_actions_href_is_not_a_kept_link(href: str):
    raw = f"<a href='{href}'>run</a>"
    assert site.mentions_actions(raw, repository=OWNER)
    assert site.strip_actions_text(raw, repository=OWNER) == "run"


def test_an_upper_case_anchor_tag_with_an_encoded_actions_href_is_unwrapped():
    raw = "<A HREF='https://github.com/evil/repo/%61ctions/runs/1'>run</A>"
    assert site.mentions_actions(raw, repository=OWNER)
    assert site.strip_actions_text(raw, repository=OWNER) == "run"


@pytest.mark.parametrize(
    "text",
    [
        "HTTPS://GITHUB.COM/evil/repo/actions/runs/1",
        "https://GitHub.com/evil/repo/actions/runs/1",
        "https://github.com/evil/repo/ACTIONS/runs/1",
    ],
)
def test_a_case_variant_of_the_host_scheme_or_actions_in_plain_text_is_stripped(text: str):
    cleaned = site.strip_actions_text(f"<p>see {text} now</p>", repository=OWNER)
    assert cleaned == "<p>see  now</p>"


# --- the gate: CI link, video player, video cell -------------------------------------


@pytest.mark.parametrize(
    "link",
    [
        "https://github.com/living-proof-labs/verification-reports-action/actions/runs/1",
        "https://github.com/Living-Proof-Labs/VERIFICATION-REPORTS-ACTION/actions/runs/1",
    ],
)
def test_the_gate_ci_link_must_match_the_caller_case_exactly(tmp_path: Path, link: str):
    reports = tmp_path / "reports"
    folder = _run_folder(reports, "1", f"https://github.com/{OWNER}/actions/runs/1")
    (folder / "index.html").write_text(f"<a href='{link}'>CI run</a>", encoding="utf-8")
    assert any("CI run link" in item for item in _problems(reports))
    own = f"https://github.com/{OWNER}/actions/runs/1"
    (folder / "index.html").write_text(f"<a href='{own}'>CI run</a>", encoding="utf-8")
    assert _problems(reports) == []


def test_the_gate_ci_link_treats_a_dot_in_the_repository_name_literally(tmp_path: Path):
    caller = "acme/my.repo"
    reports = tmp_path / "reports"
    folder = _run_folder(reports, "1", f"https://github.com/{caller}/actions/runs/1")
    (folder / "index.html").write_text(
        "<a href='https://github.com/acme/myXrepo/actions/runs/1'>CI run</a>", encoding="utf-8"
    )
    problems = require_reports.run_page_problems(reports, repository=caller)
    assert any("CI run link" in item for item in problems)
    (folder / "index.html").write_text(
        f"<a href='https://github.com/{caller}/actions/runs/1'>CI run</a>", encoding="utf-8"
    )
    assert require_reports.run_page_problems(reports, repository=caller) == []


def _video_folder(tmp_path: Path) -> tuple[Path, Path]:
    reports = tmp_path / "reports"
    folder = _run_folder(reports, RUN_ID, RUN)
    _publish_media(folder)
    return reports, folder


@pytest.mark.parametrize(
    "player",
    [
        "<video controls src='evidence/video/other.mp4'></video>",
        "<video controls></video>",
        "<video controls src='evidence/vid'></video>",
        f"<video controls src='{VIDEO.replace('.mp4', 'Xmp4')}'></video>",
        f"<video controls src='evidence/video/other.mp4'></video><p>{VIDEO}</p>",
        "<video src='evidence/video/a.mp4'></video>"
        f"<p>{VIDEO}</p><video src='evidence/video/b.mp4'></video>",
    ],
)
def test_the_player_must_be_the_published_video_itself(tmp_path: Path, player: str):
    reports, folder = _video_folder(tmp_path)
    _page(folder, CELL + player)
    problems = _problems(reports)
    assert any("video player" in item for item in problems), problems


def test_the_player_is_found_with_any_tag_case_and_attribute_order(tmp_path: Path):
    reports, folder = _video_folder(tmp_path)
    _page(folder, CELL + f"<VIDEO CONTROLS><source SRC='{VIDEO}'></VIDEO>")
    assert _problems(reports) == []


def test_the_cell_must_link_this_video_not_one_that_shares_its_directory_prefix(tmp_path: Path):
    reports, folder = _video_folder(tmp_path)
    other = "<table><tr><td><a href='evidence/video/trial-01-other.mp4'>video</a></td></tr></table>"
    _page(folder, other + PLAYER)
    assert any("trials table" in item for item in _problems(reports))
    _page(folder, CELL + PLAYER)
    assert _problems(reports) == []


# --- run.json: older runs and traversal order ----------------------------------------


def test_an_older_run_without_a_run_url_fails_the_gate_even_when_the_newest_is_fine(
    tmp_path: Path,
):
    reports = tmp_path / "reports"
    newest = _run_folder(reports, "2", f"https://github.com/{OWNER}/actions/runs/2")
    (newest / "index.html").write_text(
        f"<a href='https://github.com/{OWNER}/actions/runs/2'>CI run</a>", encoding="utf-8"
    )
    older = _run_folder(reports, "1", None, created="2026-11-20T00:00:00Z")
    (older / "index.html").write_text("<p>old</p>", encoding="utf-8")
    problems = _problems(reports)
    assert any("runs/1/run.json" in item and "run.url" in item for item in problems), problems


def test_an_unreadable_run_json_does_not_hide_a_later_run_json_without_a_run_url(
    tmp_path: Path,
):
    reports = tmp_path / "reports"
    first = _run_folder(reports, "1", RUN)
    (first / "run.json").write_text("{not json", encoding="utf-8")
    _run_folder(reports, "2", None)
    problems = site.public_page_problems(reports, repository=OWNER)
    assert any("runs/2/run.json run.url is missing" in item for item in problems), problems


# --- the caller repository defaults to GITHUB_REPOSITORY on the command lines --------


def _built_reports(tmp_path: Path, repository: str) -> Path:
    runs = tmp_path / "runs"
    site_fixture.write(runs, repository=repository)
    reports = tmp_path / "reports"
    site.build(reports, runs, repository=repository)
    return reports


def test_site_fixture_check_defaults_to_github_repository(tmp_path: Path, monkeypatch):
    repository = "Living-Proof-Labs/livingproof-unitree-g1"
    reports = _built_reports(tmp_path, repository)
    monkeypatch.setenv("GITHUB_REPOSITORY", repository)
    assert site_fixture.main(["check", str(reports)]) == 0
    monkeypatch.setenv("GITHUB_REPOSITORY", "other-org/other-repo")
    assert site_fixture.main(["check", str(reports)]) == 1
    monkeypatch.delenv("GITHUB_REPOSITORY")
    assert site_fixture.main(["check", str(reports)]) == 1


def test_reports_site_build_defaults_to_github_repository(tmp_path: Path, monkeypatch):
    repository = "Living-Proof-Labs/livingproof-unitree-g1"
    runs = tmp_path / "runs"
    site_fixture.write(runs, repository=repository)
    monkeypatch.setenv("GITHUB_REPOSITORY", "other-org/other-repo")
    assert site.main(["build", str(tmp_path / "bad"), "--runs", str(runs)]) != 0
    monkeypatch.setenv("GITHUB_REPOSITORY", repository)
    assert site.main(["build", str(tmp_path / "good"), "--runs", str(runs)]) == 0
