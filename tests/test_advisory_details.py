# SPDX-License-Identifier: Apache-2.0
"""Advisory details files are copied because a result cites them.

T1–T4. Each test fails when its fix is reverted.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from livingproof_capability_report.core import digest, render

import reports_restore
import reports_site
import site_action
from details_files import cited_details_uris
from drop_advisory_lines import DropError, keep_lines
from evaluators_run import apply_configuration, write_report_checksums
from test_evaluators_run import verifier_src
from test_site import (
    REPO,
    REPO_ID,
    _artifact_entry,
    _make_run_folder,
    _pages_zip_from_docs_root,
    _workflow_run_payload,
)

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = ROOT / "tests/fixtures/old-runs/published"
TEMPLATE_REPORT = (
    PUBLISHED / "production/unitree-g1/stock-mujoco-hallway/runs/45754832566/report.json"
)
THRESHOLDS = (
    PUBLISHED / "production/unitree-g1/stock-mujoco-hallway/runs/45754832566/thresholds.json"
)
HEALTH_LINE = "Living-Proof-Labs/livingproof-evaluator-health@v1.2.0/livingproof-evaluator.json"
EXAMPLE_COMMIT = "a" * 40
EXAMPLE_LINE = f"Living-Proof-Labs/example-advisory@{EXAMPLE_COMMIT}/livingproof-evaluator.json"
RUN_LINE = "Living-Proof-Labs/livingproof-evaluator-run@v1.9.0/livingproof-evaluator.json"


@pytest.fixture(scope="module", autouse=True)
def _capability_verifier():
    verifier_src()


OBSERVATIONS = "observations.json"
NOTES = "notes.json"


def _template() -> dict:
    return json.loads(TEMPLATE_REPORT.read_text(encoding="utf-8"))


def _advisory(identifier: str, title: str, uri: str, payload: bytes) -> dict:
    return {
        "format": "livingproof-evaluator-result/1.3.0",
        "evaluator": {"id": identifier, "kind": "advisory", "version": "1.0.0"},
        "descriptor": {
            "title": title,
            "version": "1.0.0",
            "purpose": "Record an observation. It does not grade.",
        },
        "advisory": {
            "summary": f"{title} saw the recorded trial.",
            "details": f"{title} details stay with the report.",
            "details_file": {
                "uri": uri,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "media_type": "application/json",
            },
        },
    }


def _provenance(identifier: str, line: str) -> dict:
    return {
        "line": line,
        "repo": "Living-Proof-Labs/example",
        "ref": "v1.0.0",
        "commit": "b" * 40,
        "descriptor_sha256": "ab" * 32,
        "package_name": "example",
        "installed_version": "1.0.0",
        "exit_code": 0,
        "outcome": "result",
        "evaluator_id": identifier,
    }


def _render_report(template: dict, bundle: dict, report_dir: Path) -> str:
    document = json.loads(json.dumps(template))
    document["source_bundle"] = bundle
    document["source_bundle_sha256"] = digest(bundle)
    report_dir.mkdir(parents=True, exist_ok=True)
    html = render(document, "html")
    (report_dir / "report.json").write_text(render(document, "json"), encoding="utf-8")
    (report_dir / "report.html").write_text(html, encoding="utf-8")
    (report_dir / "report.md").write_text(render(document, "markdown"), encoding="utf-8")
    (report_dir / "report.txt").write_text(render(document, "table"), encoding="utf-8")
    return html


def _sums(folder: Path) -> dict[str, str]:
    listed = {}
    for line in (folder / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest_hex, _, name = line.partition("  ")
        listed[name] = digest_hex
    return listed


def _assemble(report_dir: Path, output: Path) -> Path:
    download = report_dir.parent / "download"
    artifact = download / "reports" / "vra-demo-capability-report-stock"
    if artifact.exists():
        shutil.rmtree(artifact)
    shutil.copytree(report_dir, artifact)
    script_path = report_dir.parent / "assemble.mjs"
    module = (ROOT / "src" / "assemble.mjs").as_uri()
    script_path.write_text(
        f"import {{ assembleVerifierDefinition }} from '{module}';\n"
        "const item = JSON.parse(process.env.ITEM);\n"
        "const env = JSON.parse(process.env.ASSEMBLE_ENV);\n"
        "assembleVerifierDefinition(item, process.env.DOWNLOAD, process.env.OUTPUT, env, "
        "'0.0.0');\n",
        encoding="utf-8",
    )
    item = {
        "id": "demo",
        "path": "demo.toml",
        "execution": "capability-verifier",
        "configurations": ["stock"],
        "artifactPrefix": "vra-demo-",
        "procedure": "walk-hallway",
        "robot": {
            "repository": "Living-Proof-Labs/livingproof-unitree-g1",
            "ref": "d554c8243a12849dc7fe8acc1921bb3d4e5239a7",
        },
        "callerRepository": "Living-Proof-Labs/verification-reports-action",
        "provenance": [],
        "lock": {"releases": {}},
    }
    env = {
        "GITHUB_REPOSITORY": "Living-Proof-Labs/verification-reports-action",
        "GITHUB_RUN_ID": "424242",
        "GITHUB_SERVER_URL": "https://github.com",
        "VERIFICATION_CREATED": "2026-11-27T12:00:00Z",
    }
    completed = subprocess.run(
        ["node", str(script_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "ITEM": json.dumps(item),
            "ASSEMBLE_ENV": json.dumps(env),
            "DOWNLOAD": str(download),
            "OUTPUT": str(output),
        },
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return output / "demo" / "stock"


def _publish(source: Path, reports: Path) -> Path:
    folder, _key, record = reports_site.add_run_folder(
        reports,
        source,
        restored_keys=set(),
        seen_in_batch=set(),
    )
    reports_site.publish_run_page(folder, record, now="2026-11-27T12:00:00Z", days=7, trial=False)
    return folder


def _ids_in_action_code() -> list[str]:
    hits = []
    roots = [ROOT / "src", ROOT / "action.yml", ROOT / ".github/workflows"]
    for root in roots:
        files = (
            [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        )
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            for name in ("livingproof-health", "example-advisory"):
                if name in text:
                    hits.append(f"{path.relative_to(ROOT)}: {name}")
    return hits


def test_two_advisory_evaluators_are_copied_and_shown_without_naming_them(tmp_path):
    """T1. Both details files survive checksums, assemble and the site, and both cards render."""
    template = _template()
    observations = b'{"samples": 1}\n'
    notes = b'{"note": "example"}\n'
    bundle_path = tmp_path / "bundle" / "manifest.json"
    bundle_path.parent.mkdir()
    bundle_path.write_text(json.dumps(template["source_bundle"]) + "\n", encoding="utf-8")
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    shutil.copyfile(THRESHOLDS, report_dir / "thresholds.json")
    seen = {}

    def runner(command, **kwargs):
        del kwargs
        seen["report_dir"] = "--report-dir" in command
        lines = Path(command[command.index("--lines") + 1]).read_text(encoding="utf-8").splitlines()
        seen["lines"] = lines
        target = Path(command[command.index("--report-dir") + 1]) if seen["report_dir"] else None
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        assessment = bundle.setdefault("assessment", {})
        results = {}
        records = []
        if HEALTH_LINE in lines:
            results["livingproof-health"] = _advisory(
                "livingproof-health", "Health", OBSERVATIONS, observations
            )
            records.append(_provenance("livingproof-health", HEALTH_LINE))
            if target is not None:
                (target / OBSERVATIONS).write_bytes(observations)
        if EXAMPLE_LINE in lines:
            results["example-advisory"] = _advisory("example-advisory", "Example", NOTES, notes)
            records.append(_provenance("example-advisory", EXAMPLE_LINE))
            if target is not None:
                (target / NOTES).write_bytes(notes)
        if results:
            assessment["advisory_results"] = results
        bundle_path.write_text(json.dumps(bundle) + "\n", encoding="utf-8")
        out = Path(command[command.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "evaluators-provenance.json").write_text(
            json.dumps(records) + "\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    saved = {}

    def render_hook(image, manifest, directory, links):
        del links
        assert image.startswith("ghcr.io/living-proof-labs/livingproof-capability-report:3.17.0@")
        bundle = json.loads(Path(manifest).read_text(encoding="utf-8"))
        saved["html"] = _render_report(template, bundle, directory)

    failed = apply_configuration(
        lines=[HEALTH_LINE, EXAMPLE_LINE],
        evidence=tmp_path / "evidence.json",
        bundle_path=bundle_path,
        report_dir=report_dir,
        out=tmp_path / "out",
        provenance_records=[
            {
                "name": "livingproof-capability-report",
                "tag": "v3.17.0",
                "digest": "sha256:" + "ab" * 32,
            }
        ],
        runner=runner,
        render=render_hook,
    )
    assert failed is False
    assert seen["report_dir"] is True
    sums = _sums(report_dir)
    assert sums[OBSERVATIONS] == hashlib.sha256(observations).hexdigest()
    assert sums[NOTES] == hashlib.sha256(notes).hexdigest()
    assert (report_dir / OBSERVATIONS).read_bytes() == observations
    assert (report_dir / NOTES).read_bytes() == notes
    assembled = _assemble(report_dir, tmp_path / "assembled")
    assert (assembled / OBSERVATIONS).read_bytes() == observations
    assert (assembled / NOTES).read_bytes() == notes
    assert _sums(assembled)[OBSERVATIONS] == sums[OBSERVATIONS]
    assert _sums(assembled)[NOTES] == sums[NOTES]
    published = _publish(assembled, tmp_path / "site")
    page = (published / "index.html").read_text(encoding="utf-8")
    assert (published / OBSERVATIONS).read_bytes() == observations
    assert (published / NOTES).read_bytes() == notes
    assert _sums(published)[OBSERVATIONS] == sums[OBSERVATIONS]
    assert "evaluator-livingproof-health" in page
    assert "evaluator-example-advisory" in page
    assert page.count(">Advisory<") >= 2
    assert _ids_in_action_code() == []
    assert "eval-cue" in saved["html"]


def test_no_advisory_line_publishes_the_run_only_page(tmp_path):
    """T2. No advisory line means no card, no details file and no placeholder."""
    template = _template()
    bundle_path = tmp_path / "bundle" / "manifest.json"
    bundle_path.parent.mkdir()
    bundle_path.write_text(json.dumps(template["source_bundle"]) + "\n", encoding="utf-8")
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    shutil.copyfile(THRESHOLDS, report_dir / "thresholds.json")
    (report_dir / NOTES).write_bytes(b'{"uncited": true}\n')

    def runner(command, **kwargs):
        del kwargs
        lines = Path(command[command.index("--lines") + 1]).read_text(encoding="utf-8").splitlines()
        assert lines == [RUN_LINE]
        out = Path(command[command.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "evaluators-provenance.json").write_text(
            json.dumps([{"outcome": "result"}]) + "\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    saved = {}

    def render_hook(image, manifest, directory, links):
        del image, links
        bundle = json.loads(Path(manifest).read_text(encoding="utf-8"))
        assert "advisory_results" not in (bundle.get("assessment") or {})
        saved["html"] = _render_report(template, bundle, directory)

    apply_configuration(
        lines=[RUN_LINE],
        evidence=tmp_path / "evidence.json",
        bundle_path=bundle_path,
        report_dir=report_dir,
        out=tmp_path / "out",
        provenance_records=[
            {
                "name": "livingproof-capability-report",
                "tag": "v3.17.0",
                "digest": "sha256:" + "ab" * 32,
            }
        ],
        runner=runner,
        render=render_hook,
    )
    assert NOTES not in _sums(report_dir)
    assembled = _assemble(report_dir, tmp_path / "assembled")
    assert not (assembled / NOTES).exists()
    assert not (assembled / OBSERVATIONS).exists()
    (assembled / NOTES).write_bytes(b'{"uncited": true}\n')
    published = _publish(assembled, tmp_path / "site")
    page = (published / "index.html").read_text(encoding="utf-8")
    assert not (published / NOTES).exists()
    assert ">Advisory<" not in page
    assert "NOT RUN" not in page
    assert "placeholder" not in page.lower()
    direct = _render_report(template, template["source_bundle"], tmp_path / "direct")
    assert saved["html"] == direct
    record = json.loads((published / "run.json").read_text(encoding="utf-8"))
    report = json.loads((published / "report.json").read_text(encoding="utf-8"))
    expected = reports_site.scrub_reporter_html(
        (assembled / "report.html").read_text(encoding="utf-8"),
        record,
        trial=False,
        expired=False,
        days=7,
    )
    expected = reports_site.unlink_unpublished_evidence(expected, published)
    expected = reports_site.with_site_nav(
        expected,
        overview_href="../../../../../index.html",
        reports_href="../../../../index.html",
    )
    expected = reports_site.with_spec_provenance(expected, record, report)
    expected = reports_site.checked_report_html(expected, str(published / "index.html"))
    assert page == expected


def test_advisory_kind_lines_are_dropped_without_naming_them():
    """A descriptor whose kind is advisory is left out. Other kinds stay."""

    def fetch(url: str) -> bytes:
        if "/git/ref/tags/" in url:
            return json.dumps({"object": {"type": "commit", "sha": "c" * 40}}).encode()
        if url.endswith("/advisory.json"):
            return b'{"kind": "advisory"}'
        return b'{"kind": "integrity"}'

    kept, dropped = keep_lines(
        "Owner/one@v1.0.0/advisory.json\nOwner/two@v1.2.3/kept.json\n",
        fetch,
    )
    assert dropped == 1
    assert kept == ["Owner/two@v1.2.3/kept.json"]


def _install_recording_opener(redirects: dict[str, str]):
    """A local opener. No sockets. ``redirects`` maps a URL to a Location."""
    import io
    from email.message import EmailMessage
    from urllib.response import addinfourl

    seen: list[tuple[str, str | None]] = []

    class _Handler(urllib.request.BaseHandler):
        handler_order = 100

        def http_open(self, req):
            return self._respond(req)

        def https_open(self, req):
            return self._respond(req)

        def _respond(self, req):
            seen.append((req.full_url, req.get_header("Authorization")))
            location = redirects.get(req.full_url)
            headers = EmailMessage()
            if location is not None:
                headers["Location"] = location
                response = addinfourl(io.BytesIO(b""), headers, req.full_url, 302)
            else:
                response = addinfourl(io.BytesIO(b"{}"), headers, req.full_url, 200)
            response.msg = "ok"
            return response

    previous = urllib.request._opener
    urllib.request.install_opener(
        urllib.request.build_opener(_Handler, urllib.request.HTTPRedirectHandler)
    )
    return seen, previous


def test_the_token_is_sent_only_to_api_github_com(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    seen, previous = _install_recording_opener({})
    try:
        from drop_advisory_lines import fetch_bytes

        fetch_bytes("https://api.github.com/repos/Owner/one/git/ref/tags/v1.0.0")
        fetch_bytes("https://raw.githubusercontent.com/Owner/one/abc/file.json")
    finally:
        urllib.request.install_opener(previous)
    by_url = dict(seen)
    assert by_url["https://api.github.com/repos/Owner/one/git/ref/tags/v1.0.0"] == (
        "Bearer test-token"
    )
    assert by_url["https://raw.githubusercontent.com/Owner/one/abc/file.json"] is None


def test_the_token_is_not_forwarded_on_redirect(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    cross = "https://api.github.com/repos/Owner/one/git/ref/tags/v1.0.0"
    downgrade = "https://api.github.com/repos/Owner/one/git/tags/" + "c" * 40
    seen, previous = _install_recording_opener(
        {
            cross: "https://example.test/landed",
            downgrade: "http://example.test/plain",
        }
    )
    try:
        from drop_advisory_lines import fetch_bytes

        fetch_bytes(cross)
        fetch_bytes(downgrade)
    finally:
        urllib.request.install_opener(previous)
    by_url = dict(seen)
    assert by_url[cross] == "Bearer test-token"
    assert by_url[downgrade] == "Bearer test-token"
    assert by_url["https://example.test/landed"] is None
    assert by_url["http://example.test/plain"] is None


def test_a_refused_fetch_names_the_url():
    url = "https://api.github.com/repos/Owner/one/git/ref/tags/v1.0.0"

    def fetch(requested: str) -> bytes:
        raise urllib.error.HTTPError(requested, 403, "rate limit exceeded", None, None)

    with pytest.raises(DropError, match=url):
        keep_lines("Owner/one@v1.0.0/advisory.json\n", fetch)


def test_checksums_accept_one_advisory_result_or_a_list(tmp_path):
    payload = b'{"n":1}\n'
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    (report_dir / "report.json").write_text("{}\n", encoding="utf-8")
    (report_dir / NOTES).write_bytes(payload)
    one = {"advisory": {"details_file": {"uri": NOTES}}}
    for stored in (one, [one], {"example-advisory": one}, {"example-advisory": [one]}):
        write_report_checksums(report_dir, {"assessment": {"advisory_results": stored}})
        assert _sums(report_dir)[NOTES] == hashlib.sha256(payload).hexdigest()
    assert cited_details_uris({"assessment": {"advisory_results": [one]}}) == [NOTES]


@pytest.mark.parametrize(
    ("uri", "mutate", "message"),
    [
        (NOTES, "sha", "does not match SHA256SUMS"),
        ("../x.json", "path", "is not a relative path"),
        ("/tmp/x.json", "absolute", "is absolute"),
        (NOTES, "symlink", "is a symlink"),
        (NOTES, "missing", "not listed in SHA256SUMS"),
    ],
)
def test_the_site_refuses_an_unsafe_cited_details_file(tmp_path, uri, mutate, message):
    """T3 site guards. Reverting one guard lets that case through."""
    data = b"{}\n"
    digest_hex = "0" * 64 if mutate == "sha" else hashlib.sha256(data).hexdigest()
    listed = "other.json" if mutate == "missing" else uri
    source = tmp_path / "source"
    source.mkdir()
    if mutate in {"path", "absolute"}:
        (source / "SHA256SUMS").write_text("", encoding="utf-8")
    else:
        (source / "SHA256SUMS").write_text(f"{digest_hex}  {listed}\n", encoding="utf-8")
    if mutate == "symlink":
        (source / "target.json").write_bytes(data)
        (source / NOTES).symlink_to("target.json")
    elif mutate == "missing":
        (source / NOTES).write_bytes(data)
        (source / "other.json").write_bytes(data)
    elif mutate not in {"path", "absolute"}:
        (source / uri).write_bytes(data)
    report = {
        "source_bundle": {
            "assessment": {
                "advisory_results": {"cited": {"advisory": {"details_file": {"uri": uri}}}}
            }
        }
    }
    with pytest.raises(reports_site.SiteError, match=message):
        reports_site._publish_cited_details(source, tmp_path / "out", report)


def test_a_refused_advisory_result_publishes_with_no_details_file(tmp_path):
    """T3. advisory_not_run is a Reporter NOT RUN card and does not fail the build."""
    template = _template()
    bundle = json.loads(json.dumps(template["source_bundle"]))
    bundle["assessment"]["advisory_not_run"] = {
        "example-advisory": {"outcome": "refused", "reason": "the evaluator refused the run"}
    }
    report_dir = tmp_path / "report"
    html = _render_report(template, bundle, report_dir)
    assert "NOT RUN" in html
    assert "evaluator-example-advisory" in html
    shutil.copyfile(THRESHOLDS, report_dir / "thresholds.json")
    for name in ("report.json", "report.html", "report.md", "report.txt", "thresholds.json"):
        pass
    write_report_checksums(report_dir, bundle)
    assembled = _assemble(report_dir, tmp_path / "assembled")
    published = _publish(assembled, tmp_path / "site")
    page = (published / "index.html").read_text(encoding="utf-8")
    assert "NOT RUN" in page
    assert not (published / NOTES).exists()
    assert not (published / OBSERVATIONS).exists()


def _history_transport(monkeypatch, docs: Path) -> None:
    archive = _pages_zip_from_docs_root(docs)

    def request(url, token):
        del token
        path = urllib.parse.urlsplit(url).path.rstrip("/")
        if path == f"/repos/{REPO}":
            value = {"id": REPO_ID, "full_name": REPO}
        elif path.endswith("/jobs"):
            value = {"jobs": [{"name": "deploy", "conclusion": "success"}]}
        elif "/actions/runs/" in path:
            value = _workflow_run_payload(90, event="push")
        else:
            value = {"artifacts": [_artifact_entry(5, "main", 90, "2026-11-26T00:00:00Z")]}
        return io.BytesIO(json.dumps(value).encode())

    monkeypatch.setattr(reports_restore, "_request", request)
    monkeypatch.setattr(reports_restore, "_open_once", lambda url, token: io.BytesIO(archive))


def _merge_published_history(destination: Path) -> dict[str, bytes]:
    reports = destination / "reports"
    for name in ("production", "peer"):
        shutil.copytree(PUBLISHED / name / "unitree-g1", reports / "unitree-g1", dirs_exist_ok=True)
    kept = {}
    for folder in sorted(reports.glob("*/*/runs/*")):
        for label in ("index.html", "health.json", "run.json", "SHA256SUMS"):
            path = folder / label
            kept[str(path.relative_to(reports))] = path.read_bytes()
        for path in folder.glob("report.*"):
            kept[str(path.relative_to(reports))] = path.read_bytes()
    return kept


def test_old_published_runs_restore_byte_for_byte_and_evidence_still_expires(tmp_path, monkeypatch):
    """T4. Runs published before this change restore, and evidence still expires."""
    docs = tmp_path / "archive"
    original = _merge_published_history(docs)
    _history_transport(monkeypatch, docs)
    new_run = _make_run_folder(
        tmp_path / "incoming",
        "new",
        repository="Living-Proof-Labs/livingproof-unitree-g1",
        variant="unitree-g1-stock-mujoco-hallway",
        run_id="88888888888",
        created="2026-11-27T12:00:00Z",
    )
    output = tmp_path / "restored-site"
    site_action.run_site_action(
        {
            "INPUT_RUNS_DIR": str(new_run.parent),
            "INPUT_OUTPUT_DIR": str(output),
            "INPUT_RESTORE_HISTORY": "true",
            "INPUT_RESET_HISTORY": "false",
            "GITHUB_WORKSPACE": str(tmp_path),
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_RUN_ID": "88888888888",
        }
    )
    restored = output
    for relative, data in original.items():
        assert (restored / relative).read_bytes() == data, relative
    history = (restored / "unitree-g1/stock-mujoco-hallway/index.html").read_text(encoding="utf-8")
    assert "45754832566" in history
    assert "45760910690" in history
    assert "88888888888" in history
    # An old run past retention still builds. Evidence expires. health.json stays.
    expired_docs = tmp_path / "expired-archive"
    _merge_published_history(expired_docs)
    _history_transport(monkeypatch, expired_docs)
    expired = tmp_path / "expired-site"
    site_action.run_site_action(
        {
            "INPUT_RUNS_DIR": str(new_run.parent),
            "INPUT_OUTPUT_DIR": str(expired),
            "INPUT_RESTORE_HISTORY": "true",
            "INPUT_RESET_HISTORY": "false",
            "INPUT_EVIDENCE_RETENTION_DAYS": "7",
            "GITHUB_WORKSPACE": str(tmp_path),
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REPOSITORY": REPO,
            "GITHUB_RUN_ID": "88888888888",
        }
    )
    # The new run's timestamp is the build clock, so force a later clock through render.
    reports_site.render(expired, now="2027-02-01T00:00:00Z", days=7)
    old = expired / "unitree-g1/stock-mujoco-hallway/runs/45754832566"
    assert (old / "health.json").is_file()
    assert not (old / "evidence/video/trial-01-fail.mp4").exists()
    assert not (old / "evidence/mcap/mcap/trial-01.mcap").exists()
    assert not (old / "evidence/mcap/media.json").exists()
