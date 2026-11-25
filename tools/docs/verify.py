# SPDX-License-Identifier: Apache-2.0
"""Check generated HTML links, fragments, and required shared branding offline."""

import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Page(HTMLParser):
    def __init__(self, path):
        super().__init__()
        self.ids = set()
        self.links = []
        self.brand = False
        self.feed(path.read_text())

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        for key in ("href", "src"):
            if key in attrs:
                self.links.append(attrs[key])
        if tag == "link" and attrs.get("href", "").endswith("assets/brand.css"):
            self.brand = True
        assert attrs.get("data-md-color-primary") != "indigo", "Unexpected stock palette"


root = Path("artifacts/site").resolve()
pages = {p: Page(p) for p in root.rglob("*.html")}
assert root / "index.html" in pages, "Missing landing page"
for path, page in pages.items():
    assert page.brand, f"Missing shared brand: {path}"
    for link in page.links:
        url = urlsplit(link)
        if url.scheme or url.netloc or link.startswith("/"):
            continue
        target = (path.parent / unquote(url.path)).resolve() if url.path else path
        if target.is_dir():
            target /= "index.html"
        assert target.is_relative_to(root), f"Link leaves manual: {link}"
        assert target.exists(), f"{path.name}: missing {link}"
        if url.fragment and target in pages:
            assert unquote(url.fragment) in pages[target].ids, f"{path.name}: missing {link}"

assert (root / "build.json").is_file(), "Missing source identity"
identity = json.loads((root / "build.json").read_text())
assert (
    identity["source_commit"]
    == subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
), "Stale source identity"
assert identity["brand"] == json.loads(Path("docs-brand.lock.json").read_text())
assert "verification-reports-action" in (root / "index.html").read_text(), "Missing landing content"
assert (root / "provenance.html").is_file(), "Missing provenance page"
assert (root / "version.html").is_file(), "Missing version page"
assert (root / "license.html").is_file(), "Missing license page"

print(f"PASS: {len(pages)} HTML pages; links, fragments, brand, and identity verified")
