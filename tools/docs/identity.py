# SPDX-License-Identifier: Apache-2.0
"""Validate supplied build identities; never select a release version."""

import re

STABLE = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"


def validate(sha, dirty, env, tag_commit=None):
    kind = env.get("DOCS_KIND", "verification")
    version = env.get("MANUAL_VERSION", "")
    result = {"kind": kind, "version": version or "development", "release_tag": ""}
    if kind == "verification":
        if version:
            raise ValueError("Verification-only build cannot claim a version")
        return result
    if dirty:
        raise ValueError("Versioned build requires clean source")
    if kind == "candidate":
        pr, run, attempt = (
            env.get(k, "") for k in ("PR_NUMBER", "GITHUB_RUN_NUMBER", "GITHUB_RUN_ATTEMPT")
        )
        run_id = env.get("GITHUB_RUN_ID", "")
        if not all(re.fullmatch(r"[1-9][0-9]*", x) for x in (pr, run, run_id, attempt)):
            raise ValueError("Missing candidate run identity")
        suffix = f"-pr.{pr}.{run}.{attempt}"
        if not version.endswith(suffix) or not re.fullmatch(STABLE, version.removesuffix(suffix)):
            raise ValueError("Candidate must match shared unique PR/run/attempt version")
        if env.get("CANDIDATE_COMMIT") != sha:
            raise ValueError("Candidate source mismatch")
        result.update(run_id=run_id, run_attempt=attempt)
    elif kind == "release":
        tag = env.get("RELEASE_TAG")
        if (
            not re.fullmatch(STABLE, version)
            or version != env.get("RELEASE_VERSION")
            or tag != "v" + version
            or env.get("RELEASE_COMMIT") != sha
            or tag_commit != sha
        ):
            raise ValueError("Released tag, source and manual version must agree")
        result["release_tag"] = tag
    else:
        raise ValueError("Unknown documentation build kind")
    return result
