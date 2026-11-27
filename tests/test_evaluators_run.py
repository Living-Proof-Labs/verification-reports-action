# SPDX-License-Identifier: Apache-2.0
"""Collect-job evaluator behaviour. Each test fails when its fix is reverted."""

from __future__ import annotations

import contextlib
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
    record_missing,
    reporter_docker_command,
    reporter_image,
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
