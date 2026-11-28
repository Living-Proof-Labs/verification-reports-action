# SPDX-License-Identifier: Apache-2.0
"""Decorate verified, already-published history. This is not a raw Verify bundle."""

import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from reports_common import SiteError
from reports_site import recheck_restored_run
from trusted_viewer import (
    apply_declarations,
    load_declarations,
    read_file,
    revalidate,
    verify_manifest,
)


def migrate(root, registry, manifest_digests):
    declarations = load_declarations(registry)
    for relative, expected in manifest_digests.items():
        if relative not in declarations or not re.fullmatch("[a-f0-9]{64}", expected):
            raise SiteError("Migration requires a caller-selected run and manifest digest")
        folder = root / relative
        manifest = verify_manifest(folder)
        if hashlib.sha256(manifest).hexdigest() != expected:
            raise SiteError("Published manifest differs from caller pin")
        recheck_restored_run(folder)
    if set(manifest_digests) != set(declarations):
        raise SiteError("Every migrated declaration needs a pinned published manifest")
    apply_declarations(root, declarations)


def main():
    import json

    import site_fixture

    filename = sys.argv[1]
    if not re.fullmatch(r"config/retained/[A-Za-z0-9_-]+\.json", filename):
        raise SiteError("Select reviewed retained-history configuration from released code")
    config = json.loads(Path(filename).read_text())
    registry = config["trusted_viewers"]
    if not re.fullmatch(r"config/retained/[A-Za-z0-9_-]+\.json", registry):
        raise SiteError("Viewer registry must belong to released caller code")
    root = Path(sys.argv[2])
    # A migration must not extend the existing evidence retention window.
    from datetime import datetime, timezone

    from reports_site import DEFAULT_RETENTION_DAYS, evidence_expired

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for folder in root.glob("*/*/runs/*"):
        if (folder / "evidence").is_dir():
            created = json.loads(read_file(folder / "run.json"))["run"]["created"]
            if evidence_expired(created, now, DEFAULT_RETENTION_DAYS):
                raise SiteError(
                    "Expired evidence requires the normal retention build before migration"
                )
    migrate(root, registry, config["published_manifests"])
    declarations = load_declarations(registry)
    for folder in root.glob("*/*/runs/*"):
        if (folder / "trusted-viewer").exists():
            key = folder.relative_to(root).as_posix()
            if key not in declarations:
                raise SiteError("Restored viewer has no current caller declaration")
            revalidate(folder, declarations[key][0])
            # report.html and evidence listings still pass the ordinary checker.
            from reports_render import checked_report_html

            for page in [folder / "report.html", *sorted((folder / "evidence").glob("*.html"))]:
                if page.is_file():
                    text = page.read_text()
                    if checked_report_html(text, str(page)) != text:
                        raise SiteError("Unrecognized restored script")
        else:
            recheck_restored_run(folder)
    problems = site_fixture.check_reports_tree(root)
    if problems:
        raise SiteError("Migrated reports tree check failed: " + "; ".join(problems))


if __name__ == "__main__":
    main()
