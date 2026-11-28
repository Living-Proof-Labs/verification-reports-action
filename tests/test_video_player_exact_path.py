# SPDX-License-Identifier: Apache-2.0
"""PR 84 Low 2: the deployed-Pages gate's player check matches the published video path exactly.

`_has_video_player` looked for the path as a substring inside <video>…</video>, so a player
whose src was `….mp4x`, or that only mentioned the path in another attribute or in text,
passed the gate.
"""

import pytest

import require_reports
from test_pr84_guard_gaps_round2 import CELL, VIDEO, _page, _problems, _video_folder


@pytest.mark.parametrize(
    "player",
    [
        f"<video controls src='{VIDEO}x'></video>",
        f'<video controls src="{VIDEO}x"></video>',
        f"<video controls src={VIDEO}x></video>",
        f"<video controls src='{VIDEO}.bak'></video>",
        f"<video controls src='{VIDEO}?v=1'></video>",
        f"<video controls src='{VIDEO}#t=1'></video>",
        f"<video controls src='x/{VIDEO}'></video>",
        f"<video controls src='../{VIDEO}'></video>",
        f"<video controls><source src='{VIDEO}x'></video>",
        f"<video controls poster='{VIDEO}' src='evidence/video/other.mp4'></video>",
        f"<video controls data-src='{VIDEO}'></video>",
        f"<video controls src='evidence/video/other.mp4' title='{VIDEO}'></video>",
        f"<video controls src='evidence/video/other.mp4'>{VIDEO}</video>",
    ],
)
def test_a_player_that_is_not_exactly_the_published_video_fails_the_gate(tmp_path, player):
    reports, folder = _video_folder(tmp_path)
    _page(folder, CELL + player)
    assert any("in the video player" in item for item in _problems(reports)), player
    assert not require_reports._has_video_player(CELL + player, VIDEO)


@pytest.mark.parametrize(
    "player",
    [
        f"<video controls preload='metadata' src='{VIDEO}'></video>",
        f'<video controls src="{VIDEO}"></video>',
        f"<video controls src={VIDEO}></video>",
        f"<video controls src={VIDEO} preload=metadata></video>",
        f"<video controls src = '{VIDEO}' ></video >",
        f"<VIDEO CONTROLS><SOURCE SRC='{VIDEO}' TYPE='video/mp4'></VIDEO>",
        f"<video controls>\n  <source src='{VIDEO}'>\n</video>",
    ],
)
def test_the_published_video_as_the_player_source_passes_the_gate(tmp_path, player):
    reports, folder = _video_folder(tmp_path)
    _page(folder, CELL + player)
    assert _problems(reports) == []
