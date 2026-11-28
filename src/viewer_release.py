# SPDX-License-Identifier: Apache-2.0
"""Fetch bounded, caller-pinned public GitHub release code, never evaluator URLs."""

import hashlib
import json
import re
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from reports_restore import SiteError

LIMIT = 1024 * 1024


class ReleaseRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"github.com", "release-assets.githubusercontent.com"}
            or parsed.username
            or parsed.password
        ):
            raise SiteError("Release asset redirect is not a trusted GitHub download host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_url(url):
    opener = build_opener(HTTPSHandler(), ReleaseRedirects())
    with opener.open(
        Request(url, headers={"Accept": "application/vnd.github+json"}), timeout=20
    ) as response:
        raw = response.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise SiteError("Viewer release response exceeds 1 MiB")
    return raw


def fetch_release_viewer(pin, read=read_url):
    repository, tag, commit, asset = (
        pin.get(k) for k in ("viewer_repository", "viewer_tag", "viewer_commit", "viewer_asset")
    )
    if (
        not isinstance(repository, str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
        or not isinstance(tag, str)
        or not re.fullmatch(r"v\d+\.\d+\.\d+", tag)
        or not isinstance(commit, str)
        or not re.fullmatch(r"[a-f0-9]{40}", commit)
        or not isinstance(asset, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]+\.html", asset)
        or not re.fullmatch(r"[a-f0-9]{64}", pin.get("viewer_sha256", ""))
    ):
        raise SiteError(
            "Caller must pin a released repository, stable tag, commit, asset and digest"
        )
    api = f"https://api.github.com/repos/{repository}"
    release = json.loads(read(f"{api}/releases/tags/{tag}"))
    if (
        release.get("tag_name") != tag
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or not release.get("published_at")
    ):
        raise SiteError("Viewer requires a published stable release")
    obj = json.loads(read(f"{api}/git/ref/tags/{tag}"))["object"]
    for _ in range(8):
        if obj.get("type") != "tag":
            break
        obj = json.loads(read(f"{api}/git/tags/{obj['sha']}"))["object"]
    if obj.get("type") != "commit" or obj.get("sha") != commit:
        raise SiteError("Viewer tag differs from caller commit pin")
    matches = [a for a in release.get("assets", []) if a.get("name") == asset]
    expected_url = f"https://github.com/{repository}/releases/download/{tag}/{asset}"
    if (
        len(matches) != 1
        or matches[0].get("state") != "uploaded"
        or type(matches[0].get("size")) is not int
        or not 0 < matches[0]["size"] <= LIMIT
        or matches[0].get("browser_download_url") != expected_url
    ):
        raise SiteError("Expected one bounded asset on the pinned release")
    raw = read(expected_url)
    if len(raw) != matches[0]["size"] or hashlib.sha256(raw).hexdigest() != pin["viewer_sha256"]:
        raise SiteError("Viewer asset differs from caller digest or size")
    return raw
