# SPDX-License-Identifier: Apache-2.0
"""Vera's #86 Low: the gate's player check reads the page as a browser would, not as text.

`_has_video_player` matched `<video …>…</video>` text with a case-insensitive pattern, so a
player inside a comment, a script, a template or a textarea counted, a `<source>` counted
under a `<video src>` (which a browser ignores), and a path in another case matched.
"""

import pytest

import require_reports

VIDEO = "evidence/video/trial-01-fail.mp4"
OTHER = "evidence/video/other.mp4"
PLAYER = f"<video controls src='{VIDEO}'></video>"


@pytest.mark.parametrize(
    "page",
    [
        f"<!-- {PLAYER} -->",
        f"<!--{PLAYER}--><p>x</p>",
        f'<script>document.write("{PLAYER}")</script>',
        f"<script type='text/plain'>{PLAYER}</script>",
        f"<style>/* {PLAYER} */</style>",
        f"<template>{PLAYER}</template>",
        f"<template><template></template>{PLAYER}</template>",
        f"<textarea>{PLAYER}</textarea>",
        f"<TEXTAREA>{PLAYER}</TEXTAREA>",
        f"<title>{PLAYER}</title>",
        f"<noscript>{PLAYER}</noscript>",
        f"<xmp>{PLAYER}</xmp>",
        f"<noembed>{PLAYER}</noembed>",
        f"<iframe>{PLAYER}</iframe>",
        # A <video src> plays its own src; a browser ignores its <source> children.
        f"<video controls src='{OTHER}'><source src='{VIDEO}'></video>",
        f"<video controls src=''><source src='{VIDEO}'></video>",
        # Only a direct <source> child is a source.
        f"<video controls><div><source src='{VIDEO}'></div></video>",
        f"<video controls><audio><source src='{VIDEO}'></audio></video>",
        # A path is exact, case included; only tag and attribute names ignore case.
        f"<video controls src='{VIDEO.upper()}'></video>",
        "<video controls src='evidence/video/Trial-01-fail.mp4'></video>",
        "<video controls src='evidence/video/trial-01-fail.MP4'></video>",
        f"<video controls><source src='{VIDEO.upper()}'></video>",
    ],
)
def test_a_player_a_browser_would_not_show_or_play_is_not_a_player(page):
    assert require_reports._has_video_player(page, VIDEO) is False


@pytest.mark.parametrize(
    "page",
    [
        PLAYER,
        f"<VIDEO CONTROLS SRC='{VIDEO}'></VIDEO>",
        f"<Video Controls><Source Src='{VIDEO}' Type='video/mp4'></Video>",
        f"<video controls src='{VIDEO}'><source src='{OTHER}'></video>",
        f"<video controls><source src='{OTHER}'><source src='{VIDEO}'></video>",
        f"<!-- {OTHER} -->{PLAYER}",
        f"<script>var a = 1;</script>{PLAYER}",
        f"<template><p>x</p></template>{PLAYER}",
        f"<textarea>x</textarea>{PLAYER}",
        f"<video controls><source src='{OTHER}'></video>{PLAYER}",
    ],
)
def test_a_real_player_of_the_exact_path_still_passes(page):
    assert require_reports._has_video_player(page, VIDEO) is True
