# SPDX-License-Identifier: Apache-2.0
"""Verify the served manual belongs to the expected clean source revision."""

import argparse
import hashlib
import json
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "script" and attrs.get("src"):
            self.urls.append(attrs["src"])
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.urls.append(attrs["href"])


def check(base, commit, version=None):
    base = base.rstrip("/") + "/"

    def fetch(path):
        url = urljoin(base, path)
        if not url.startswith(base):
            raise ValueError(f"Asset leaves manual URL: {path}")
        with urlopen(url, timeout=20) as response:
            return response.read()

    identity = json.loads(fetch("build.json"))
    if identity.get("source_commit") != commit or identity.get("source_dirty") is not False:
        raise ValueError("Published source identity is stale or dirty")
    if version is not None and (
        identity.get("kind") != "release"
        or identity.get("version") != version
        or identity.get("release_tag") != "v" + version
    ):
        raise ValueError("Published release version identity mismatch")
    for path, expected in (
        ("index.html", "verification-reports-action"),
        ("provenance.html", "Pinned inputs"),
        ("version.html", commit),
    ):
        html = fetch(path).decode()
        if expected not in html or "assets/brand.css" not in html:
            raise ValueError(f"Missing expected content or brand in {path}")
        assets = Assets()
        assets.feed(html)
        for asset in assets.urls:
            if not fetch(asset):
                raise ValueError(f"Empty asset: {asset}")
    brand = fetch("assets/brand.css")
    if hashlib.sha256(brand).hexdigest() != identity["brand"]["sha256"]:
        raise ValueError("Published brand checksum mismatch")
    print(f"PASS: served manual {commit}; landing, provenance, identity and assets")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Manual base URL, including repository path")
    parser.add_argument("--commit", required=True, help="Expected full source commit SHA")
    parser.add_argument("--version", help="Require the published stable release version")
    parser.add_argument("--attempts", type=int, default=1, help="Retry count for Pages propagation")
    args = parser.parse_args()
    if urlsplit(args.url).scheme not in {"http", "https"}:
        parser.error("--url must use HTTP or HTTPS")
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit) or not 1 <= args.attempts <= 6:
        parser.error("Expected a 40-character SHA and 1–6 attempts")
    for attempt in range(args.attempts):
        try:
            check(args.url, args.commit, args.version)
            return
        except (OSError, ValueError, KeyError) as exc:
            if attempt == args.attempts - 1:
                raise SystemExit(f"Published manual verification failed: {exc}") from exc
            time.sleep(10)


if __name__ == "__main__":
    main()
