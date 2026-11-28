# SPDX-License-Identifier: Apache-2.0
"""Guards on this-repository run links that mutation left unpinned (follow-up to #79 and #84).

Each test here fails when the guard it names is removed, and the suite was green without it.
"""

import json
from pathlib import Path

import pytest

import require_reports


@pytest.fixture(autouse=True)
def _caller_env(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", OWNER_REPO)


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


def _page(cell: str) -> str:
    return (
        f"<a href='{RUN}'>CI run</a>"
        "<a href='evidence/mcap/mcap/trial-01.mcap'>MCAP</a>"
        "<video controls src='evidence/video/trial-01-fail.mp4'></video>"
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
