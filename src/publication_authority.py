# SPDX-License-Identifier: Apache-2.0
"""Authenticated Pages publication proof; never consume local logs or receipts.

The caller approves exact workflow bytes once, independently of evaluator pins.
Only actions:read and contents:read endpoints are used. The successful pinned
production deploy job is the deployment proof (not the REST deployments API).
"""

import base64
import hashlib
import json
import re
import urllib.error

from reports_common import SiteError

DEPLOY_ACTION = "actions/deploy-pages@d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e"
MAX_LOG_BYTES = 1024 * 1024


def _require(condition, message):
    if not condition:
        raise SiteError("Unverified Pages publication: " + message)


def verify(artifact, archive, repository, branch, token, api, policy):
    try:
        _verify(artifact, archive, repository, branch, token, api, policy)
    except (KeyError, TypeError, ValueError, urllib.error.URLError) as error:
        raise SiteError(
            "Unverified Pages publication: incomplete or inaccessible evidence"
        ) from error


def _verify(artifact, archive, repository, branch, token, api, policy):
    """Verify API records and archive before extraction; return no reusable receipt.

    `policy` is trusted caller configuration, never loaded from the archive.
    Its source fingerprints must cover both the entry workflow and reusable
    production workflow, whose gates and fixed deploy step were reviewed.
    """
    from reports_restore import _api_url, _read_following_redirects, _read_json

    _require(api == "https://api.github.com", "unsupported API origin")
    _require(repository == policy["repository"] and branch == policy["branch"], "caller identity")
    run_id = artifact.get("workflow_run", {}).get("id")
    _require(type(run_id) is int and type(artifact.get("id")) is int, "artifact identity")
    prefix = ("repos", repository)

    def get(*parts, query=None):
        return _read_json(_api_url(api, *prefix, *parts, query=query), token)

    # Re-fetch the artifact; a list response or a caller-supplied URL is not authority.
    actual = get("actions/artifacts", artifact["id"])
    _require(actual == artifact, "artifact metadata changed")
    _require(
        actual.get("expired") is False and actual.get("name") == policy["artifact"],
        "artifact state",
    )
    checksum = actual.get("digest", "")
    _require(re.fullmatch(r"sha256:[0-9a-f]{64}", checksum), "missing archive digest")
    _require(checksum == "sha256:" + hashlib.sha256(archive).hexdigest(), "archive digest")
    run = get("actions/runs", run_id)
    sha = run.get("head_sha", "")
    _require(re.fullmatch(r"[0-9a-f]{40}", sha), "run commit")
    _require(run.get("id") == run_id and run.get("head_branch") == branch, "run identity")
    _require(
        run.get("path") == policy["workflow"] and run.get("event") == policy["event"], "workflow"
    )
    _require(
        run.get("status") == "completed" and run.get("conclusion") == "success",
        "run not successful",
    )
    own = get()
    for field in ("repository", "head_repository"):
        _require(
            run.get(field, {}).get("id") == own.get("id")
            and run[field].get("full_name") == repository,
            "repository",
        )
    arun = actual["workflow_run"]
    _require(arun.get("head_sha") == sha and arun.get("head_branch") == branch, "artifact commit")
    _require(
        arun.get("repository_id") == own["id"] and arun.get("head_repository_id") == own["id"],
        "artifact repository",
    )
    files = policy["source_sha256"]
    _require(
        set(files) == {policy["workflow"], policy["reusable_workflow"]},
        "required workflow fingerprints",
    )
    for path, expected in files.items():
        _require(re.fullmatch(r"[0-9a-f]{64}", expected), "invalid source fingerprint")
        content = get("contents", path, query={"ref": sha})
        _require(
            content.get("type") == "file" and content.get("encoding") == "base64", "workflow source"
        )
        raw = base64.b64decode(content["content"])
        _require(hashlib.sha256(raw).hexdigest() == expected, "unapproved workflow source")
    jobs = get(
        "actions/runs", run_id, "attempts", run["run_attempt"], "jobs", query={"per_page": 100}
    )
    _require(jobs.get("total_count", 101) <= 100, "too many jobs")
    matched = [j for j in jobs.get("jobs", []) if j.get("name") == policy["job"]]
    _require(len(matched) == 1, "ambiguous deploy job")
    job = get("actions/jobs", matched[0]["id"])
    _require(
        job.get("run_id") == run_id
        and job.get("run_attempt") == run["run_attempt"]
        and job.get("head_sha") == sha,
        "job binding",
    )
    _require(
        job.get("status") == "completed" and job.get("conclusion") == "success", "deploy failed"
    )
    steps = job.get("steps", [])
    expected_steps = [
        "Set up job",
        "Require published release at current protected main",
        "Run " + DEPLOY_ACTION,
        "Complete job",
    ]
    _require([s.get("name") for s in steps] == expected_steps, "unexpected deploy steps")
    _require(
        all(s.get("status") == "completed" and s.get("conclusion") == "success" for s in steps),
        "deploy step failed",
    )
    logs = _read_following_redirects(
        _api_url(api, *prefix, "actions/jobs", job["id"], "logs"), token, max_bytes=MAX_LOG_BYTES
    )
    verify_log(logs, actual["id"], sha)


def verify_log(raw, artifact_id, commit):
    """Strict known pinned-action grammar, after authenticated source/job checks."""
    _require(len(raw) <= MAX_LOG_BYTES, "oversized deploy log")
    lines = []
    for line in raw.decode("utf-8-sig").splitlines():
        match = re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+Z (.*)", line)
        _require(match is not None, "malformed deploy log")
        lines.append(match[1])
    marker = "##[group]Run " + DEPLOY_ACTION
    _require(lines.count(marker) == 1, "ambiguous deploy step log")
    tail = lines[lines.index(marker) + 1 :]
    marker = "Creating Pages deployment with payload:"
    _require(tail.count(marker) == 1, "ambiguous deployment payload")
    pos = tail.index(marker)
    # Exact bounded payload shape from the immutable action. No substring lookup,
    # workspace logs, unmasked token, or payload from another step is accepted.
    payload_lines = tail[pos + 1 : pos + 6]
    _require(
        len(payload_lines) == 5 and payload_lines[0] == "{" and payload_lines[-1] == "}",
        "payload shape",
    )

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "duplicate deployment payload field")
            result[key] = value
        return result

    payload = json.loads("\n".join(payload_lines), object_pairs_hook=unique_object)
    _require(type(payload.get("artifact_id")) is int, "invalid deployment artifact ID")
    _require(
        payload == {"artifact_id": artifact_id, "pages_build_version": commit, "oidc_token": "***"},
        "deployment payload binding",
    )
    created = f"Created deployment for {commit}, ID: {commit}"
    _require(tail.count(created) == 1 and tail.index(created) > pos + 5, "deployment creation")
    _require(
        tail.count("Reported success!") == 1
        and tail.index("Reported success!") > tail.index(created),
        "deployment success",
    )
    _require(
        not any(line.startswith("##[group]Run ") for line in tail),
        "unexpected later executable step",
    )
