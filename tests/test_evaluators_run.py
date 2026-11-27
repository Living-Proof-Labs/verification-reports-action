# SPDX-License-Identifier: Apache-2.0
"""Collect-job evaluator behaviour. Each test fails when its fix is reverted."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from evaluators_run import (
    apply_configuration,
    classify,
    last_stderr_line,
    load_provenance,
    loader_command,
    main,
    pin_integrity_clock,
    record_missing,
    render_with_reporter,
    reporter_docker_command,
    reporter_image,
    resolve_caller_evaluators,
    run_downloaded,
)

PIN = "93a490d82ae50e4c551873dfcfaa778adcd5b689"


def verifier_src() -> Path:
    dest = Path("/tmp/capability-verifier")
    marker = dest / "src" / "capability_verifier" / "evaluators.py"
    if not marker.is_file() or PIN not in subprocess.check_output(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], text=True
    ):
        if dest.exists():
            subprocess.run(
                ["git", "-C", str(dest), "fetch", "--depth", "1", "origin", PIN], check=True
            )
            subprocess.run(
                ["git", "-C", str(dest), "checkout", "--detach", "FETCH_HEAD"], check=True
            )
        else:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "--no-checkout",
                    "https://github.com/Living-Proof-Labs/capability-verifier.git",
                    str(dest),
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(dest), "fetch", "--depth", "1", "origin", PIN], check=True
            )
            subprocess.run(
                ["git", "-C", str(dest), "checkout", "--detach", "FETCH_HEAD"], check=True
            )
    src = dest / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    return src


@pytest.fixture(scope="module", autouse=True)
def _loader_import_path():
    verifier_src()


def _records():
    return [
        {
            "name": "livingproof-capability-report",
            "tag": "v3.15.4",
            "digest": "sha256:" + "ab" * 32,
        }
    ]


def _verdict(report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    text = "Hallway: overall FAIL.\n"
    (report_dir / "report.md").write_text(text, encoding="utf-8")
    (report_dir / "report.txt").write_text(text, encoding="utf-8")
    (report_dir / "report.html").write_text(
        "<p class='stored-status'>Hallway: overall FAIL.</p>\n", encoding="utf-8"
    )
    (report_dir / "report.json").write_text("{}\n", encoding="utf-8")


def _render(image, manifest, report_dir, links):
    del manifest, links
    assert "3.16.0" not in image
    assert image.startswith("ghcr.io/living-proof-labs/livingproof-capability-report:3.15.4@")
    _verdict(report_dir)


def test_reporter_docker_runs_as_the_current_user_and_keeps_the_image_entrypoint(tmp_path):
    image = reporter_image(_records())
    manifest = tmp_path / "bundle" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text("{}\n", encoding="utf-8")
    report = tmp_path / "report"
    report.mkdir()
    links = tmp_path / "links" / "evidence-links.json"
    links.parent.mkdir()
    links.write_text("{}\n", encoding="utf-8")
    command = reporter_docker_command(
        image, manifest, report, links, fmt="html", output="report.html"
    )
    assert command[command.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert command[command.index("-w") + 1] == "/out"
    assert "--entrypoint" not in command
    assert command[command.index(image) + 1] == "render"
    assert "3.16.0" not in image
    text = reporter_docker_command(image, manifest, report, None, text=True)
    assert text[-2:] == ["render", "/out/report.json"]


def test_reporter_image_follows_the_lock_pin():
    image = reporter_image(_records())
    assert (
        image
        == "ghcr.io/living-proof-labs/livingproof-capability-report:3.15.4@" + "sha256:" + "ab" * 32
    )
    assert "3.16.0" not in image


def test_media_dir_is_an_argument_only_when_the_caller_has_one(tmp_path):
    evidence = tmp_path / "manifest.json"
    bundle = tmp_path / "bundle.json"
    out = tmp_path / "out"
    without = loader_command(tmp_path / "lines.txt", evidence, out, bundle, None)
    assert "--media-dir" not in without
    assert without[5:7] == ["capability_verifier.evaluators", "run"]
    with_media = loader_command(tmp_path / "lines.txt", evidence, out, bundle, tmp_path / "mcap")
    assert with_media[-2:] == ["--media-dir", str(tmp_path / "mcap")]


def test_the_exit_code_does_not_change_a_recorded_outcome(tmp_path):
    seen = []

    def once(code: int) -> dict:
        root = tmp_path / str(code)
        bundle_path = root / "bundle" / "manifest.json"
        bundle_path.parent.mkdir(parents=True)
        bundle_path.write_text(json.dumps({"assessment": {"runs": [{"grade": "FAIL"}]}}) + "\n")
        report = root / "report"
        evidence = root / "evidence.json"
        evidence.write_text("{}\n")

        def runner(command, **kwargs):
            seen.append(kwargs["env"]["PYTHONPATH"])
            out = Path(command[command.index("--out") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "evaluators-provenance.json").write_text(
                json.dumps([{"outcome": "result", "exit_code": code}]) + "\n", encoding="utf-8"
            )
            return subprocess.CompletedProcess(command, code, "", "this stderr is not the branch\n")

        failed = apply_configuration(
            lines=["owner/repo@v1.2.3/descriptor.json"],
            evidence=evidence,
            bundle_path=bundle_path,
            report_dir=report,
            out=root / "eval-out",
            provenance_records=_records(),
            runner=runner,
            render=_render,
        )
        assert failed is False
        return json.loads(bundle_path.read_text(encoding="utf-8"))

    assert once(0) == once(7)
    assert seen == ["src", "src"]
    assert (
        classify([{"outcome": "result"}], 0) == classify([{"outcome": "failure"}], 1) == "recorded"
    )
    assert classify(None, 0) == "missing"


def test_no_lines_does_not_call_the_loader_or_add_a_card(tmp_path):
    report = tmp_path / "reports" / "vra-demo-capability-report-stock"
    _verdict(report)
    calls = []
    failed = run_downloaded(
        {
            "evaluators": {"lines": [], "input": "#"},
            "definitions": [
                {
                    "execution": "capability-verifier",
                    "id": "demo",
                    "path": "demo.toml",
                    "artifactPrefix": "vra-demo-",
                    "configurations": ["stock"],
                    "provenance": _records(),
                }
            ],
        },
        tmp_path,
        tmp_path / "out",
        runner=lambda command, **kwargs: calls.append(command),
    )
    assert failed is False
    assert calls == []
    text = (report / "report.md").read_text(encoding="utf-8")
    assert "Run Integrity" not in text
    assert "NOT RUN" not in text


def test_missing_provenance_records_the_stderr_reason(tmp_path):
    from capability_verifier.collect import integrity_report_lines

    bundle_path = tmp_path / "bundle" / "manifest.json"
    bundle_path.parent.mkdir(parents=True)
    bundle_path.write_text(json.dumps({"assessment": {"runs": [{"conclusion": "PASS"}]}}) + "\n")
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}\n")
    report = tmp_path / "report"
    stderr = "preparing\nrefused: 'main' is not a declaration line\n"

    def runner(command, **kwargs):
        del command, kwargs
        return subprocess.CompletedProcess([], 1, "", stderr)

    failed = apply_configuration(
        lines=["owner/repo@main/descriptor.json"],
        evidence=evidence,
        bundle_path=bundle_path,
        report_dir=report,
        out=tmp_path / "eval-out",
        provenance_records=_records(),
        runner=runner,
        render=_render,
    )
    assert failed is True
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    reason = bundle["assessment"]["integrity_not_run"]["reason"]
    assert reason == last_stderr_line(stderr)
    assert bundle["assessment"]["integrity_not_run"]["job_result"] == "failure"
    shown = "\n".join(integrity_report_lines(bundle))
    assert reason in shown
    assert "NOT RUN (failure)" in shown
    page = (report / "report.md").read_text(encoding="utf-8")
    assert reason in page
    assert "NOT RUN (failure)" in page
    assert (report / "integrity-lines.txt").is_file()
    bad = tmp_path / "not-a-list.json"
    bad.write_text("{}\n", encoding="utf-8")
    assert load_provenance(bad) is None
    assert load_provenance(tmp_path / "missing.json") is None
    unreadable = tmp_path / "unreadable.json"
    unreadable.write_bytes(b"\xff\xfe")
    assert load_provenance(unreadable) is None


def test_a_bad_line_fails_with_the_loader_refusal(tmp_path):
    from capability_verifier.evaluators import main

    evidence = tmp_path / "manifest.json"
    evidence.write_text("{}\n", encoding="utf-8")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text("{}\n", encoding="utf-8")
    cases = [
        "Living-Proof-Labs/example@main/descriptor.json",
        "Living-Proof-Labs/example@93a490d8/descriptor.json",
        "descriptor.json",
    ]
    for line in cases:
        lines = tmp_path / "lines.txt"
        lines.write_text(line + "\n", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(
                [
                    "run",
                    "--lines",
                    str(lines),
                    "--evidence-manifest",
                    str(evidence),
                    "--out",
                    str(tmp_path / "out"),
                    "--bundle",
                    str(bundle_path),
                ]
            )
        stderr = err.getvalue()
        assert code != 0
        assert "is not a declaration line" in stderr
        bundle = {"assessment": {"runs": [{}]}}
        reason = record_missing(bundle, stderr)
        assert reason == last_stderr_line(stderr)
        assert "is not a declaration line" in reason
        from capability_verifier.collect import integrity_report_lines

        shown = "\n".join(integrity_report_lines(bundle))
        assert reason in shown


def _strip_integrity(document):
    copy = json.loads(json.dumps(document))
    assessment = copy.get("assessment")
    if isinstance(assessment, dict):
        assessment.pop("integrity_results", None)
        assessment.pop("integrity_not_run", None)
    return copy


def _result_document():
    return {
        "format": "livingproof-evaluator-result/1.1.0",
        "evaluator": {"id": "example-scenario", "kind": "scenario", "display_name": "Scenario"},
        "scenario": {"trial_id": "stock-seed-01"},
    }


def _provenance_runner(command, **kwargs):
    del kwargs
    out = Path(command[command.index("--out") + 1])
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluators-provenance.json").write_text('[{"outcome": "result"}]\n', encoding="utf-8")
    bundle_path = Path(command[command.index("--bundle") + 1])
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle.setdefault("assessment", {})["integrity_results"] = {"added-by-loader": True}
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    return subprocess.CompletedProcess(command, 0, "", "")


def test_scenario_results_are_on_the_bundle_the_loader_and_render_see(tmp_path):
    document = _result_document()
    results = tmp_path / "results" / "example-scenario"
    results.mkdir(parents=True)
    (results / "stock-seed-01.json").write_text(json.dumps(document) + "\n", encoding="utf-8")
    bundle_path = tmp_path / "verified" / "bundle" / "manifest.json"
    bundle_path.parent.mkdir(parents=True)
    bundle_path.write_text(
        json.dumps({"results": [{"id": "stock-seed-01"}], "assessment": {}}) + "\n",
        encoding="utf-8",
    )
    evidence = tmp_path / "manifest.json"
    evidence.write_text("{}\n", encoding="utf-8")
    report = tmp_path / "report"
    report.mkdir()
    seen = {}

    def runner(command, **kwargs):
        bundle = json.loads(
            Path(command[command.index("--bundle") + 1]).read_text(encoding="utf-8")
        )
        seen["at_loader"] = bundle
        return _provenance_runner(command, **kwargs)

    def render(image, manifest, report_dir, links):
        del image, links
        seen["at_render"] = json.loads(Path(manifest).read_text(encoding="utf-8"))
        _verdict(report_dir)

    apply_configuration(
        lines=["owner/repo@v1.2.3/descriptor.json"],
        evidence=evidence,
        bundle_path=bundle_path,
        report_dir=report,
        out=tmp_path / "out",
        provenance_records=_records(),
        results_dir=tmp_path / "results",
        runner=runner,
        render=render,
    )
    stored = seen["at_loader"]["results"][0]["evaluator_results"]
    assert stored[0]["scenario"]["trial_id"] == "stock-seed-01"
    assert (
        seen["at_render"]["results"][0]["evaluator_results"][0]["evaluator"]["id"]
        == "example-scenario"
    )


def test_rerendered_report_keeps_the_original_apart_from_integrity(tmp_path):
    document = _result_document()
    original = {
        "summary": "hallway",
        "results": [{"id": "stock-seed-01", "evaluator_results": [document]}],
        "assessment": {"grade": "FAIL"},
    }
    results = tmp_path / "results" / "example-scenario"
    results.mkdir(parents=True)
    (results / "stock-seed-01.json").write_text(json.dumps(document) + "\n", encoding="utf-8")
    bundle_path = tmp_path / "verified" / "bundle" / "manifest.json"
    bundle_path.parent.mkdir(parents=True)
    bundle_path.write_text(
        json.dumps(
            {
                "summary": "hallway",
                "results": [{"id": "stock-seed-01"}],
                "assessment": {"grade": "FAIL"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    evidence = tmp_path / "manifest.json"
    evidence.write_text("{}\n", encoding="utf-8")
    report = tmp_path / "report"
    report.mkdir()
    (report / "report.json").write_text(json.dumps(original) + "\n", encoding="utf-8")

    def render(image, manifest, report_dir, links):
        del image, links
        bundle = json.loads(Path(manifest).read_text(encoding="utf-8"))
        _verdict(report_dir)
        (report_dir / "report.json").write_text(json.dumps(bundle) + "\n", encoding="utf-8")

    apply_configuration(
        lines=["owner/repo@v1.2.3/descriptor.json"],
        evidence=evidence,
        bundle_path=bundle_path,
        report_dir=report,
        out=tmp_path / "out",
        provenance_records=_records(),
        results_dir=tmp_path / "results",
        runner=_provenance_runner,
        render=render,
    )
    rendered = json.loads((report / "report.json").read_text(encoding="utf-8"))
    assert rendered["assessment"]["integrity_results"] == {"added-by-loader": True}
    assert _strip_integrity(rendered) == _strip_integrity(original)


def test_main_returns_zero_and_records_whether_the_job_should_fail(tmp_path, monkeypatch):
    output = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.delenv("INPUT_PLAN_PATH", raising=False)
    monkeypatch.delenv("INPUT_EVALUATORS", raising=False)
    assert main() == 0
    assert output.read_text(encoding="utf-8") == "failed=true\n"

    output.write_text("", encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text('{"definitions": []}\n', encoding="utf-8")
    monkeypatch.setenv("INPUT_PLAN_PATH", str(plan))
    monkeypatch.setattr("evaluators_run.run_downloaded", lambda *args, **kwargs: False)
    assert main() == 0
    assert output.read_text(encoding="utf-8") == "failed=false\n"

    output.write_text("", encoding="utf-8")
    monkeypatch.setattr("evaluators_run.run_downloaded", lambda *args, **kwargs: True)
    assert main() == 0
    assert output.read_text(encoding="utf-8") == "failed=true\n"


def test_main_returns_zero_when_the_download_run_raises(tmp_path, monkeypatch):
    output = tmp_path / "github-output.txt"
    plan = tmp_path / "plan.json"
    plan.write_text('{"definitions": []}\n', encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("INPUT_PLAN_PATH", str(plan))
    monkeypatch.delenv("INPUT_EVALUATORS", raising=False)

    def boom(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("provenance is missing")

    monkeypatch.setattr("evaluators_run.run_downloaded", boom)
    assert main() == 0
    assert output.read_text(encoding="utf-8") == "failed=true\n"


def test_missing_evaluators_path_returns_zero_and_records_failure(tmp_path, monkeypatch):
    caller = tmp_path / "caller"
    caller.mkdir()
    plan = tmp_path / "plan.json"
    plan.write_text('{"definitions": []}\n', encoding="utf-8")
    output = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("INPUT_PLAN_PATH", str(plan))
    monkeypatch.setenv("INPUT_CALLER_ROOT", str(caller))
    monkeypatch.setenv("INPUT_EVALUATORS", "missing.yml")
    assert main() == 0
    assert output.read_text(encoding="utf-8") == "failed=true\n"


def test_inline_empty_list_is_the_sentinel(tmp_path):
    declared = resolve_caller_evaluators("[]", tmp_path)
    assert declared["lines"] == []
    assert declared["run"] is False
    assert declared["input"] == "#"


def test_empty_input_does_not_read_an_evaluators_file(tmp_path, monkeypatch):
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / "evaluators.yml").write_text(
        "- owner/repo@v1.2.3/descriptor.json\n", encoding="utf-8"
    )
    captured = []

    def fake_run(plan, download, out_root, runner=None, render=None):
        del download, out_root, runner, render
        captured.append(plan["evaluators"]["lines"])
        return False

    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"evaluators": {"lines": ["should-not-stick"]}, "definitions": []}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "github-output.txt"))
    monkeypatch.setenv("INPUT_PLAN_PATH", str(plan))
    monkeypatch.setenv("INPUT_CALLER_ROOT", str(caller))
    monkeypatch.setenv("INPUT_EVALUATORS", "")
    monkeypatch.setattr("evaluators_run.run_downloaded", fake_run)
    assert main() == 0
    assert captured == [[]]


def test_a_path_or_a_list_is_passed_to_the_loader(tmp_path, monkeypatch):
    caller = tmp_path / "caller"
    caller.mkdir()
    line = "owner/repo@v1.2.3/descriptor.json"
    (caller / "mine.yml").write_text(f"- {line}\n", encoding="utf-8")
    captured = []

    def fake_run(plan, download, out_root, runner=None, render=None):
        del download, out_root, runner, render
        captured.append(plan["evaluators"]["lines"])
        return False

    plan = tmp_path / "plan.json"
    plan.write_text('{"definitions": []}\n', encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "github-output.txt"))
    monkeypatch.setenv("INPUT_PLAN_PATH", str(plan))
    monkeypatch.setenv("INPUT_CALLER_ROOT", str(caller))
    monkeypatch.setattr("evaluators_run.run_downloaded", fake_run)
    monkeypatch.setenv("INPUT_EVALUATORS", "mine.yml")
    assert main() == 0
    assert captured == [[line]]
    monkeypatch.setenv("INPUT_EVALUATORS", line)
    captured.clear()
    assert main() == 0
    assert captured == [[line]]

    calls = []

    def runner(command, **kwargs):
        calls.append(Path(command[command.index("--lines") + 1]).read_text(encoding="utf-8"))
        return _provenance_runner(command, **kwargs)

    root = tmp_path / "apply"
    bundle_path = root / "bundle" / "manifest.json"
    bundle_path.parent.mkdir(parents=True)
    bundle_path.write_text('{"results": [{"id": "t"}], "assessment": {}}\n', encoding="utf-8")
    evidence = root / "evidence.json"
    evidence.write_text("{}\n", encoding="utf-8")
    report = root / "report"
    report.mkdir()
    apply_configuration(
        lines=[line],
        evidence=evidence,
        bundle_path=bundle_path,
        report_dir=report,
        out=root / "out",
        provenance_records=_records(),
        runner=runner,
        render=_render,
    )
    assert calls == [f"{line}\n"]
    assert resolve_caller_evaluators("", caller)["lines"] == []
    assert resolve_caller_evaluators("mine.yml", caller)["lines"] == [line]


def _out_mount(command) -> Path:
    args = list(command)
    for index, arg in enumerate(args):
        if arg == "-v" and index + 1 < len(args) and args[index + 1].endswith(":/out"):
            return Path(args[index + 1][: -len(":/out")])
    raise AssertionError("the reporter command does not mount /out")


def test_render_uses_an_empty_directory_when_the_report_already_exists(tmp_path):
    report = tmp_path / "report"
    report.mkdir()
    (report / "report.json").write_text('{"old": true}\n', encoding="utf-8")
    (report / "report.md").write_text("old md\n", encoding="utf-8")
    (report / "report.html").write_text("old html\n", encoding="utf-8")
    (report / "report.txt").write_text("old txt\n", encoding="utf-8")
    (report / "thresholds.json").write_text("{}\n", encoding="utf-8")
    manifest = tmp_path / "bundle" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text("{}\n", encoding="utf-8")

    def runner(command):
        host = _out_mount(command)
        args = list(command)
        if "-o" in args:
            target = Path(args[args.index("-o") + 1]).name
            if (host / target).exists():
                raise FileExistsError(f"[Errno 17] File exists: '/out/{target}'")
            (host / target).write_text(f"rendered {target}\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, "rendered text\n", "")

    render_with_reporter(reporter_image(_records()), manifest, report, None, runner=runner)
    assert (report / "report.json").read_text(encoding="utf-8") == "rendered report.json\n"
    assert (report / "report.md").read_text(encoding="utf-8") == "rendered report.md\n"
    assert (report / "report.html").read_text(encoding="utf-8") == "rendered report.html\n"
    assert (report / "report.txt").read_text(encoding="utf-8") == "rendered text\n"
    assert (report / "thresholds.json").read_text(encoding="utf-8") == "{}\n"


def _render_failure_layout(tmp_path, monkeypatch):
    download = tmp_path / "downloaded"
    prefix = "vra-demo-"
    evidence = download / "evidence" / f"{prefix}evidence-stock" / "manifest.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("{}\n", encoding="utf-8")
    bundle_path = download / "verified" / f"{prefix}verified-stock" / "bundle" / "manifest.json"
    bundle_path.parent.mkdir(parents=True)
    bundle_path.write_text(
        json.dumps({"assessment": {"runs": [{"conclusion": "FAIL"}]}}) + "\n", encoding="utf-8"
    )
    report = download / "reports" / f"{prefix}capability-report-stock"
    _verdict(report)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "evaluators": {
                    "lines": ["owner/repo@v1.2.3/descriptor.json"],
                    "input": "owner/repo@v1.2.3/descriptor.json",
                },
                "definitions": [
                    {
                        "execution": "capability-verifier",
                        "id": "demo",
                        "path": "demo.toml",
                        "artifactPrefix": prefix,
                        "configurations": ["stock"],
                        "provenance": _records(),
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("INPUT_PLAN_PATH", str(plan))
    monkeypatch.setenv("INPUT_DOWNLOAD_ROOT", str(download))
    monkeypatch.delenv("INPUT_EVALUATORS", raising=False)

    def loader(command, **kwargs):
        del kwargs
        out = Path(command[command.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "evaluators-provenance.json").write_text(
            '[{"outcome": "result"}]\n', encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    def render(image, manifest, report_dir, links):
        del image, manifest, report_dir, links
        raise RuntimeError("reporter json exited 1")

    monkeypatch.setattr("evaluators_run.subprocess.run", loader)
    monkeypatch.setattr("evaluators_run.render_with_reporter", render)
    return report, bundle_path, output


def test_a_render_failure_still_leaves_integrity_lines(tmp_path, monkeypatch, capsys):
    report, bundle_path, output = _render_failure_layout(tmp_path, monkeypatch)
    assert main() == 0
    assert output.read_text(encoding="utf-8") == "failed=true\n"
    lines = (report / "integrity-lines.txt").read_text(encoding="utf-8")
    assert "reporter json exited 1" in lines
    assert "NOT RUN (failure)" in lines
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    missing = bundle["assessment"]["integrity_not_run"]
    assert missing["job_result"] == "failure"
    assert "reporter json exited 1" in missing["reason"]
    page = (report / "report.md").read_text(encoding="utf-8")
    assert "reporter json exited 1" in page
    digest = hashlib.sha256((report / "report.md").read_bytes()).hexdigest()
    assert f"{digest}  report.md" in (report / "SHA256SUMS").read_text(encoding="utf-8")
    assert "reporter json exited 1" in capsys.readouterr().err


def test_a_render_failure_names_the_configuration(tmp_path, monkeypatch, capsys):
    report, bundle_path, _output = _render_failure_layout(tmp_path, monkeypatch)
    assert main() == 0
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert bundle["assessment"]["integrity_not_run"]["reason"].startswith("stock:")
    text = (report / "integrity-lines.txt").read_text(encoding="utf-8")
    assert "stock: reporter json exited 1" in text
    assert "stock: reporter json exited 1" in capsys.readouterr().err


def _integrity_document(started_at: str) -> dict:
    return {
        "format": "livingproof-evaluator-result/1.2.0",
        "evaluator": {
            "id": "livingproof-run-integrity",
            "kind": "integrity",
            "display_name": "Run Integrity",
        },
        "timestamps": {"started_at": started_at, "completed_at": started_at},
        "integrity": {"run": {"test_system_status": "COMPLETED", "success": True}},
    }


def test_a_result_outcome_writes_integrity_lines(tmp_path):
    bundle_path = tmp_path / "bundle" / "manifest.json"
    bundle_path.parent.mkdir(parents=True)
    bundle_path.write_text(
        json.dumps({"assessment": {"runs": [{"conclusion": "PASS"}]}}) + "\n", encoding="utf-8"
    )
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}\n", encoding="utf-8")
    report = tmp_path / "report"
    report.mkdir()

    def runner(command, **kwargs):
        del kwargs
        out = Path(command[command.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "evaluators-provenance.json").write_text(
            json.dumps([{"outcome": "result"}]) + "\n", encoding="utf-8"
        )
        target = Path(command[command.index("--bundle") + 1])
        bundle = json.loads(target.read_text(encoding="utf-8"))
        bundle["assessment"]["integrity_results"] = {
            "livingproof-run-integrity": _integrity_document("2099-01-01T00:00:00Z")
        }
        target.write_text(json.dumps(bundle) + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    failed = apply_configuration(
        lines=["owner/repo@v1.2.3/descriptor.json"],
        evidence=evidence,
        bundle_path=bundle_path,
        report_dir=report,
        out=tmp_path / "out",
        provenance_records=_records(),
        runner=runner,
        render=_render,
    )
    assert failed is False
    text = (report / "integrity-lines.txt").read_text(encoding="utf-8")
    assert "Run Integrity: PASS" in text


def test_integrity_clock_follows_the_run(tmp_path):
    bundle_path = tmp_path / "bundle" / "manifest.json"
    bundle_path.parent.mkdir(parents=True)
    bundle_path.write_text(
        json.dumps({"assessment": {"runs": [{"run_id": "walk-hallway-example-20261127T061921Z"}]}})
        + "\n",
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}\n", encoding="utf-8")
    report = tmp_path / "report"
    report.mkdir()

    def runner(command, **kwargs):
        del kwargs
        out = Path(command[command.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "evaluators-provenance.json").write_text(
            json.dumps([{"outcome": "result"}]) + "\n", encoding="utf-8"
        )
        target = Path(command[command.index("--bundle") + 1])
        bundle = json.loads(target.read_text(encoding="utf-8"))
        bundle["assessment"]["integrity_results"] = {
            "livingproof-run-integrity": _integrity_document("2099-01-01T00:00:00Z")
        }
        target.write_text(json.dumps(bundle) + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    apply_configuration(
        lines=["owner/repo@v1.2.3/descriptor.json"],
        evidence=evidence,
        bundle_path=bundle_path,
        report_dir=report,
        out=tmp_path / "out",
        provenance_records=_records(),
        runner=runner,
        render=_render,
    )
    stored = json.loads(bundle_path.read_text(encoding="utf-8"))
    stamps = stored["assessment"]["integrity_results"]["livingproof-run-integrity"]["timestamps"]
    assert stamps == {"started_at": "2026-11-27T06:19:21Z", "completed_at": "2026-11-27T06:19:21Z"}
    assert pin_integrity_clock({"assessment": {}}) is False
