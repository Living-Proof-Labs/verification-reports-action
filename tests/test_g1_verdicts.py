# SPDX-License-Identifier: Apache-2.0
"""G1 still grades FAIL, and the Scenario verdicts stay the published ones.

The compare is the committed view in tests/fixtures/g1/scenario-verdicts.json.
Both published G1 reports must match it, and capability-verifier v3 collect()
must put those same steps back on the bundle without changing the grade.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from compare_integrity import scenario_view
from test_evaluators_run import verifier_src

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "tests/fixtures/g1/scenario-verdicts.json"
PUBLISHED_ROOT = ROOT / "tests/fixtures/old-runs/published"
HALLWAY = "unitree-g1/stock-mujoco-hallway/runs"
PUBLISHED = (
    PUBLISHED_ROOT / "production" / HALLWAY / "45754832566/report.json",
    PUBLISHED_ROOT / "peer" / HALLWAY / "45760910690/report.json",
)


@pytest.fixture(scope="module", autouse=True)
def _loader_import_path():
    verifier_src()


def test_g1_grades_fail_and_scenario_verdicts_are_unchanged(tmp_path):
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    views = [scenario_view(json.loads(path.read_text(encoding="utf-8"))) for path in PUBLISHED]
    assert views[0] == views[1] == expected
    assert expected["grades"] == ["FAIL"]
    assert expected["run_grades"] == ["FAIL"]
    steps = expected["scenarios"][0]["steps"]
    assert [(step["id"], step["verdict"]) for step in steps] == [
        ("hallway-exit-reached", "PASS"),
        ("hallway-lateral-limit", "FAIL"),
        ("hallway-wall-contact", "FAIL"),
        ("hallway-fall", "PASS"),
    ]
    assert "lateral 0.60 m > 0.49 m at 10.46 s" in steps[1]["summary"]
    assert expected["decisions"][0]["status"] == "FAIL"

    from capability_verifier.collect import collect

    scenario = expected["scenarios"][0]
    document = {
        "format": "livingproof-evaluator-result/1.1.0",
        "evaluator": {
            "id": scenario["evaluator"],
            "kind": "scenario",
            "display_name": "Scenario",
            "version": "1.6.0",
        },
        "scenario": {"trial_id": scenario["trial_id"], "steps": scenario["steps"]},
    }
    bundle = {
        "results": [{"id": scenario["result_id"]}],
        "assessment": {
            "runs": [{"grade": "FAIL", "rationale": expected["rationales"][0]}],
            "decisions": [dict(expected["decisions"][0])],
        },
    }
    results = tmp_path / "results" / scenario["evaluator"]
    results.mkdir(parents=True)
    (results / f"{scenario['trial_id']}.json").write_text(
        json.dumps(document) + "\n", encoding="utf-8"
    )
    collect(bundle, tmp_path / "results", tmp_path / "report")
    stored = bundle["results"][0]["evaluator_results"][0]
    assert stored["scenario"]["steps"] == steps
    assert bundle["assessment"]["runs"][0]["grade"] == "FAIL"
    assert bundle["assessment"]["decisions"][0]["status"] == "FAIL"
    assert bundle["assessment"]["decisions"][0]["reason"] == expected["decisions"][0]["reason"]
