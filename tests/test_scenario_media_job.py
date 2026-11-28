# SPDX-License-Identifier: Apache-2.0
"""The pinned scenario-media job must run on the declared path (capability-verifier#277).

The action no longer passes evaluator-commit. If scenario-media still requires
that pin, the job is skipped for every action caller. Walk-hallway has no world
document, so the skip is invisible there. A scenario that declares media (a
world document) would lose its media-phase results with no error. The action
cannot un-skip a reusable-workflow job; this test fails until a
capability-verifier release changes the gate and the pin moves.
"""

from __future__ import annotations

import re

from test_evaluators_run import verifier_src


def _scenario_media_if() -> str:
    text = (verifier_src().parent / ".github" / "workflows" / "scenario.yml").read_text(
        encoding="utf-8"
    )
    block = re.search(r"\n  scenario-media:\n(.*?)\n    runs-on:", text, flags=re.S)
    assert block is not None, "scenario.yml has no scenario-media job"
    match = re.search(r"^    if: (.+)$", block.group(1), flags=re.M)
    assert match is not None, "scenario-media has no job-level if"
    return match.group(1)


def test_the_pinned_scenario_media_job_runs_when_evaluators_are_declared():
    """The job must not require evaluator-commit when the evaluators input is set."""
    condition = _scenario_media_if()
    assert "inputs.evaluators" in condition, (
        "scenario-media in the pinned capability-verifier is still gated on "
        "evaluator-commit only, so a declared Scenario line skips the job "
        f"(capability-verifier#277): {condition}"
    )
