# SPDX-License-Identifier: Apache-2.0
"""#75 step 2: the site action wires the pinned reporter's `site` command and renders nothing.

site_action.py restores history (reports_restore.py, here) and then runs
`livingproof-capability-report site`, passing the caller repository. The renderer modules,
their assets, the golden builder and report.mjs's stub renderer are gone from the action.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import site_action
import site_fixture

ROOT = Path(__file__).resolve().parents[1]
CALLER = "Living-Proof-Labs/livingproof-unitree-g1"

FAKE_REPORTER = """#!{python}
import json, pathlib, sys
log = pathlib.Path({log!r})
log.write_text(json.dumps(sys.argv[1:]))
if sys.argv[1:2] == ["site"]:
    reports = pathlib.Path(sys.argv[2])
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "site.json").write_text('{{"runs": 4, "scenarios": 2}}')
    (reports / "index.html").write_text("<p>index</p>")
    print("Built " + str(reports) + " (2 scenarios, 4 runs).")
sys.exit({code})
"""


def _fake_reporter(tmp_path: Path, code: int = 0) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "argv.json"
    exe = bin_dir / "livingproof-capability-report"
    exe.write_text(FAKE_REPORTER.format(python=sys.executable, log=str(log), code=code))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return bin_dir, log


def _env(tmp_path: Path, bin_dir: Path, **extra: str) -> dict[str, str]:
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "INPUT_RUNS_DIR": str(tmp_path / "runs"),
        "INPUT_OUTPUT_DIR": "reports",
        "INPUT_RESTORE_HISTORY": "false",
        "INPUT_UPLOAD_ARTIFACT": "false",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REPOSITORY": CALLER,
        "GITHUB_WORKSPACE": str(tmp_path),
        "GITHUB_RUN_ID": "4242",
    }
    env.update(extra)
    return env


def test_the_site_action_runs_the_pinned_site_command_with_the_caller_repository(tmp_path):
    bin_dir, log = _fake_reporter(tmp_path)
    (tmp_path / "runs").mkdir()
    outputs = site_action.run_site_action(
        _env(
            tmp_path,
            bin_dir,
            INPUT_EVIDENCE_RETENTION_DAYS="3",
            INPUT_EXCLUDE_RUNS="918, 920\n919",
        )
    )
    argv = json.loads(log.read_text())
    assert argv == [
        "site",
        str(tmp_path / "reports"),
        "--runs",
        str(tmp_path / "runs"),
        "--evidence-retention-days",
        "3",
        "--repository",
        CALLER,
        "--allow-replace-run-id",
        "4242",
        "--git-dir",
        str(tmp_path),
        "--exclude-run",
        "918",
        "--exclude-run",
        "920",
        "--exclude-run",
        "919",
    ]
    assert outputs["runs-count"] == "4"
    assert outputs["reports-dir"] == "reports"


def test_a_failing_site_command_is_a_site_error_that_names_its_message(tmp_path, capsys):
    bin_dir, _ = _fake_reporter(tmp_path, code=1)
    (tmp_path / "runs").mkdir()
    with pytest.raises(site_action.SiteError, match="site command failed"):
        site_action.run_site_action(_env(tmp_path, bin_dir))


def test_the_site_action_refuses_to_run_without_the_reporter(tmp_path):
    (tmp_path / "runs").mkdir()
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    env = _env(tmp_path, empty)
    env["PATH"] = str(empty)
    with pytest.raises(site_action.SiteError, match="livingproof-capability-report"):
        site_action.run_site_action(env, executable_dir=empty)


def test_the_site_action_through_the_real_reporter_writes_the_golden_site(tmp_path):
    # The deployed bytes do not change: the fixture golden built by the Action's own
    # renderer before #75 is what the pinned site command writes through site_action.
    site_fixture.write(tmp_path / "in1", run_id="100", created="2026-11-20T06:00:00Z")
    site_fixture.write(
        tmp_path / "in2",
        run_id="200",
        created="2026-11-29T06:00:00Z",
        commit="b" * 40,
        tag="v2.3.33",
    )
    base = {
        "PATH": os.environ["PATH"],
        "INPUT_OUTPUT_DIR": "reports",
        "INPUT_EVIDENCE_RETENTION_DAYS": "7",
        "INPUT_RESTORE_HISTORY": "false",
        "INPUT_UPLOAD_ARTIFACT": "false",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REPOSITORY": CALLER,
        "GITHUB_WORKSPACE": str(tmp_path),
    }
    site_action.run_site_action({**base, "INPUT_RUNS_DIR": str(tmp_path / "in1")})
    site_action.run_site_action({**base, "INPUT_RUNS_DIR": str(tmp_path / "in2")})
    expected = (ROOT / "tests/fixtures/site-golden/SHA256SUMS").read_text(encoding="utf-8")
    assert site_fixture.digests(tmp_path / "reports") == expected


@pytest.mark.parametrize(
    "moved",
    [
        "src/reports_site.py",
        "src/reports_render.py",
        "src/reports_common.py",
        "src/assets/styles.css",
        "src/assets/open.js",
        "tools/site_golden.py",
    ],
)
def test_the_moved_renderer_is_gone_from_the_action(moved):
    assert not (ROOT / moved).exists()


def test_no_action_python_renders_the_reports_section():
    for path in [*(ROOT / "src").glob("*.py"), *(ROOT / "tools").glob("*.py")]:
        text = path.read_text(encoding="utf-8")
        assert "import reports_site" not in text, path
        assert "import reports_render" not in text, path
        assert "from reports_common" not in text, path


def test_the_action_side_scripts_need_only_the_standard_library():
    # docs.yml runs the restore and the published-reports gate with the runner's python3,
    # before the site action installs the reporter, so they must not import it.
    code = (
        "import sys; sys.path[:0] = ['src', 'tools'];"
        "import reports_restore, require_reports, site_fixture, site_action, details_files;"
        "assert 'livingproof_capability_report' not in sys.modules"
    )
    subprocess.run([sys.executable, "-S", "-c", code], cwd=ROOT, check=True)


def test_the_restore_command_runs_from_reports_restore():
    result = subprocess.run(
        [sys.executable, "-S", "src/reports_restore.py", "restore", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--reset-history" in result.stdout and "--site-url" in result.stdout
    docs = (ROOT / ".github/workflows/docs.yml").read_text(encoding="utf-8")
    # Since #74 the production job restores inside the site action (restore-root), so the
    # workflow calls no restore command; the CLI stays for local use.
    assert "restore-root: artifacts/site/reports" in docs
    assert "reports_site.py" not in docs and "reports_restore.py restore" not in docs
