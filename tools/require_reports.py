# SPDX-License-Identifier: Apache-2.0
"""Publish the G1 example as the public reports index and reject fixture smoke."""

import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

# reports_render._badge_html emits exactly one of these class attributes.
GRADE_BADGE = re.compile(r'class="badge badge-(?:pass|fail|warn|muted)"')
FIXTURE_SEGMENTS = ("fixture-scenario-stock", "fixture-hallway-stock")
FIXTURE_PHRASE = "this repository's fixture"
TEXT_SUFFIXES = {".html", ".md", ".txt", ".json", ".css", ".js"}
SAMPLE_REPORT = "reports/g1/unitree-g1/stock-mujoco-hallway/latest/"
STABLE_PAGE = Path("g1/unitree-g1/stock-mujoco-hallway/latest/index.html")
RUN_ID_LINK = re.compile(r"reports/(?:[^/\s\"')<>]+/)*runs/[0-9]+/")
_LOCAL_REF = re.compile(r"""(?P<attr>\b(?:href|src))=(?P<q>["'])(?P<url>.*?)(?P=q)""")
MOCK_ROOT_FILES = ("site.json", "styles.css", "open.js")
CI_RUN_LINK = re.compile(
    r"""href=(["'])https://github\.com/[^/"'<>\s]+/[^/"'<>\s]+/actions/runs/[0-9]+\1"""
)
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".avi"}


def _keep_public_url(url: str) -> bool:
    """Leave absolute, root-absolute, data, in-page, and already-prefixed links alone."""
    if not url or url.startswith(("#", "mailto:", "/", "g1/", "../", "index.html")):
        return True
    return urlsplit(url).scheme in {"http", "https", "data"}


def _prefix_g1_links(text: str) -> str:
    """Point the copied G1 index at the stable reports/g1/ tree."""

    def repl(match: re.Match[str]) -> str:
        url = match.group("url")
        if _keep_public_url(url):
            return match.group(0)
        quote = match.group("q")
        return f"{match.group('attr')}={quote}g1/{url}{quote}"

    return _LOCAL_REF.sub(repl, text)


def publish_public_index(reports: Path) -> None:
    """Make reports/index.html the G1 index, with scenario links under g1/."""
    source = reports / "g1" / "index.html"
    if not source.is_file():
        raise SystemExit("G1 index is missing")
    text = _prefix_g1_links(source.read_text(encoding="utf-8"))
    (reports / "index.html").write_text(text, encoding="utf-8")
    for name in MOCK_ROOT_FILES:
        leftover = reports / name
        if leftover.is_file():
            leftover.unlink()


def fixture_problems(reports: Path) -> list[str]:
    """Public fixture paths and the fixture pinned-input phrase."""
    problems: list[str] = []
    index_path = reports / "index.html"
    if index_path.is_file():
        index = index_path.read_text(encoding="utf-8")
        for name in FIXTURE_SEGMENTS:
            if name in index:
                problems.append(f"public index lists {name}")
    if not reports.is_dir():
        return problems
    for path in sorted(reports.rglob("*")):
        if path.name in FIXTURE_SEGMENTS:
            problems.append(f"public reports contain fixture path {path.relative_to(reports)}")
    for path in sorted(reports.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if FIXTURE_PHRASE in text:
            problems.append(
                f"public reports contain the fixture phrase in {path.relative_to(reports)}"
            )
    return problems


def drop_published_fixtures(reports: Path) -> None:
    """Remove fixture smoke restored from an earlier Pages artifact."""
    if not reports.is_dir():
        raise SystemExit("reports directory is missing")
    victims = [
        path for path in reports.rglob("*") if path.is_dir() and path.name in FIXTURE_SEGMENTS
    ]
    for path in sorted(victims, key=lambda item: len(item.parts), reverse=True):
        if path.exists():
            shutil.rmtree(path)
    for path in list(reports.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if FIXTURE_PHRASE in path.read_text(encoding="utf-8", errors="replace"):
            path.unlink()
    for path in sorted(
        (item for item in reports.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        if path != reports and path.exists() and not any(path.iterdir()):
            path.rmdir()


def _newest_run_folders(reports: Path) -> list[Path]:
    """The newest run folder of each scenario history, by the time its record says."""
    newest: dict[Path, tuple[str, Path]] = {}
    for record in sorted(reports.rglob("runs/*/run.json")):
        try:
            created = json.loads(record.read_text(encoding="utf-8"))["run"]["created"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not isinstance(created, str):
            continue
        history = record.parent.parent
        if history not in newest or created > newest[history][0]:
            newest[history] = (created, record.parent)
    return [folder for _, folder in newest.values()]


def run_page_problems(reports: Path) -> list[str]:
    """The newest run page of each scenario links its CI run and the media it published.

    Only the newest run is held to this: an older restored run was built before its page
    carried the links, and its evidence may since have been pruned.
    """
    problems: list[str] = []
    for folder in _newest_run_folders(reports):
        name = (folder / "index.html").relative_to(reports).as_posix()
        page_path = folder / "index.html"
        if not page_path.is_file():
            problems.append(f"{name} is missing")
            continue
        page = page_path.read_text(encoding="utf-8")
        if CI_RUN_LINK.search(page) is None:
            problems.append(f"{name} is missing the CI run link")
        evidence = folder / "evidence"
        for file in sorted(evidence.rglob("*")) if evidence.is_dir() else []:
            relative = file.relative_to(folder).as_posix()
            published_video = file.suffix.lower() in VIDEO_SUFFIXES and relative.startswith(
                "evidence/video/"
            )
            published_recording = file.suffix.lower() == ".mcap" and not file.name.endswith(
                ".robot.mcap"
            )
            if not file.is_file() or not (published_video or published_recording):
                continue
            if f"'{relative}'" not in page and f'"{relative}"' not in page:
                problems.append(f"{name} does not link the published {relative}")
    return problems


def require_published(reports: Path, g1_status: str) -> None:
    """Fail unless the G1 site build succeeded and the public tree is the G1 example.

    A refused build can still leave index.html on disk, so file existence is not enough.
    Fixture smoke must not appear on the public index or as public run folders.
    """
    if g1_status != "success":
        raise SystemExit(f"refused site build: g1={g1_status}")
    index = (reports / "index.html").read_text(encoding="utf-8")
    if "unitree-g1" not in index or GRADE_BADGE.search(index) is None:
        raise SystemExit("public index is missing the G1 report")
    if problems := fixture_problems(reports):
        raise SystemExit(problems[0])
    if not (reports / STABLE_PAGE).is_file():
        raise SystemExit(f"built site is missing {SAMPLE_REPORT}")
    if problems := run_page_problems(reports):
        raise SystemExit(problems[0])


def require_readme_sample(site: Path, readme: Path) -> None:
    """The README sample link must be the stable latest page and exist in the built site.

    A pinned run id is not a stable sample. latest/ is the page that always
    shows the current hallway report.
    """
    text = readme.read_text(encoding="utf-8")
    if RUN_ID_LINK.search(text):
        raise SystemExit("README links a reports run id")
    if SAMPLE_REPORT not in text:
        raise SystemExit("README is missing the stable G1 report link")
    page = site / SAMPLE_REPORT / "index.html"
    if not page.is_file():
        raise SystemExit(f"built site is missing {SAMPLE_REPORT}")


def require_g1_build(reports: Path, status: str) -> None:
    """Fail unless the pre-merge G1 site build succeeded and shows the real report."""
    if status != "success":
        raise SystemExit(f"refused site build: {status}")
    index = (reports / "index.html").read_text(encoding="utf-8")
    if "unitree-g1" not in index or GRADE_BADGE.search(index) is None:
        raise SystemExit("G1 site output is missing the expected report")
    if problems := run_page_problems(reports):
        raise SystemExit(problems[0])


def main(argv: list[str]) -> None:
    if len(argv) == 3 and argv[1] == "drop-fixtures":
        drop_published_fixtures(Path(argv[2]))
        return
    if len(argv) == 3 and argv[1] == "publish-index":
        publish_public_index(Path(argv[2]))
        return
    if len(argv) == 3 and argv[1] == "check":
        reports = Path(argv[2])
        require_published(reports, os.environ.get("G1_STATUS", ""))
        require_readme_sample(reports.parent, Path("README.md"))
        return
    if len(argv) == 3 and argv[1] == "g1":
        require_g1_build(Path(argv[2]), os.environ.get("SITE_STATUS", ""))
        return
    raise SystemExit("usage: require_reports.py drop-fixtures|publish-index|check|g1 <reports-dir>")


if __name__ == "__main__":
    main(sys.argv)
