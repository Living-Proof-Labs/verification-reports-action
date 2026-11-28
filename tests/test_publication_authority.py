"""Authenticated production evidence is separate from restored file assertions."""

import base64
import copy
import hashlib
import json
from urllib.parse import urlsplit

import pytest

import publication_authority as authority
import reports_restore
from reports_common import SiteError


def log_bytes(artifact=11, commit="a" * 40):
    lines = [
        "##[group]Run " + authority.DEPLOY_ACTION,
        "##[endgroup]",
        "Creating Pages deployment with payload:",
        *json.dumps(
            {"artifact_id": artifact, "pages_build_version": commit, "oidc_token": "***"}, indent=2
        ).splitlines(),
        f"Created deployment for {commit}, ID: {commit}",
        "Reported success!",
    ]
    return ("\n".join("2026-11-28T07:06:55.0000000Z " + line for line in lines) + "\n").encode()


@pytest.fixture
def authority_api(monkeypatch):
    repo, sha, archive = "owner/repo", "a" * 40, b"authenticated archive"
    own = {"id": 9, "full_name": repo}
    artifact = {
        "id": 11,
        "name": "github-pages",
        "expired": False,
        "digest": "sha256:" + hashlib.sha256(archive).hexdigest(),
        "workflow_run": {
            "id": 22,
            "repository_id": 9,
            "head_repository_id": 9,
            "head_branch": "main",
            "head_sha": sha,
        },
    }
    files = {
        ".github/workflows/release.yml": b"approved entry workflow",
        ".github/workflows/docs.yml": b"approved protected release deploy gates",
    }
    policy = {
        "repository": repo,
        "branch": "main",
        "workflow": ".github/workflows/release.yml",
        "reusable_workflow": ".github/workflows/docs.yml",
        "event": "workflow_run",
        "artifact": "github-pages",
        "job": "deploy-docs / deploy",
        "source_sha256": {p: hashlib.sha256(b).hexdigest() for p, b in files.items()},
    }
    run = {
        "id": 22,
        "head_sha": sha,
        "head_branch": "main",
        "run_attempt": 1,
        "path": policy["workflow"],
        "event": "workflow_run",
        "status": "completed",
        "conclusion": "success",
        "repository": own,
        "head_repository": own,
    }
    job = {
        "id": 33,
        "run_id": 22,
        "run_attempt": 1,
        "head_sha": sha,
        "name": policy["job"],
        "status": "completed",
        "conclusion": "success",
        "steps": [
            {"name": name, "status": "completed", "conclusion": "success"}
            for name in [
                "Set up job",
                "Require published release at current protected main",
                "Run " + authority.DEPLOY_ACTION,
                "Complete job",
            ]
        ],
    }
    responses = {
        "": own,
        "/actions/artifacts/11": artifact,
        "/actions/runs/22": run,
        "/actions/runs/22/attempts/1/jobs": {"total_count": 1, "jobs": [job]},
        "/actions/jobs/33": job,
    }
    for path, raw in files.items():
        responses["/contents/" + path] = {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(raw).decode(),
        }
    calls = []

    def api(url, token):
        assert token == "authorized-test-token"
        assert url.startswith("https://api.github.com/repos/owner/repo")
        calls.append(url)
        return copy.deepcopy(responses[urlsplit(url).path.removeprefix("/repos/owner/repo")])

    def logs(url, token, *, max_bytes):
        assert url == "https://api.github.com/repos/owner/repo/actions/jobs/33/logs"
        assert max_bytes == authority.MAX_LOG_BYTES
        assert token == "authorized-test-token"
        return log_bytes()

    monkeypatch.setattr(reports_restore, "_read_json", api)
    monkeypatch.setattr(reports_restore, "_read_following_redirects", logs)
    return artifact, archive, policy, responses, calls


def verify_fixture(fixture, **changes):
    artifact, archive, policy, _, _ = fixture
    args = dict(
        artifact=artifact,
        archive=archive,
        repository="owner/repo",
        branch="main",
        token="authorized-test-token",
        api="https://api.github.com",
        policy=policy,
    )
    args.update(changes)
    authority.verify(**args)


def test_authenticated_production_archive(authority_api):
    verify_fixture(authority_api)
    assert any("contents/.github/workflows/docs.yml?ref=" in c for c in authority_api[-1])


@pytest.mark.parametrize(
    "fault",
    [
        "digest",
        "missing-digest",
        "source",
        "fork",
        "ref",
        "workflow",
        "run-failed",
        "job-failed",
        "step",
        "job-commit",
        "attempt",
    ],
)
def test_refuses_incompatible_evidence(authority_api, fault):
    artifact, _, _, data, _ = authority_api
    if fault == "digest":
        artifact["digest"] = "sha256:" + "0" * 64
    if fault == "missing-digest":
        del artifact["digest"]
    if fault == "source":
        data["/contents/.github/workflows/docs.yml"]["content"] = base64.b64encode(
            b"changed"
        ).decode()
    if fault == "fork":
        data["/actions/runs/22"]["head_repository"] = {"id": 77, "full_name": "other/repo"}
    if fault == "ref":
        data["/actions/runs/22"]["head_branch"] = "other"
    if fault == "workflow":
        data["/actions/runs/22"]["path"] = ".github/workflows/unapproved.yml"
    if fault == "run-failed":
        data["/actions/runs/22"]["conclusion"] = "failure"
    job = data["/actions/jobs/33"]
    if fault == "job-failed":
        job["conclusion"] = "failure"
    if fault == "step":
        job["steps"][2]["name"] = "Run unapproved/action@main"
    if fault == "job-commit":
        job["head_sha"] = "b" * 40
    if fault == "attempt":
        job["run_attempt"] = 2
    with pytest.raises(SiteError):
        verify_fixture(authority_api)


@pytest.mark.parametrize(
    "fault", ["artifact", "commit", "duplicate", "missing", "later-step", "raw", "oversize"]
)
def test_strict_pinned_deploy_log(fault):
    raw = log_bytes()
    if fault == "artifact":
        raw = log_bytes(artifact=12)
    if fault == "commit":
        raw = log_bytes(commit="b" * 40)
    if fault == "duplicate":
        raw += raw
    if fault == "missing":
        raw = raw.replace(b"Reported success!", b"Failed!")
    if fault == "later-step":
        raw += b"2026-11-28T07:06:56.0000000Z ##[group]Run arbitrary\n"
    if fault == "raw":
        raw = b"Creating Pages deployment with payload: 11 Reported success!"
    if fault == "oversize":
        raw = b"x" * (authority.MAX_LOG_BYTES + 1)
    with pytest.raises(SiteError):
        authority.verify_log(raw, 11, "a" * 40)


def test_log_download_is_bounded_and_requires_https(monkeypatch):
    import io

    class Response(io.BytesIO):
        def read(self, size=-1):
            assert size == 5
            return super().read(size)

    monkeypatch.setattr(reports_restore, "_open_once", lambda *a: Response(b"too many bytes"))
    with pytest.raises(SiteError, match="size bound"):
        reports_restore._read_following_redirects("https://api.github.com/logs", None, max_bytes=4)
    with pytest.raises(SiteError, match="HTTPS"):
        reports_restore._read_following_redirects("http://api.github.com/logs", None, max_bytes=4)


def test_missing_or_denied_api_cannot_authorize(authority_api, monkeypatch):
    import urllib.error

    def denied(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.github.com/", 403, "Forbidden", {}, None)

    monkeypatch.setattr(reports_restore, "_read_json", denied)
    with pytest.raises(SiteError, match="inaccessible evidence"):
        verify_fixture(authority_api)


@pytest.mark.parametrize("state", ["old", "new", "mixed", "future"])
def test_exact_reviewed_workflow_transition(authority_api, state):
    _, _, policy, responses, _ = authority_api
    old = policy.pop("source_sha256")
    new_bytes = {path: ("reviewed transition " + path).encode() for path in old}
    new = {path: hashlib.sha256(raw).hexdigest() for path, raw in new_bytes.items()}
    policy["source_revisions"] = [old, new]
    for index, (path, raw) in enumerate(new_bytes.items()):
        if state == "new" or (state == "mixed" and index == 0):
            responses["/contents/" + path]["content"] = base64.b64encode(raw).decode()
        if state == "future":
            responses["/contents/" + path]["content"] = base64.b64encode(
                b"unreviewed future"
            ).decode()
    if state in {"old", "new"}:
        verify_fixture(authority_api)
    else:
        with pytest.raises(SiteError, match="unapproved workflow source"):
            verify_fixture(authority_api)


def test_production_configuration_matches_only_reviewable_workflow_pairs():
    from pathlib import Path

    root = Path(__file__).parents[1]
    policy = json.loads((root / "config/viewers/publication.json").read_text())
    assert len(policy["source_revisions"]) == 2
    assert policy["source_revisions"][0] == {
        ".github/workflows/release.yml": (
            "cbbff6e61ead6bd09701b50a573f3af2f60006fb7a9737f912c06c23030aa145"
        ),
        ".github/workflows/docs.yml": (
            "03ab8c7ede263c71f169ae52055256ffca72155881666d06542ff2cbed7d5bae"
        ),
    }
    assert policy["source_revisions"][1] == {
        path: hashlib.sha256((root / path).read_bytes()).hexdigest()
        for path in policy["source_revisions"][0]
    }
    workflow = (root / ".github/workflows/docs.yml").read_text()
    assert "python3 src/reports_site.py restore" not in workflow
    assert "restore-root: artifacts/site/reports" in workflow
    assert "publication-approval: config/viewers/publication.json" in workflow
    assert "restore-history: 'true'" in workflow
