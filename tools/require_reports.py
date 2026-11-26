# SPDX-License-Identifier: Apache-2.0
"""Link the G1 report from the mock index and reject a refused site build."""

import os
import sys
from pathlib import Path

G1_HREF = 'href="g1/"'
G1_LINK = '<p class="sibling-report"><a href="g1/">Unitree G1 report</a></p>'
HEADING = "<h1>Verification reports</h1>"


def link_g1_report(index_path: Path) -> None:
    """Add a link from the mock reports index to /reports/g1/."""
    text = index_path.read_text(encoding="utf-8")
    if G1_HREF in text:
        return
    if HEADING not in text:
        raise SystemExit("mock reports index has no heading for the G1 link")
    index_path.write_text(text.replace(HEADING, HEADING + G1_LINK, 1), encoding="utf-8")


def require_published(reports: Path, mock_status: str, g1_status: str) -> None:
    """Fail unless both site builds succeeded and the pages have the expected content.

    A refused build can still leave index.html on disk, so file existence is not enough.
    """
    if mock_status != "success" or g1_status != "success":
        raise SystemExit(f"refused site build: mock={mock_status} g1={g1_status}")
    index = (reports / "index.html").read_text(encoding="utf-8")
    g1 = (reports / "g1" / "index.html").read_text(encoding="utf-8")
    if G1_HREF not in index:
        raise SystemExit("mock index does not link to /reports/g1/")
    if "verification-reports-action" not in index:
        raise SystemExit("mock index is missing the mock report")
    if "unitree-g1" not in g1:
        raise SystemExit("G1 index is missing the G1 report")


def require_g1_build(reports: Path, status: str) -> None:
    """Fail unless the pre-merge G1 site build succeeded and shows the real report."""
    if status != "success":
        raise SystemExit(f"refused site build: {status}")
    index = (reports / "index.html").read_text(encoding="utf-8")
    if "unitree-g1" not in index or "FAIL" not in index:
        raise SystemExit("G1 site output is missing the expected report")


def main(argv: list[str]) -> None:
    if len(argv) == 3 and argv[1] == "link":
        link_g1_report(Path(argv[2]))
        return
    if len(argv) == 3 and argv[1] == "check":
        require_published(
            Path(argv[2]),
            os.environ.get("MOCK_STATUS", ""),
            os.environ.get("G1_STATUS", ""),
        )
        return
    if len(argv) == 3 and argv[1] == "g1":
        require_g1_build(Path(argv[2]), os.environ.get("SITE_STATUS", ""))
        return
    raise SystemExit("usage: require_reports.py link|check|g1 <reports-dir>")


if __name__ == "__main__":
    main(sys.argv)
