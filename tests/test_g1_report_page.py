# SPDX-License-Identifier: Apache-2.0
"""The reports section the manual publishes has an index page."""

from pathlib import Path

import reports_site as site
import site_fixture


def test_site_build_writes_reports_index(tmp_path: Path):
    runs = tmp_path / "runs"
    site_fixture.write(runs)
    reports = tmp_path / "site" / "reports"
    site.build(reports, runs, now="2026-11-25T00:00:00Z")
    index = reports / "index.html"
    assert index.is_file()
    text = index.read_text()
    assert "unitree-g1" in text
