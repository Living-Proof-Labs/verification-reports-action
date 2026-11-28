# SPDX-License-Identifier: Apache-2.0
import json

import pytest

from compare_integrity import compare_scenario, different, main, scenario_view, view


def _document(percentage, finding, started="2026-11-27T00:00:00Z", version="1.9.0"):
    return {
        "evaluator": {"id": "example", "version": version, "display_name": "Run Integrity"},
        "descriptor": {"version": version, "title": "Run Integrity"},
        "timestamps": {"started_at": started, "completed_at": started},
        "integrity": {
            "run": {
                "test_system_status": "COMPLETED",
                "percentage": percentage,
                "started_at": started,
            },
            "findings": [finding],
        },
    }


def test_timestamps_and_version_stamps_are_not_a_difference():
    left = _document(80, {"id": "gap"}, started="2026-11-26T00:00:00Z", version="1.8.6")
    right = _document(80, {"id": "gap"}, started="2026-11-27T00:00:00Z", version="1.9.0")
    assert view(left) == view(right)
    assert different(left, right) is False


def test_a_finding_or_percentage_change_is_a_difference():
    base = _document(80, {"id": "gap"})
    assert different(base, _document(70, {"id": "gap"})) is True
    assert different(base, _document(80, {"id": "other"})) is True


def _report(steps=None, grade="FAIL", copies=1, version="1.5.3"):
    steps = steps or [
        {"id": "hallway-exit-reached", "verdict": "PASS", "summary": "exit at 11.46 s"},
        {"id": "hallway-lateral-limit", "verdict": "FAIL", "summary": "lateral 0.60 m"},
    ]
    document = {
        "format": "livingproof-evaluator-result/1.1.0",
        "evaluator": {"id": "example-scenario", "kind": "scenario", "version": version},
        "scenario": {"trial_id": "stock-seed-01", "steps": steps},
    }
    return {
        "summaries": [{"grade": grade}],
        "source_bundle": {
            "results": [
                {"id": "stock-seed-01", "evaluator_results": [document] * copies},
            ],
            "assessment": {
                "runs": [{"grade": grade, "rationale": "0/1 PASS, needs 1"}],
                "decisions": [
                    {"result_id": "stock-seed-01", "status": grade, "reason": "Failed: lateral"}
                ],
            },
        },
    }


def _compare(tmp_path, report, expected=None):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(scenario_view(expected or _report())), encoding="utf-8")
    found = tmp_path / "report.json"
    found.write_text(json.dumps(report), encoding="utf-8")
    return compare_scenario(found, snapshot)


def test_the_scenario_view_has_the_grade_verdicts_and_findings_but_no_version_stamp():
    shown = scenario_view(_report())
    assert shown["grades"] == ["FAIL"]
    assert shown["scenarios"][0]["evaluator"] == "example-scenario"
    assert "version" not in shown["scenarios"][0]
    assert [step["verdict"] for step in shown["scenarios"][0]["steps"]] == ["PASS", "FAIL"]
    assert scenario_view(_report(version="1.5.3")) == scenario_view(_report(version="1.6.0"))


def test_a_new_evaluator_version_with_the_same_verdicts_is_not_a_difference(tmp_path, capsys):
    assert _compare(tmp_path, _report(version="1.6.0")) == 0
    assert "no difference" in capsys.readouterr().out


def test_a_changed_verdict_grade_or_finding_is_a_difference(tmp_path, capsys):
    flipped = [
        {"id": "hallway-exit-reached", "verdict": "PASS", "summary": "exit at 11.46 s"},
        {"id": "hallway-lateral-limit", "verdict": "PASS", "summary": "lateral 0.60 m"},
    ]
    assert _compare(tmp_path, _report(steps=flipped)) == 1
    assert _compare(tmp_path, _report(grade="PASS")) == 1
    worded = [
        {"id": "hallway-exit-reached", "verdict": "PASS", "summary": "exit at 12.00 s"},
        {"id": "hallway-lateral-limit", "verdict": "FAIL", "summary": "lateral 0.60 m"},
    ]
    assert _compare(tmp_path, _report(steps=worded)) == 1
    assert "differ" in capsys.readouterr().out


def test_scenario_results_placed_twice_are_a_difference(tmp_path):
    assert _compare(tmp_path, _report(copies=2)) == 1


def test_a_report_with_no_scenario_result_is_a_difference(tmp_path):
    report = _report()
    report["source_bundle"]["results"][0]["evaluator_results"] = []
    assert _compare(tmp_path, report) == 1


def test_other_evaluators_results_are_not_part_of_the_scenario_view():
    report = _report()
    integrity = _document(100, {"id": "finding", "summary": "ok"})
    health = {
        "format": "livingproof-evaluator-result/1.1.0",
        "evaluator": {"id": "example-health", "kind": "advisory", "version": "1.2.0"},
    }
    report["source_bundle"]["results"][0]["evaluator_results"] += [integrity, health]
    assert scenario_view(report) == scenario_view(_report())


def test_verdicts_and_report_are_used_together(tmp_path):
    path = tmp_path / "x.json"
    path.write_text("{}", encoding="utf-8")
    for argv in (["--verdicts", str(path)], ["--report", str(path)]):
        with pytest.raises(SystemExit) as raised:
            main(argv)
        assert raised.value.code == 2
