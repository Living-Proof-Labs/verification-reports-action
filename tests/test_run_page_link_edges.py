# SPDX-License-Identifier: Apache-2.0
"""Edge cases for the CI run, video and recording links on published run pages (#55 b).

These pin guards that the main tests leave unpinned: each one was found by mutation, a change
that removes the guard and leaves the suite green.
"""

import json
import os
from pathlib import Path

import pytest

import evaluators_run
import require_reports


@pytest.fixture(autouse=True)
def _caller_env(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "Living-Proof-Labs/verification-reports-action")


REPO = "https://github.com/Living-Proof-Labs/verification-reports-action"
CALLER = "Living-Proof-Labs/verification-reports-action"
RUN = f"{REPO}/actions/runs/46115314157"
PAGE = (
    f"<a href='{RUN}'>CI run</a>"
    "<a href='evidence/mcap/mcap/trial-01.mcap'>MCAP</a>"
    "<video controls src='evidence/video/trial-01-fail.mp4'></video>"
)
PAGE_WITH_CELL = (
    PAGE + "<table><tr><td><a href='evidence/video/trial-01-fail.mp4'>video</a></td></tr></table>"
)


def _run_folder(reports: Path, page: str, files: tuple[str, ...]) -> Path:
    folder = reports / "g1/unitree-g1/stock-mujoco-hallway/runs/1"
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(
        json.dumps(
            {
                "run": {
                    "id": "1",
                    "created": "2026-11-28T05:19:19Z",
                    "url": RUN,
                }
            }
        ),
        encoding="utf-8",
    )
    (folder / "index.html").write_text(page, encoding="utf-8")
    for name in files:
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"x")
    return reports


# ------------------------------------------------------------ the newest-run link check


def test_the_robot_side_recording_is_not_a_link_the_page_must_carry(tmp_path: Path):
    # The real run folder holds trial-01.robot.mcap; the reporter never links it.
    reports = _run_folder(
        tmp_path,
        PAGE_WITH_CELL,
        (
            "evidence/video/trial-01-fail.mp4",
            "evidence/mcap/mcap/trial-01.mcap",
            "evidence/mcap/mcap/trial-01.robot.mcap",
        ),
    )
    assert require_reports.run_page_problems(reports) == []


@pytest.mark.parametrize(
    "href",
    [
        "https://github.com/Living-Proof-Labs/verification-reports-action",
        f"{REPO}/actions",
        f"{REPO}/actions/workflows/verify.yml",
        f"{RUN}/job/7",
        f"{RUN}/artifacts/9",
        "https://example.com/org/repo/actions/runs/1",
        "actions/runs/1",
    ],
)
def test_only_a_github_run_address_counts_as_the_ci_run_link(tmp_path: Path, href: str):
    page = PAGE.replace(RUN, href)
    reports = _run_folder(tmp_path, page, ())
    assert require_reports.run_page_problems(reports) == [
        "g1/unitree-g1/stock-mujoco-hallway/runs/1/index.html is missing the CI run link"
    ]


def test_a_ci_run_link_in_text_does_not_count_as_a_link(tmp_path: Path):
    reports = _run_folder(tmp_path, f"<p>{RUN}</p>", ())
    assert require_reports.run_page_problems(reports)


# --------------------------------------------------------------- the URL filter, in anchors


# ------------------------------------------------ evidence the site did not publish: text


@pytest.fixture
def published(tmp_path: Path) -> Path:
    folder = tmp_path / "run"
    (folder / "evidence/video").mkdir(parents=True)
    (folder / "evidence/video/kept.mp4").write_bytes(b"mp4")
    (folder / "report.json").write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"secret")
    try:
        os.symlink(outside, folder / "evidence/video/linked.mp4")
    except OSError:  # pragma: no cover - platforms without symlinks
        pytest.skip("symlinks are not available")
    return folder


# --------------------------------------------------------- the collect job's site paths


def _media(download: Path, *, video_dir: str, video_name: str, mcap_names=("trial-01.mcap",)):
    prefix = "vra-unitree-g1-hallway-"
    video = download / "video" / f"{prefix}{video_dir}"
    video.mkdir(parents=True)
    (video / video_name).write_bytes(b"mp4")
    mcap = download / "mcap" / f"{prefix}mcap-stock" / "mcap"
    mcap.mkdir(parents=True)
    for name in mcap_names:
        (mcap / name).write_bytes(b"mcap")
    return prefix


def _links(tmp_path: Path) -> Path:
    source = tmp_path / "evidence-links.json"
    source.write_text(
        json.dumps(
            {
                "run": RUN,
                "links": [
                    {"label": "MCAP", "url": f"{RUN}/artifacts/1", "kind": "mcap"},
                    {
                        "label": "video",
                        "url": f"{RUN}/artifacts/2",
                        "kind": "video",
                        "seed": 1,
                        "status": "FAIL",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return source


def _resolve(tmp_path: Path, prefix: str) -> list[dict]:
    out = tmp_path / "out"
    out.mkdir()
    resolved = evaluators_run.site_evidence_links(
        _links(tmp_path), tmp_path / "dl", prefix, "stock", out
    )
    return json.loads(resolved.read_text(encoding="utf-8"))["links"]


def test_the_video_is_matched_on_its_status_as_well_as_its_seed(tmp_path: Path):
    # Seed 1 FAILED. A PASS video of seed 1 must not be taken for it.
    prefix = _media(
        tmp_path / "dl", video_dir="video-stock-seed-1-pass", video_name="trial-01-pass.mp4"
    )
    mcap, video = _resolve(tmp_path, prefix)
    assert "path" not in video
    assert mcap["path"] == "evidence/mcap/mcap/trial-01.mcap"


def test_two_candidate_videos_for_one_trial_are_not_guessed_between(tmp_path: Path):
    prefix = _media(
        tmp_path / "dl", video_dir="video-stock-seed-1-fail", video_name="trial-01-fail.mp4"
    )
    (tmp_path / "dl/video" / f"{prefix}video-stock-seed-01-fail").mkdir()
    (tmp_path / "dl/video" / f"{prefix}video-stock-seed-01-fail/other.mp4").write_bytes(b"x")
    assert "path" not in _resolve(tmp_path, prefix)[1]


@pytest.mark.parametrize(
    "name",
    [
        "my artifact.mp4",
        "artifact-1.mp4",
        "ARTIFACTS.mp4",
        "a'b c.mp4",
        "a<b>.mp4",
        "x" * 240 + ".mp4",
    ],
)
def test_a_video_the_page_must_not_point_at_gets_no_site_path(tmp_path: Path, name: str):
    prefix = _media(tmp_path / "dl", video_dir="video-stock-seed-1-fail", video_name=name)
    assert "path" not in _resolve(tmp_path, prefix)[1]


# ------------------------------------------------ L2: only this repository's run URLs


OTHER = "https://github.com/other-org/other-repo/actions/runs/46115314157"


# -------------------------------- L3: near-miss addresses are end-of-address misses


# -------------------------------- L5: the trials-table video cell, and unreadable run.json


def test_a_page_with_only_the_video_tag_is_missing_the_cell_link(tmp_path: Path):
    reports = _run_folder(
        tmp_path,
        PAGE,
        ("evidence/video/trial-01-fail.mp4", "evidence/mcap/mcap/trial-01.mcap"),
    )
    assert require_reports.run_page_problems(reports) == [
        "g1/unitree-g1/stock-mujoco-hallway/runs/1/index.html does not link the published "
        "evidence/video/trial-01-fail.mp4 in the trials table"
    ]


def test_an_unreadable_run_json_is_a_problem_not_a_skip(tmp_path: Path):
    reports = _run_folder(
        tmp_path,
        PAGE.replace(
            "<video controls src='evidence/video/trial-01-fail.mp4'></video>",
            "<video controls src='evidence/video/trial-01-fail.mp4'></video>"
            "<table><tr><td><a href='evidence/video/trial-01-fail.mp4'>video</a></td></tr></table>",
        ),
        ("evidence/video/trial-01-fail.mp4", "evidence/mcap/mcap/trial-01.mcap"),
    )
    (reports / "g1/unitree-g1/stock-mujoco-hallway/runs/1/run.json").write_text(
        "not-json", encoding="utf-8"
    )
    problems = require_reports.run_page_problems(reports)
    assert any("run.json" in item and "unreadable" in item for item in problems), problems
