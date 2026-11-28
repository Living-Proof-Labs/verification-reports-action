# SPDX-License-Identifier: Apache-2.0
"""The collect job runs the loader only on lines the trial jobs have not run.

The trial workflows run a scenario line and upload its results, which the collect
job attaches before the loader runs. A second loader run would execute that line
again and place its results a second time. Each test fails when its fix is reverted.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluators_run import apply_configuration, executable_lines, main, run_downloaded
from test_evaluators_run import _records, _render, _verdict, verifier_src

GRADER = "owner/scenario@v1.6.0/descriptor.json"
INTEGRITY = "owner/integrity@v1.9.0/descriptor.json"
ADVISORY = "owner/advisory@v1.2.0/descriptor.json"
KINDS = {GRADER: "scenario", INTEGRITY: "integrity", ADVISORY: "advisory"}


@pytest.fixture(scope="module", autouse=True)
def _loader_import_path():
    verifier_src()


def fake_prepare(text: str) -> list:
    """Stands in for capability_verifier.evaluators.prepare, which needs the network."""
    return [
        SimpleNamespace(declaration=SimpleNamespace(line=line), document={"kind": KINDS[line]})
        for line in text.splitlines()
        if line.strip()
    ]


def test_the_kind_the_trial_jobs_ran_is_not_run_again():
    kept = executable_lines([GRADER, INTEGRITY, ADVISORY], prepare=fake_prepare)
    assert kept == [INTEGRITY, ADVISORY]


def test_every_line_is_kept_when_none_is_a_trial_kind():
    assert executable_lines([INTEGRITY, ADVISORY], prepare=fake_prepare) == [INTEGRITY, ADVISORY]


def test_the_filter_reads_the_kind_from_the_descriptor_not_from_the_line_text():
    odd = "owner/scenario-named-integrity@v1.0.0/descriptor.json"
    KINDS[odd] = "scenario"
    try:
        assert executable_lines([odd, INTEGRITY], prepare=fake_prepare) == [INTEGRITY]
    finally:
        del KINDS[odd]


def test_the_default_filter_is_the_loader_prepare(monkeypatch):
    import capability_verifier.evaluators as loader

    seen = {}

    def prepare(text, **kwargs):
        seen["text"] = text
        return fake_prepare(text)

    monkeypatch.setattr(loader, "prepare", prepare)
    assert executable_lines([GRADER, INTEGRITY]) == [INTEGRITY]
    assert seen["text"] == f"{GRADER}\n{INTEGRITY}\n"


def _tree(tmp_path: Path):
    bundle = tmp_path / "verified" / "bundle" / "manifest.json"
    bundle.parent.mkdir(parents=True)
    bundle.write_text(
        json.dumps({"results": [{"id": "stock-seed-01"}], "assessment": {}}) + "\n",
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence" / "manifest.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("{}\n", encoding="utf-8")
    report = tmp_path / "report"
    _verdict(report)
    return bundle, evidence, report


def _loader_lines(command) -> list[str]:
    lines_file = Path(command[command.index("--lines") + 1])
    return lines_file.read_text(encoding="utf-8").splitlines()


def test_the_loader_is_given_no_scenario_line_and_the_scenario_is_not_run_a_second_time(tmp_path):
    bundle, evidence, report = _tree(tmp_path)
    given = []

    def runner(command, **kwargs):
        del kwargs
        given.append(_loader_lines(command))
        out = Path(command[command.index("--out") + 1])
        (out / "evaluators-provenance.json").write_text(
            '[{"outcome": "result"}]\n', encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    failed = apply_configuration(
        lines=[GRADER, INTEGRITY],
        evidence=evidence,
        bundle_path=bundle,
        report_dir=report,
        out=tmp_path / "out",
        provenance_records=_records(),
        runner=runner,
        render=_render,
        select=lambda lines: executable_lines(lines, prepare=fake_prepare),
    )
    assert failed is False
    assert given == [[INTEGRITY]]


def test_only_a_scenario_line_means_the_loader_and_the_renderer_do_not_run(tmp_path):
    bundle, evidence, report = _tree(tmp_path)
    before = bundle.read_bytes()

    def runner(command, **kwargs):
        raise AssertionError("the loader must not run for a scenario-only declaration")

    def render(*args, **kwargs):
        raise AssertionError("nothing changed, so the report is not rendered again")

    failed = apply_configuration(
        lines=[GRADER],
        evidence=evidence,
        bundle_path=bundle,
        report_dir=report,
        out=tmp_path / "out",
        provenance_records=_records(),
        runner=runner,
        render=render,
        select=lambda lines: executable_lines(lines, prepare=fake_prepare),
    )
    assert failed is False
    assert bundle.read_bytes() == before
    assert not (report / "integrity-lines.txt").exists()


def test_a_line_the_loader_refuses_is_recorded_as_not_run_and_fails_the_job(tmp_path):
    bundle, evidence, report = _tree(tmp_path)

    def prepare(text):
        raise RuntimeError("refused: descriptor does not match the implementation schema")

    failed = apply_configuration(
        lines=[GRADER, INTEGRITY],
        evidence=evidence,
        bundle_path=bundle,
        report_dir=report,
        out=tmp_path / "out",
        provenance_records=_records(),
        runner=lambda *args, **kwargs: pytest.fail("the loader must not run"),
        render=_render,
        configuration="stock",
        select=lambda lines: executable_lines(lines, prepare=prepare),
    )
    assert failed is True
    stored = json.loads(bundle.read_text(encoding="utf-8"))
    missing = stored["assessment"]["integrity_not_run"]
    assert "descriptor does not match" in missing["reason"]
    assert missing["evaluator"] == f"{GRADER}\n{INTEGRITY}"


def test_the_download_run_gives_the_filter_to_every_configuration(tmp_path):
    seen = []

    def select(lines):
        seen.append(list(lines))
        return []

    prefix = "vra-hallway-"
    download = tmp_path / "downloaded"
    for kind, name in (
        ("evidence", "manifest.json"),
        ("verified", "bundle/manifest.json"),
        ("capability-report", "report.json"),
    ):
        folder = {"evidence": "evidence", "verified": "verified", "capability-report": "reports"}[
            kind
        ]
        target = download / folder / f"{prefix}{kind}-stock" / name
        target.parent.mkdir(parents=True)
        target.write_text("{}\n", encoding="utf-8")
    plan = {
        "evaluators": {"lines": [GRADER, INTEGRITY], "input": "x"},
        "definitions": [
            {
                "execution": "capability-verifier",
                "id": "hallway",
                "path": "hallway.toml",
                "artifactPrefix": prefix,
                "configurations": ["stock"],
                "provenance": _records(),
            }
        ],
    }
    failed = run_downloaded(
        plan,
        download,
        tmp_path / "out",
        runner=lambda *args, **kwargs: pytest.fail("no line is left to run"),
        render=_render,
        select=select,
    )
    assert failed is False
    assert seen == [[GRADER, INTEGRITY]]


def test_the_job_entry_point_filters_the_lines(tmp_path, monkeypatch):
    plan = tmp_path / "plan.json"
    plan.write_text('{"definitions": []}\n', encoding="utf-8")
    monkeypatch.setenv("INPUT_PLAN_PATH", str(plan))
    monkeypatch.delenv("INPUT_EVALUATORS", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    seen = {}

    def run(plan, download, out, **kwargs):
        seen.update(kwargs)
        return False

    monkeypatch.setattr("evaluators_run.run_downloaded", run)
    assert main() == 0
    assert seen["select"] is executable_lines
