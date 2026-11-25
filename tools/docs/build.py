# SPDX-License-Identifier: Apache-2.0
"""Stage README, repository docs, shared brand, and build identity for Zensical."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.request import urlopen

from identity import validate

REPO = "https://github.com/Living-Proof-Labs/verification-reports-action"


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    stage = Path("artifacts/docs-source")
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree("docs", stage)

    lock = json.loads(Path("docs-brand.lock.json").read_text())
    url = f"https://raw.githubusercontent.com/{lock['repository']}/{lock['commit']}/{lock['path']}"
    with urlopen(url, timeout=30) as response:
        brand = response.read()
    if hashlib.sha256(brand).hexdigest() != lock["sha256"]:
        raise SystemExit("Shared brand checksum mismatch")
    (stage / "assets").mkdir()
    (stage / "assets/brand.css").write_bytes(brand)

    sha = output("git", "rev-parse", "HEAD")
    dirty = bool(output("git", "status", "--porcelain"))
    tag_commit = None
    if os.environ.get("DOCS_KIND") == "release":
        tag_commit = output(
            "git", "rev-parse", "--verify", f"refs/tags/{os.environ['RELEASE_TAG']}^{{commit}}"
        )
    identity = validate(sha, dirty, os.environ, tag_commit)
    identity.update(
        {
            "source_commit": sha,
            "source_dirty": dirty,
            "docs_status": "publication tracked by workflow",
            "brand": lock,
        }
    )
    print(f"Manual: {identity['kind']} {identity['version']}; source {sha}")
    (stage / "build.json").write_text(json.dumps(identity, indent=2) + "\n")

    readme = Path("README.md").read_text()
    readme = (
        readme.replace("(docs/provenance.md)", "(provenance.md)")
        .replace(
            "(docs/decisions/0001-composite-vs-reusable-workflow.md)",
            "(decisions/0001-composite-vs-reusable-workflow.md)",
        )
        .replace("(LICENSE)", "(license.md)")
    )
    (stage / "index.md").write_text(readme)

    provenance = (stage / "provenance.md").read_text()
    (stage / "provenance.md").write_text(
        provenance.replace(
            "(../.github/workflows/report.yml)",
            f"({REPO}/blob/{sha}/.github/workflows/report.yml)",
        )
    )

    (stage / "version.md").write_text(
        "# Version and source\n\n"
        f"Docs: {identity['version']} ({identity['kind']}) · [{sha[:12]}]({REPO}/tree/{sha})\n\n"
        f"Full source commit: `{sha}`. Uncommitted changes: `{identity['source_dirty']}`.\n\n"
        f"Release tag: `{identity['release_tag'] or 'none'}`.\n\n"
        "This manual identifies its verification, candidate or released source above. "
        "Build identity alone does not prove deployment. Historical manuals are not maintained. "
        "Source history remains available in the "
        f"[repository]({REPO}).\n\n"
        "[Machine-readable build identity](build.json). Only clean CI artifacts identify "
        "the exact committed content; dirty local builds include uncommitted work.\n\n"
        f"Shared brand: {lock['version']} at `{lock['commit']}`, verified by SHA-256.\n"
    )
    (stage / "license.md").write_text(
        "# License\n\nRepository content is Apache-2.0. The shared brand retains its "
        "upstream license notice.\n\n```text\n" + Path("LICENSE").read_text() + "\n```\n"
    )


if __name__ == "__main__":
    main()
