# SPDX-License-Identifier: Apache-2.0
import hashlib
import json

import pytest

from reports_common import SiteError
from viewer_release import fetch_release_viewer


@pytest.mark.parametrize("fault", ["", "draft", "commit", "digest", "url", "duplicate"])
def test_only_exact_caller_release_asset_is_loaded(fault):
    raw = b"<html>owned code</html>"
    pin = dict(
        viewer_repository="Example/viewer",
        viewer_tag="v1.2.3",
        viewer_commit="a" * 40,
        viewer_asset="viewer.html",
        viewer_sha256=hashlib.sha256(raw).hexdigest(),
    )
    url = "https://github.com/Example/viewer/releases/download/v1.2.3/viewer.html"
    asset = dict(name="viewer.html", state="uploaded", size=len(raw), browser_download_url=url)
    release = dict(
        tag_name="v1.2.3", draft=False, prerelease=False, published_at="2026-01-01", assets=[asset]
    )
    commit = "a" * 40
    if fault == "draft":
        release["draft"] = True
    if fault == "commit":
        commit = "b" * 40
    if fault == "digest":
        pin["viewer_sha256"] = "0" * 64
    if fault == "url":
        asset["browser_download_url"] = "https://untrusted.invalid/viewer.html"
    if fault == "duplicate":
        release["assets"] *= 2
    reads = []

    def read(request):
        reads.append(request)
        if "/releases/tags/" in request:
            return json.dumps(release).encode()
        if "/git/ref/tags/" in request:
            return json.dumps({"object": {"type": "commit", "sha": commit}}).encode()
        assert request == url
        return raw

    if fault:
        with pytest.raises(SiteError):
            fetch_release_viewer(pin, read)
    else:
        assert fetch_release_viewer(pin, read) == raw
    assert all("untrusted" not in r for r in reads)


@pytest.mark.parametrize(
    "approval",
    [
        None,
        [],
        [{}],
        [
            {
                "run_id": "run",
                "configuration": "stock",
                "index": True,
                "observation_sha256": "a" * 64,
            }
        ],
    ],
)
def test_historical_allowlist_requires_explicit_well_typed_trials(monkeypatch, approval):
    import trusted_viewer

    monkeypatch.setattr(
        trusted_viewer, "load_viewers", lambda path, **kwargs: [{"approved_trials": approval}]
    )
    with pytest.raises(SiteError):
        trusted_viewer.load_history_viewers("caller-reviewed.json")


@pytest.mark.parametrize(
    "line",
    [
        "",
        "Living-Proof-Labs/livingproof-evaluator-health@main/livingproof-evaluator.json",
        "Other/repo@v1.2.0/livingproof-evaluator.json",
    ],
)
def test_fresh_version_requires_matching_stable_declaration(tmp_path, monkeypatch, line):
    from pathlib import Path

    import trusted_viewer

    config = Path(__file__).parents[1] / "config/viewers/evaluators.json"
    if not line or line.startswith("Other/"):
        assert trusted_viewer.load_viewers(config, [line]) == []
    else:
        with pytest.raises(SiteError, match="version-pinned"):
            trusted_viewer.load_viewers(config, [line])
