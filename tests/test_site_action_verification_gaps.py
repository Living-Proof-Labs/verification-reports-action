# SPDX-License-Identifier: Apache-2.0
"""Pins found by reviewing #86 that the suite left unpinned (each failed under a mutant).

- the gate's player check: a repeated src, a tag that only starts with `source`, and a
  `<source>` that is not inside a `<video>`
- the gate's own `run_url_problem` is the same rule as the pinned reporter's
- the site action passes the reporter's output and its last error line through
- `reports_restore.py restore`, which docs.yml runs only on a deploy, hands every option on
- the reporter pin is the same release and digest in every file that names it
- no file the action ships or documents still names a module that moved
"""

from __future__ import annotations

import json
import re
import stat
import sys
from pathlib import Path

import pytest
from livingproof_capability_report.site import assemble as reporter

import reports_restore
import require_reports
import site_action

ROOT = Path(__file__).resolve().parents[1]
OWNER = "Living-Proof-Labs/verification-reports-action"
VIDEO = "evidence/video/trial-01-fail.mp4"
OTHER = "evidence/video/other.mp4"


# --- the gate's player check ---------------------------------------------------------


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        # A browser takes the first src, so a good second one does not make a player.
        (f"<video src='{OTHER}' src='{VIDEO}'></video>", False),
        (f"<video src={OTHER} src={VIDEO}></video>", False),
        (f"<video controls src='{VIDEO}' src='{OTHER}'></video>", True),
        # Only a `source` element counts as a source, not a tag that begins with the word.
        (f"<video><sourcery src='{VIDEO}'></video>", False),
        (f"<video><sources src='{VIDEO}'></video>", False),
        (f"<video><source src='{VIDEO}'></video>", True),
        # A <source> outside a <video> is not a player.
        (f"<audio><source src='{VIDEO}'></audio>", False),
        (f"<picture><source src='{VIDEO}'></picture>", False),
        (f"<source src='{VIDEO}'>", False),
        (
            f"<video src='{OTHER}'></video><p><source src='{VIDEO}'></p>"
            f"<video src='{OTHER}'></video>",
            False,
        ),
        (f"<video src='{OTHER}'></video><source src='{VIDEO}'></video>", False),
    ],
)
def test_the_player_check_reads_the_first_src_and_only_a_source_inside_a_video(page, expected):
    assert require_reports._has_video_player(page, VIDEO) is expected


# --- the gate's run.url rule is the reporter's rule ---------------------------------

_RUN = f"https://github.com/{OWNER}/actions/runs/46115314157"
_URLS = [
    _RUN,
    f"https://github.com/{OWNER}/actions/runs/1",
    f"https://github.com/{OWNER}/actions/runs/",
    f"https://github.com/{OWNER}/actions/runs/0",
    f"https://github.com/{OWNER}/actions/runs/\u0663",
    f"{_RUN}\n",
    f"{_RUN} ",
    f"{_RUN}/",
    f"{_RUN}/job/1",
    f"{_RUN}/attempts/2",
    f"{_RUN}/artifacts/9",
    f"{_RUN}?pr=1",
    f"{_RUN}#summary",
    f"{_RUN}.zip",
    f"{_RUN};a=1",
    f"{_RUN}:80",
    f"{_RUN}&amp;x=1",
    f"{_RUN}%2Fattempts%2F2",
    f"{_RUN}\\attempts\\2",
    "https://github.com/evil/repo/actions/runs/1",
    "https://github.com/Evil-Labs/verification-reports-action/actions/runs/1",
    "https://github.com/living-proof-labs/verification-reports-action/actions/runs/1",
    "https://github.com/Living-Proof-Labs/VERIFICATION-REPORTS-ACTION/actions/runs/1",
    f"https://github.com/{OWNER}/ACTIONS/runs/1",
    f"https://github.com/{OWNER}/%61ctions/runs/1",
    f"https://github.com/{OWNER}/&#97;ctions/runs/1",
    f"https://github.com/{OWNER}/actions/workflows/verify.yml",
    f"https://github.com/{OWNER}/actions",
    f"https://github.com/{OWNER}./actions/runs/1",
    f"https://x@github.com/{OWNER}/actions/runs/1",
    f"https://github.com:443/{OWNER}/actions/runs/1",
    f"https://www.github.com/{OWNER}/actions/runs/1",
    f"http://github.com/{OWNER}/actions/runs/1",
    f"HTTPS://GITHUB.COM/{OWNER}/actions/runs/1",
    f"//github.com/{OWNER}/actions/runs/1",
    "https://github.com.evil.example/o/r/actions/runs/1",
    "https://github.com@evil.example/o/r/actions/runs/1",
    "javascript:alert(1)//actions/runs/1",
    "../../../../etc/passwd",
    "",
    " ",
    None,
    5,
    True,
    [],
    {},
]
_REPOSITORIES = [OWNER, "a/b.c", "a/bxc", "", None, f"{OWNER}\n", OWNER.lower()]
_RECORDS = [None, [], {}, "text", 3, {"run": None}, {"run": []}, {"run": "x"}, {"run": {}}]


@pytest.mark.parametrize("repository", _REPOSITORIES)
def test_the_gate_run_url_rule_gives_the_reporters_answer(repository):
    records = [{"run": {"url": url}} for url in _URLS] + _RECORDS
    for record in records:
        assert require_reports.run_url_problem(
            "x/run.json", record, repository
        ) == reporter.run_url_problem("x/run.json", record, repository), (repository, record)


def test_the_gate_run_url_rule_needs_a_caller_an_id_and_a_literal_repository_name():
    problem = require_reports.run_url_problem
    ok = {"run": {"url": _RUN}}
    assert problem("p", ok, OWNER) is None
    assert problem("p", ok, None) == "p run.url is not this repository's run"
    assert problem("p", ok, "") == "p run.url is not this repository's run"
    assert problem("p", {"run": {"url": f"https://github.com/{OWNER}/actions/runs/"}}, OWNER)
    assert problem("p", {"run": {"url": "https://github.com/a/bxc/actions/runs/1"}}, "a/b.c")
    assert problem("p", {"run": {}}, OWNER) == "p run.url is missing"
    assert problem("p", {"run": {"url": None}}, OWNER) == "p run.url is missing"
    assert problem("p", {"run": {"url": 5}}, OWNER) == "p run.url is not this repository's run"


# --- the site action passes the reporter's output and its last error line on ----------


def _reporter(tmp_path: Path, body: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "livingproof-capability-report"
    exe.write_text(f"#!{sys.executable}\nimport pathlib, sys\n{body}\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "runs").mkdir()
    return bin_dir


def _env(tmp_path: Path, bin_dir: Path, **extra: str) -> dict[str, str]:
    env = {
        "PATH": str(bin_dir),
        "INPUT_RUNS_DIR": str(tmp_path / "runs"),
        "INPUT_OUTPUT_DIR": "reports",
        "INPUT_RESTORE_HISTORY": "false",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REPOSITORY": OWNER,
        "GITHUB_WORKSPACE": str(tmp_path),
    }
    env.update(extra)
    return env


_WRITES_SITE = (
    "r = pathlib.Path(sys.argv[2]); r.mkdir(parents=True, exist_ok=True)\n"
    "(r / 'site.json').write_text('{\"runs\": 1}')\n"
)


def test_the_reporters_output_and_warnings_reach_the_action_logs(tmp_path, capsys):
    body = _WRITES_SITE + (
        "print('::warning::from the reporter')\nprint('to stderr', file=sys.stderr)\n"
    )
    site_action.run_site_action(_env(tmp_path, _reporter(tmp_path, body)))
    seen = capsys.readouterr()
    assert "::warning::from the reporter" in seen.out
    assert "to stderr" in seen.err


def test_a_failed_site_command_reports_its_last_error_line_without_the_prefix(tmp_path):
    body = (
        "print('first line', file=sys.stderr)\n"
        "print('Error: the last line', file=sys.stderr)\n"
        "sys.exit(2)"
    )
    with pytest.raises(site_action.SiteError) as raised:
        site_action.run_site_action(_env(tmp_path, _reporter(tmp_path, body)))
    assert str(raised.value) == ("livingproof-capability-report site command failed: the last line")


def test_a_failed_site_command_with_no_message_reports_its_exit_code(tmp_path):
    with pytest.raises(site_action.SiteError, match=r"failed: exit 3$"):
        site_action.run_site_action(_env(tmp_path, _reporter(tmp_path, "sys.exit(3)")))


@pytest.mark.parametrize("value", [None, "", "  "])
def test_the_site_action_needs_the_caller_repository_before_it_runs_the_reporter(tmp_path, value):
    ran = tmp_path / "ran"
    bin_dir = _reporter(tmp_path, f"pathlib.Path({str(ran)!r}).write_text('x')")
    env = _env(tmp_path, bin_dir)
    if value is None:
        del env["GITHUB_REPOSITORY"]
    else:
        env["GITHUB_REPOSITORY"] = value
    with pytest.raises(site_action.SiteError, match="GITHUB_REPOSITORY is required"):
        site_action.run_site_action(env)
    assert not ran.exists()


def test_the_caller_repository_is_passed_without_surrounding_whitespace(tmp_path):
    log = tmp_path / "argv.json"
    body = (
        _WRITES_SITE + f"pathlib.Path({str(log)!r}).write_text(__import__('json').dumps(sys.argv))"
    )
    site_action.run_site_action(
        _env(tmp_path, _reporter(tmp_path, body), GITHUB_REPOSITORY=f"  {OWNER}\t")
    )
    argv = json.loads(log.read_text())
    assert argv[argv.index("--repository") + 1] == OWNER


# --- `reports_restore.py restore`: docs.yml runs it only on a deploy ------------------


def test_the_restore_command_hands_every_option_to_restore(tmp_path, monkeypatch):
    calls = []

    def fake_restore(*args, **kwargs):
        calls.append((args, kwargs))
        return None

    monkeypatch.setattr(reports_restore, "restore", fake_restore)
    monkeypatch.delenv("SITE_URL", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "env/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "77")
    monkeypatch.setenv("GH_TOKEN", "token-from-env")
    status = reports_restore.main(
        [
            "restore",
            str(tmp_path / "reports"),
            "--repository",
            "o/r",
            "--branch",
            "release",
            "--current-run",
            "5",
            "--dry-run",
            "--site-url",
            "https://example.test/x/",
            "--pages-artifact",
            "github-pages-9",
            "--pages-job",
            "deploy",
            "--deploy-workflow",
            "docs.yml",
            "--reset-history",
        ]
    )
    assert status == 0
    ((args, kwargs),) = calls
    assert args == (tmp_path / "reports", "o/r", "release", 5, "token-from-env")
    assert kwargs == {
        "dry_run": True,
        "site_url": "https://example.test/x/",
        "reset_history": True,
        "pages_artifact": "github-pages-9",
        "pages_job": "deploy",
        "deploy_workflow": "docs.yml",
    }


def test_the_restore_command_defaults_come_from_the_workflow_environment(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(reports_restore, "restore", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setenv("GITHUB_REPOSITORY", "env/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "77")
    monkeypatch.setenv("SITE_URL", "https://example.test/env/")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "fallback-token")
    assert reports_restore.main(["restore", str(tmp_path)]) == 0
    ((args, kwargs),) = calls
    assert args == (tmp_path, "env/repo", "main", 77, "fallback-token")
    assert kwargs["site_url"] == "https://example.test/env/"
    assert kwargs["reset_history"] is False and kwargs["dry_run"] is False
    assert kwargs["pages_artifact"] is None and kwargs["pages_job"] is None


def test_a_failed_restore_exits_non_zero_and_says_why(tmp_path, monkeypatch, capsys):
    def refuse(*args, **kwargs):
        raise reports_restore.SiteError("no Pages artifact")

    monkeypatch.setattr(reports_restore, "restore", refuse)
    assert reports_restore.main(["restore", str(tmp_path)]) == 1
    assert "error: no Pages artifact" in capsys.readouterr().err


def test_docs_yml_passes_its_reset_choice_and_site_url_to_the_site_action():
    # Since #74 the production job restores inside the site action (restore-root), not
    # with a separate `reports_restore.py restore` step; branch defaults to main.
    text = (ROOT / ".github/workflows/docs.yml").read_text(encoding="utf-8")
    assert "reports_restore.py restore" not in text
    assert "reset-history: ${{ inputs.reset_history && 'true' || 'false' }}" in text
    assert "restore-root: artifacts/site/reports" in text
    assert "restore-history: 'true'" in text
    assert "site-url: https://docs.livingprooflabs.com/verification-reports-action/" in text


# --- one reporter pin, in every file that names it ------------------------------------


def test_the_reporter_pin_is_one_release_and_one_digest_in_every_file():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    pinned = re.search(r'"livingproof-capability-report==([0-9.]+)"', pyproject)
    assert pinned
    version = pinned[1]
    wheel = (
        "https://github.com/Living-Proof-Labs/livingproof-capability-report/releases/download/"
        f"v{version}/livingproof_capability_report-{version}-py3-none-any.whl"
    )
    assert f'livingproof-capability-report = {{ url = "{wheel}" }}' in pyproject
    digests = set()
    for name in ("site/requirements.in", "site/requirements.txt"):
        text = (ROOT / name).read_text(encoding="utf-8")
        found = re.findall(re.escape(wheel) + r"#sha256=([0-9a-f]{64})", text)
        assert len(found) == 1, name
        digests.add(found[0])
    assert re.search(
        rf"--hash=sha256:({next(iter(digests))})\b",
        (ROOT / "site/requirements.txt").read_text(encoding="utf-8"),
    )
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert f'{{ url = "{wheel}", hash = "sha256:{next(iter(digests))}" }}' in lock
    assert f'version = "{version}"\nsource = {{ url = "{wheel}" }}' in lock
    assert len(digests) == 1
    assert not re.search(r"capability-report[^\n]*3\.1[0-9]\.", lock.replace(version, ""))


# --- nothing that ships or documents still names what moved ---------------------------

_MOVED = re.compile(r"reports_(?:site|render|common)\b|site_golden|src/assets|renderReport")


def test_no_workflow_doc_action_or_script_names_a_module_that_moved():
    paths = [
        *(ROOT / ".github").rglob("*"),
        *(ROOT / "docs").rglob("*"),
        *(ROOT / "site").rglob("*"),
        *(ROOT / "src").glob("*"),
        *(ROOT / "tools").glob("*"),
        *ROOT.glob("*.md"),
        *ROOT.glob("*.toml"),
        *ROOT.glob("*.yml"),
        *ROOT.glob("*.json"),
    ]
    hits = [
        f"{path.relative_to(ROOT)}: {_MOVED.search(text)[0]}"
        for path in paths
        if path.is_file() and path.suffix not in {".png", ".mp4", ".mcap"}
        for text in [path.read_text(encoding="utf-8", errors="replace")]
        if _MOVED.search(text)
    ]
    assert hits == []
