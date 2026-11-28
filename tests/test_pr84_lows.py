# SPDX-License-Identifier: Apache-2.0
"""L1-L4 follow-ups on #84: fail closed on run.url, case variants, encoded actions, video player."""

import json
from pathlib import Path

import pytest

import require_reports

OWNER = "Living-Proof-Labs/verification-reports-action"
OTHER = "other-org/other-repo"
RUN = f"https://github.com/{OWNER}/actions/runs/46115314157"
OTHER_RUN = f"https://github.com/{OTHER}/actions/runs/7"


def _record(url) -> dict:
    run = {"id": "46115314157", "created": "2026-11-28T05:19:19Z"}
    if url is not None:
        run["url"] = url
    return {"run": run}


@pytest.mark.parametrize(
    "url",
    [
        None,
        5,
        "https://github.com/other-org/other-repo/actions/runs/7",
        "https://github.com/Living-Proof-Labs/verification-reports-action/actions/runs/1/job/2",
        "https://github.com/Living-Proof-Labs/verification-reports-action/actions/runs/1/",
        "https://github.com/Living-Proof-Labs/verification-reports-action/actions/runs/1?x=1",
        "https://github.com.evil.example/Living-Proof-Labs/verification-reports-action/actions/runs/1",
        "http://github.com/Living-Proof-Labs/verification-reports-action/actions/runs/1",
    ],
)
def test_a_missing_malformed_or_foreign_run_url_fails_the_gate(tmp_path: Path, url):
    reports = tmp_path / "reports"
    scenario = reports / "g1/unitree-g1/stock-mujoco-hallway/runs/1"
    scenario.mkdir(parents=True)
    record = {"run": {"id": "1", "created": "2026-11-28T05:19:19Z"}}
    if url is not None:
        record["run"]["url"] = url
    (scenario / "run.json").write_text(json.dumps(record), encoding="utf-8")
    (scenario / "index.html").write_text(f"<a href='{RUN}'>CI run</a>", encoding="utf-8")
    problems = require_reports.run_page_problems(reports, repository=OWNER)
    assert problems
    assert any("run.url" in item for item in problems)


def test_the_gate_binds_to_the_caller_not_a_foreign_run_url(tmp_path: Path):
    reports = tmp_path / "reports"
    scenario = reports / "g1/unitree-g1/stock-mujoco-hallway/runs/1"
    scenario.mkdir(parents=True)
    (scenario / "run.json").write_text(json.dumps(_record(OTHER_RUN)), encoding="utf-8")
    (scenario / "index.html").write_text(f"<a href='{RUN}'>CI run</a>", encoding="utf-8")
    problems = require_reports.run_page_problems(reports, repository=OWNER)
    assert any("run.url" in item for item in problems)
    # A valid this-repo run.url and this-repo CI link is accepted.
    (scenario / "run.json").write_text(json.dumps(_record(RUN)), encoding="utf-8")
    assert require_reports.run_page_problems(reports, repository=OWNER) == []


def test_a_page_with_only_the_video_cell_is_missing_the_player(tmp_path: Path):
    reports = tmp_path / "reports"
    folder = reports / "g1/unitree-g1/stock-mujoco-hallway/runs/46115314157"
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(json.dumps(_record(RUN)), encoding="utf-8")
    page = (
        f"<a href='{RUN}'>CI run</a>"
        "<a href='evidence/mcap/mcap/trial-01.mcap'>MCAP</a>"
        "<table><tr><td><a href='evidence/video/trial-01-fail.mp4'>video</a></td></tr></table>"
    )
    (folder / "index.html").write_text(page, encoding="utf-8")
    for name in ("evidence/video/trial-01-fail.mp4", "evidence/mcap/mcap/trial-01.mcap"):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"x")
    problems = require_reports.run_page_problems(reports, repository=OWNER)
    assert any("video" in item and "player" in item for item in problems)
    (folder / "index.html").write_text(
        page + "<video controls src='evidence/video/trial-01-fail.mp4'></video>",
        encoding="utf-8",
    )
    assert require_reports.run_page_problems(reports, repository=OWNER) == []
