# SPDX-License-Identifier: Apache-2.0
"""The reports section the manual publishes has an index page."""

from pathlib import Path

from livingproof_capability_report.site import assemble as site

import site_fixture


def test_site_build_writes_reports_index(tmp_path: Path):
    for repository, slug in (
        ("Living-Proof-Labs/verification-reports-action", "verification-reports-action"),
        ("Living-Proof-Labs/livingproof-unitree-g1", "unitree-g1"),
    ):
        runs = tmp_path / slug
        site_fixture.write(runs, repository=repository)
        reports = tmp_path / "site" / slug
        site.build(reports, runs, now="2026-11-25T00:00:00Z", repository=repository)
        text = (reports / "index.html").read_text()
        assert f"{slug}/" in text
