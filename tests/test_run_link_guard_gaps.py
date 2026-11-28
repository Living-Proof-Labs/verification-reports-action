# SPDX-License-Identifier: Apache-2.0
"""Guards on this-repository run links that mutation left unpinned (follow-up to #79 and #84).

Each test here fails when the guard it names is removed, and the suite was green without it.
"""

import json
from pathlib import Path

import pytest

import reports_site as site
import require_reports

OWNER_REPO = "Living-Proof-Labs/verification-reports-action"
OTHER_REPO = "Other-Org/other-repo"
RUN = f"https://github.com/{OWNER_REPO}/actions/runs/46115314157"
OTHER_RUN = f"https://github.com/{OTHER_REPO}/actions/runs/7"


def _run(reports: Path, name: str, repo: str, run_id: str, page: str) -> Path:
    folder = reports / name / "runs" / run_id
    folder.mkdir(parents=True)
    record = {
        "run": {
            "id": run_id,
            "url": f"https://github.com/{repo}/actions/runs/{run_id}",
            "created": "2026-11-28T05:19:19Z",
        }
    }
    (folder / "run.json").write_text(json.dumps(record), encoding="utf-8")
    (folder / "index.html").write_text(page, encoding="utf-8")
    return folder


def test_a_url_in_evidence_links_to_another_repository_is_dropped():
    report = {
        "evidence_links": {
            "run": RUN,
            "links": [
                {"label": "mine", "kind": "note", "url": RUN},
                {"label": "theirs", "kind": "note", "url": OTHER_RUN},
            ],
        }
    }
    assert site.strip_actions_links(report, repository=OWNER_REPO)
    links = report["evidence_links"]["links"]
    assert links[0]["url"] == RUN
    assert "url" not in links[1]
    assert report["evidence_links"]["run"] == RUN


def test_a_run_page_may_not_link_another_repository_that_is_elsewhere_on_the_site(
    tmp_path: Path,
):
    # Two repositories publish to one site. A run page is held to its own repository,
    # not to every repository on the site.
    reports = tmp_path / "reports"
    _run(reports, "a", OWNER_REPO, "1", f"<a href='{RUN}'>CI run</a>")
    _run(
        reports,
        "b",
        OTHER_REPO,
        "7",
        f"<a href='https://github.com/{OTHER_REPO}/actions/runs/7'>x</a>",
    )
    assert site.public_page_problems(reports) == []
    page = reports / "a/runs/1/index.html"
    page.write_text(f"<a href='{OTHER_RUN}'>other</a>", encoding="utf-8")
    assert site.public_page_problems(reports) == ["a/runs/1/index.html links to /actions/"]


@pytest.mark.parametrize(
    "href",
    [
        f"https://github.com/{OWNER_REPO.lower()}/actions/runs/46115314157",
        f"https://github.com/{OWNER_REPO.upper()}/actions/runs/46115314157",
        "https://github.com/Living-Proof-Labs/Verification-Reports-Action/actions/runs/1",
    ],
)
def test_the_owner_and_repository_are_matched_exactly_not_by_case(href: str):
    # Fails closed: a different spelling of this repository is not kept as a link.
    cleaned = site.strip_actions_text(f"<a href='{href}'>run</a>", repository=OWNER_REPO)
    assert cleaned == "run"


def _page(cell: str) -> str:
    return (
        f"<a href='{RUN}'>CI run</a>"
        "<a href='evidence/mcap/mcap/trial-01.mcap'>MCAP</a>"
        f"<table><tr><td>{cell}</td></tr></table>"
    )


def _folder(reports: Path, page: str) -> None:
    folder = _run(reports, "g1/unitree-g1/stock-mujoco-hallway", OWNER_REPO, "46115314157", page)
    for name in ("evidence/video/trial-01-fail.mp4", "evidence/mcap/mcap/trial-01.mcap"):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"x")


def test_the_trials_table_cell_must_link_the_video_not_another_evidence_file(tmp_path: Path):
    reports = tmp_path / "reports"
    _folder(reports, _page("<a href='evidence/mcap/media.json'>video</a>"))
    assert require_reports.run_page_problems(reports) == [
        "g1/unitree-g1/stock-mujoco-hallway/runs/46115314157/index.html does not link the "
        "published evidence/video/trial-01-fail.mp4 in the trials table"
    ]
    (reports / "g1/unitree-g1/stock-mujoco-hallway/runs/46115314157/index.html").write_text(
        _page("<a href='evidence/video/trial-01-fail.mp4'>video</a>"), encoding="utf-8"
    )
    assert require_reports.run_page_problems(reports) == []


@pytest.mark.parametrize(
    "record",
    [
        "[]",
        "null",
        '"text"',
        "{}",
        '{"run": 5}',
        '{"run": {"id": "1"}}',
        '{"run": {"id": "1", "created": 5}}',
        "{not json",
    ],
)
def test_a_run_json_without_a_created_time_is_unreadable_not_skipped(tmp_path: Path, record: str):
    reports = tmp_path / "reports"
    _folder(reports, _page("<a href='evidence/video/trial-01-fail.mp4'>video</a>"))
    run = reports / "g1/unitree-g1/stock-mujoco-hallway/runs/46115314157"
    (run / "run.json").write_text(record, encoding="utf-8")
    problems = require_reports.run_page_problems(reports)
    assert problems == [
        "g1/unitree-g1/stock-mujoco-hallway/runs/46115314157/run.json is unreadable"
    ]
