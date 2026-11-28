# SPDX-License-Identifier: Apache-2.0
"""Rivet's call on #86: when evidence expiry makes `site` rewrite a decorated run as a plain
page, the action composes its viewer again, and refuses only if that recomposition fails
validation. The order stays: validate the history, run `site`, then compose viewers.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

import site_action
import trusted_viewer

OWNER = "Living-Proof-Labs/verification-reports-action"
RUN = "unitree-g1/stock-mujoco-hallway/runs/1001"


def _setup(tmp_path: Path, monkeypatch, *, rewrite: bool, composed=True, valid=True):
    run = tmp_path / "reports" / RUN
    (run / "trusted-viewer").mkdir(parents=True)
    (run / "index.html").write_text("decorated page")
    log: list[str] = []
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "livingproof-capability-report"
    body = (
        f"log = pathlib.Path({str(tmp_path / 'ran')!r}); log.write_text('ran')\n"
        "r = pathlib.Path(sys.argv[2])\n"
        "(r / 'site.json').write_text('{\"runs\": 1}')\n"
    )
    if rewrite:
        body += f"(r / {RUN!r} / 'index.html').write_text('plain page after expiry')\n"
    exe.write_text(f"#!{sys.executable}\nimport pathlib, sys\n{body}", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "runs").mkdir()

    def validate_history(root, viewers):
        assert not (tmp_path / "ran").exists(), "history must be validated before site runs"
        log.append("validate_history")

    def render_fresh(folder, viewers, *, command=None, existing=False):
        assert (tmp_path / "ran").exists(), "viewers are composed after site runs"
        log.append(f"render_fresh existing={existing}")
        if composed is None:
            raise site_action.SiteError("Expected one unchanged native evaluator panel")
        if composed:
            (folder / "index.html").write_text("decorated page")
        return composed

    def validate_restored(folder, viewers):
        log.append("validate_restored")
        if not valid:
            raise site_action.SiteError("Restored executable lacks one exact approval")

    monkeypatch.setattr(trusted_viewer, "validate_history", validate_history)
    monkeypatch.setattr(trusted_viewer, "render_fresh", render_fresh)
    monkeypatch.setattr(trusted_viewer, "validate_restored", validate_restored)
    import livingproof_capability_report.site as reporter_site

    monkeypatch.setattr(reporter_site, "check_reports_tree", lambda *a, **k: [])
    env = {
        "PATH": str(bin_dir),
        "INPUT_RUNS_DIR": str(tmp_path / "runs"),
        "INPUT_OUTPUT_DIR": "reports",
        "INPUT_RESTORE_HISTORY": "false",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REPOSITORY": OWNER,
        "GITHUB_WORKSPACE": str(tmp_path),
    }
    return env, run, log


def test_an_untouched_decorated_run_is_not_composed_again(tmp_path, monkeypatch):
    env, run, log = _setup(tmp_path, monkeypatch, rewrite=False)
    site_action.run_site_action(env)
    assert log == ["validate_history"]
    assert (run / "index.html").read_text() == "decorated page"


def test_a_run_expiry_rewrote_is_composed_again_and_validated(tmp_path, monkeypatch):
    env, run, log = _setup(tmp_path, monkeypatch, rewrite=True)
    site_action.run_site_action(env)
    assert log == ["validate_history", "render_fresh existing=True", "validate_restored"]
    assert (run / "index.html").read_text() == "decorated page"


def test_a_rewritten_run_with_no_viewer_to_compose_is_refused(tmp_path, monkeypatch):
    # No historical approval to compose with: refuse, never publish the plain page silently.
    env, run, log = _setup(tmp_path, monkeypatch, rewrite=True, composed=False)
    with pytest.raises(site_action.SiteError, match="could not be composed again"):
        site_action.run_site_action(env)


def test_a_recomposition_that_fails_validation_is_refused(tmp_path, monkeypatch):
    env, run, log = _setup(tmp_path, monkeypatch, rewrite=True, valid=False)
    with pytest.raises(site_action.SiteError, match="lacks one exact approval"):
        site_action.run_site_action(env)


def test_a_recomposition_the_reporter_refuses_is_refused(tmp_path, monkeypatch):
    env, run, log = _setup(tmp_path, monkeypatch, rewrite=True, composed=None)
    with pytest.raises(site_action.SiteError, match="native evaluator panel"):
        site_action.run_site_action(env)


def test_a_failed_history_validation_stops_before_site_runs(tmp_path, monkeypatch):
    env, run, log = _setup(tmp_path, monkeypatch, rewrite=True)

    def refuse(root, viewers):
        raise site_action.SiteError("Restored executable has no corresponding native panel")

    monkeypatch.setattr(trusted_viewer, "validate_history", refuse)
    with pytest.raises(site_action.SiteError, match="no corresponding native panel"):
        site_action.run_site_action(env)
    assert not (tmp_path / "ran").exists()
