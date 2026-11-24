#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Render static HTML pages, provenance blocks, navigation, and listings for `reports/`."""

from __future__ import annotations

import html
import ipaddress
import re
import urllib.parse
from pathlib import Path

from reports_common import (
    _REPORTER_TAG,
    ANCHOR_REPORTER,
    COMMIT_SHA,
    EVALUATOR_ANCHOR,
    MARKED_INTEGRITY_STATUSES,
    SiteError,
    _plain_label,
    _require_single_robot,
    _stamp,
    _stored_text,
    listing_path,
    site_path,
)

ASSETS_DIR = Path(__file__).resolve().with_name("assets")
DOCS_HOME_URL = "https://docs.livingprooflabs.com/"
ARCHIVED_REPORTS_URL = (
    "https://docs.livingprooflabs.com/livingproof-verification/reports/index.html"
)
_REPO_PATH = re.compile(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+")

SITE_NAV_STYLES = """.site-nav {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.35rem 0.6rem;
  margin: 0 0 0.85rem;
  font-size: 0.86rem;
}
.site-nav a {
  color: var(--teal-bright, #64dfc7);
  font-weight: 500;
  text-decoration: none;
}
.site-nav a:hover {
  text-decoration: underline;
}
.site-nav a[aria-current="page"] {
  color: var(--header-text, #f8fafc);
  font-weight: 650;
}
.site-nav-sep {
  color: var(--header-muted, #b7c5d6);
  opacity: 0.72;
}
"""
_ROBOT_TITLES = {
    "booster-t1": "Booster T1",
    "unitree-g1": "Unitree G1",
    "unitree-go1": "Unitree Go1",
}
THEME_SWITCH = (
    '<fieldset class="theme"><legend>Colour theme</legend>'
    '<label><input type="radio" name="theme" id="theme-system" value="system" checked> '
    "System</label>"
    '<label><input type="radio" name="theme" id="theme-light" value="light"> Light</label>'
    '<label><input type="radio" name="theme" id="theme-dark" value="dark"> Dark</label>'
    "</fieldset>"
)
LISTING_STYLE = (
    "body{font-family:system-ui,sans-serif;margin:2rem auto;max-width:72rem;padding:0 1rem;"
    "color:#1b1f24}table{border-collapse:collapse}td,th{border:1px solid #d0d7de;"
    "padding:.35rem .6rem;text-align:left}code{font-size:.9em}"
)


def _load_asset(name: str, header: str) -> str:
    """Read a packaged asset next to this module (the action checkout, never the caller's).

    The source file starts with a license header line that is not part of the published
    asset, so the generated site stays byte-identical to the pre-split output.
    """
    text = (ASSETS_DIR / name).read_text(encoding="utf-8")
    first, sep, rest = text.partition("\n")
    if first != header or not sep:
        raise SiteError(f"{ASSETS_DIR / name}: missing the expected license header")
    return rest


def load_styles_css() -> str:
    return _load_asset("styles.css", "/* SPDX-License-Identifier: Apache-2.0 */")


def load_open_js() -> str:
    return _load_asset("open.js", "// SPDX-License-Identifier: Apache-2.0")


_SAFE_HOST = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.?"
)


def _valid_https_netloc(parsed: urllib.parse.SplitResult) -> bool:
    netloc = parsed.netloc
    if not netloc or "@" in netloc or any(ch.isspace() for ch in netloc):
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    try:
        _ = parsed.port
        hostname = parsed.hostname
    except ValueError:
        return False
    if not hostname:
        return False
    if netloc.startswith("["):
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            return False
        return True
    return _SAFE_HOST.fullmatch(hostname) is not None


def safe_href(value: object) -> str | None:
    """Allow only https:// URLs and relative links; reject javascript:, data:, http:, //."""
    if not isinstance(value, str):
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return None
    cleaned = value.strip()
    if not cleaned or "\\" in cleaned or cleaned.startswith("//"):
        return None
    try:
        parsed = urllib.parse.urlsplit(cleaned)
    except ValueError:
        return None
    if parsed.scheme:
        if parsed.scheme.lower() != "https" or not _valid_https_netloc(parsed):
            return None
        return cleaned
    if parsed.netloc:
        return None
    return cleaned


def _evaluator_documents(report: object) -> list[dict]:
    if not isinstance(report, dict):
        return []
    docs: list[dict] = []
    bundle = report.get("source_bundle")
    if isinstance(bundle, dict):
        results = bundle.get("results")
        if isinstance(results, list):
            for result in results:
                if not isinstance(result, dict):
                    continue
                items = result.get("evaluator_results")
                if isinstance(items, list):
                    docs.extend(d for d in items if isinstance(d, dict))
                prov = result.get("provenance")
                if isinstance(prov, dict):
                    p_items = prov.get("evaluator_results")
                    if isinstance(p_items, list):
                        docs.extend(d for d in p_items if isinstance(d, dict))
                    one = prov.get("evaluator_result")
                    if isinstance(one, dict):
                        docs.append(one)
                original = result.get("original_result")
                if isinstance(original, dict):
                    docs.append(original)
        assessment = bundle.get("assessment")
        integrity = assessment.get("integrity_results") if isinstance(assessment, dict) else None
        if isinstance(integrity, dict):
            for item in integrity.values():
                if isinstance(item, dict):
                    docs.append(item)
                elif isinstance(item, list):
                    docs.extend(d for d in item if isinstance(d, dict))
    summaries = report.get("summaries")
    if isinstance(summaries, list):
        for summary in summaries:
            if not isinstance(summary, dict):
                continue
            items = summary.get("evaluator_results")
            if isinstance(items, list):
                docs.extend(d for d in items if isinstance(d, dict))
    return docs


def evaluator_slug(ev_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", str(ev_id)) or "evaluator"


def reporter_has_evaluator_anchors(record: dict) -> bool:
    releases = record.get("releases") if isinstance(record.get("releases"), dict) else {}
    reporter = releases.get("reporter") if isinstance(releases.get("reporter"), dict) else {}
    tag = _stored_text(reporter.get("tag"))
    found = _REPORTER_TAG.fullmatch(tag) if tag else None
    return found is not None and tuple(int(n) for n in found.groups()) >= ANCHOR_REPORTER


def page_evaluator_ids(report: object) -> set[str]:
    ids = set()
    for doc in _evaluator_documents(report):
        ev = doc.get("evaluator") if isinstance(doc.get("evaluator"), dict) else {}
        ev_id = _stored_text(ev.get("id"))
        if ev_id is not None:
            ids.add(ev_id)
    return ids


def evaluator_fragment(record: dict, ev_id: object, report: object = None) -> str:
    ev_id_str = _stored_text(ev_id)
    if ev_id_str is None or not reporter_has_evaluator_anchors(record):
        return EVALUATOR_ANCHOR
    ids = page_evaluator_ids(report)
    slug = evaluator_slug(ev_id_str)
    if ev_id_str not in ids or any(o != ev_id_str and evaluator_slug(o) == slug for o in ids):
        return EVALUATOR_ANCHOR
    return f"#evaluator-{slug}"


def _simple_page(title: str, body: str, up: str = "") -> str:
    safe_up = safe_href(up)
    nav = (
        f'<p><a href="{html.escape(safe_up, quote=True)}">← All verification reports</a></p>'
        if safe_up
        else ""
    )
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{LISTING_STYLE}</style></head>"
        f"<body>{nav}<h1>{html.escape(title)}</h1>\n{body}\n</body></html>\n"
    )


def a(url: str, text: str) -> str:
    safe = safe_href(url)
    if safe is None:
        return html.escape(text)
    return f'<a href="{html.escape(safe, quote=True)}">{html.escape(text)}</a>'


def write_listing(folder: Path, record: dict, up: str) -> None:
    path = listing_path(record)
    if path is None or not site_path(path):
        return
    prefix = path.removesuffix(".html").removeprefix("evidence/") + "/"
    rows = "".join(
        "<tr><td>"
        + a(
            item["path"].removeprefix("evidence/"),
            item["path"].removeprefix(f"evidence/{prefix}"),
        )
        + f"</td><td>{item['bytes']}</td><td><code>{html.escape(item['sha256'])}</code></td></tr>"
        for item in record["files"]
    )
    title = _plain_label(record.get("label"))
    body = (
        f"<p>All {len(record['files'])} files copied to this site. "
        f"{a('../index.html', 'Back to the report')}</p>"
        f"<table><tr><th>File</th><th>Bytes</th><th>sha256</th></tr>{rows}</table>"
    )
    out = folder / path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_simple_page(title, body, up=f"../{up}index.html"), encoding="utf-8")


def _spec_repository_url(spec: dict) -> str | None:
    repository = _stored_text(spec.get("repository"))
    if repository is None:
        return None
    repository = repository.rstrip("/").removesuffix(".git")
    if repository.startswith("https://"):
        try:
            parsed = urllib.parse.urlsplit(repository)
        except ValueError:
            return None
        if not re.fullmatch(r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*", parsed.netloc):
            return None
        if parsed.query or parsed.fragment:
            return None
        path = parsed.path.removeprefix("/")
        url = repository
    else:
        path = repository
        url = "https://github.com/" + repository
    if not _REPO_PATH.fullmatch(path) or path.rsplit("/", 1)[-1] in {".", ".."}:
        return None
    return url


def _spec_repository_label(spec: dict) -> str | None:
    url = _spec_repository_url(spec)
    if url:
        return urllib.parse.urlsplit(url).path.removeprefix("/")
    return _stored_text(spec.get("repository"))


def _spec_assessments(report: object) -> list[dict]:
    summaries = report.get("summaries", []) if isinstance(report, dict) else []
    assessed: list[dict] = []
    for summary in summaries if isinstance(summaries, list) else []:
        spec = summary.get("spec") if isinstance(summary, dict) else None
        if isinstance(spec, dict) and spec and spec not in assessed:
            assessed.append(spec)
    return assessed


def _spec_core(spec: object) -> tuple | None:
    if not isinstance(spec, dict) or not spec:
        return None
    repo_label = _spec_repository_label(spec)
    tag = _stored_text(spec.get("tag"))
    commit = _stored_text(spec.get("commit"))
    if repo_label is None and tag is None and commit is None:
        return None
    return (repo_label, tag, commit)


def _spec_line_html(spec: object, *, assessed: bool) -> str:
    core = _spec_core(spec)
    if core is None or not isinstance(spec, dict):
        return "Not recorded"
    _, tag, commit = core
    repository_url = _spec_repository_url(spec)
    pieces = []
    if tag is not None:
        label = html.escape(tag)
        if repository_url:
            href = f"{repository_url}/releases/tag/{urllib.parse.quote(tag, safe='')}"
            title = f"Release {tag}" + (f", commit {commit}" if commit is not None else "")
            pieces.append(
                f'<a href="{html.escape(href, quote=True)}" '
                f'title="{html.escape(title, quote=True)}">{label}</a>'
            )
        else:
            pieces.append(label)
    elif commit is not None:
        title = html.escape(f"Commit {commit}", quote=True)
        if repository_url and COMMIT_SHA.fullmatch(commit):
            pieces.append(
                f'<a href="{html.escape(repository_url, quote=True)}/commit/{commit}" '
                f'title="{title}" aria-label="{title}">commit</a>'
            )
        else:
            pieces.append(f'<span title="{title}">commit</span>')
    repository_label = _spec_repository_label(spec)
    if repository_label is not None:
        pieces.append(html.escape(repository_label))
    if assessed:
        cap = _stored_text(spec.get("capability"))
        if cap:
            pieces.append(html.escape(cap))
        rev = _stored_text(spec.get("revision"))
        if rev:
            pieces.append(html.escape("revision " + rev))
    return " · ".join(pieces)


def _spec_summary_html(generation: object, assessed: list[dict]) -> str:
    spec = assessed[0] if assessed else generation
    core = _spec_core(spec)
    if core is None or not isinstance(spec, dict):
        return "<strong>Spec</strong> not recorded"
    _, tag, _commit = core
    pieces = [html.escape(tag or "commit")]
    cap = _stored_text(spec.get("capability"))
    if cap:
        pieces.append(html.escape(cap))
    rev = _stored_text(spec.get("revision"))
    if rev:
        pieces.append(html.escape("revision " + rev))
    if len(assessed) > 1:
        pieces.append(f"+{len(assessed) - 1} more")
    return "<strong>Spec</strong> " + " · ".join(pieces)


_PROVENANCE_SUMMARY_STYLE = (
    "cursor:pointer;margin:0;font-size:inherit;font-weight:400;"
    "text-transform:none;letter-spacing:normal;color:inherit"
)
_PROVENANCE_SUMMARY_CSS = (
    "details.spec-provenance>summary::after{"
    'content:"Show";float:none;margin-left:.6rem;text-transform:none;'
    "letter-spacing:normal;font-weight:600;color:var(--link,#0b5cad)}"
    'details.spec-provenance[open]>summary::after{content:"Hide"}'
)


def spec_provenance_html(record: dict, report: object) -> str:
    generation = (record.get("releases") or {}).get("specs")
    assessed = _spec_assessments(report)
    if len(assessed) == 1 and _spec_core(generation) == _spec_core(assessed[0]) is not None:
        lines = [("Generation and assessment", _spec_line_html(assessed[0], assessed=True))]
    elif not assessed and _spec_core(generation) is None:
        lines = [("Generation and assessment", "Not recorded")]
    else:
        lines = [("Generation", _spec_line_html(generation, assessed=False))]
        lines += [("Assessment", _spec_line_html(spec, assessed=True)) for spec in assessed] or [
            ("Assessment", "Not recorded")
        ]
    rows = "".join(
        f'<dt style="font-weight:600">{label}</dt><dd style="margin:0 0 .5rem">{line}</dd>'
        for label, line in lines
    )
    return (
        '<details class="spec-provenance" id="spec-provenance" '
        'style="margin:1.5rem 0 0;font-size:.875rem;overflow-wrap:anywhere">'
        f'<summary style="{_PROVENANCE_SUMMARY_STYLE}">'
        f"{_spec_summary_html(generation, assessed)}</summary>"
        f"<style>{_PROVENANCE_SUMMARY_CSS}</style>"
        f'<dl style="margin:.5rem 0 0">{rows}</dl>'
        '<p style="margin:0;font-size:.8rem;opacity:.75">'
        "Recorded at run time; not reassessed.</p></details>"
    )


def with_spec_provenance(public: str, record: dict, report: object) -> str:
    if 'id="spec-provenance"' in public:
        return public
    section = spec_provenance_html(record, report)
    for closing in (r"</main\s*>", r"</body\s*>"):
        found = list(re.finditer(closing, public, re.I))
        if found:
            return public[: found[-1].start()] + section + public[found[-1].start() :]
    return public + section


def _site_nav_html(overview_href: str, reports_href: str, *, on_reports_index: bool = False) -> str:
    reports_attr = ' aria-current="page"' if on_reports_index else ""
    return (
        '<nav class="site-nav" aria-label="Site">'
        f'<a href="{html.escape(DOCS_HOME_URL, quote=True)}">Documentation home</a>'
        '<span class="site-nav-sep" aria-hidden="true">/</span>'
        f'<a href="{html.escape(overview_href, quote=True)}">Overview</a>'
        '<span class="site-nav-sep" aria-hidden="true">/</span>'
        f'<a href="{html.escape(reports_href, quote=True)}"{reports_attr}>Reports</a>'
        "</nav>"
    )


def with_site_nav(public: str, *, overview_href: str, reports_href: str) -> str:
    nav = _site_nav_html(overview_href, reports_href)
    if '<nav class="site-nav"' in public:
        return public
    style_tag = f"<style>{SITE_NAV_STYLES}</style>"
    if "</head>" in public and ".site-nav" not in public:
        public = public.replace("</head>", f"{style_tag}</head>", 1)
    masthead_inner = re.search(
        r"<div\s+class=[\"'][^\"']*\bmasthead-inner\b[^\"']*[\"'][^>]*>", public, re.I
    )
    if masthead_inner:
        pos = masthead_inner.end()
        return public[:pos] + nav + public[pos:]
    body_open = re.search(r"<body[^>]*>", public, re.I)
    if body_open:
        pos = body_open.end()
        header = f'<header class="masthead"><div class="wrap masthead-inner">{nav}</div></header>'
        return public[:pos] + header + public[pos:]
    return nav + public


def redirect_page(target: str, title: str = "Latest report") -> str:
    safe_target = safe_href(target) or ""
    safe = html.escape(safe_target, quote=True)
    shown = html.escape(title)
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0; url={safe}">'
        f'<link rel="canonical" href="{safe}">'
        f"<title>{shown}</title></head><body>"
        f'<p><a href="{safe}">{shown}</a></p>'
        "</body></html>\n"
    )


def _robot_title(robot: str) -> str:
    if robot in _ROBOT_TITLES:
        return _ROBOT_TITLES[robot]
    parts = []
    for part in robot.split("-"):
        if re.fullmatch(r"[a-z]+\d+", part) and len(part) <= 3:
            parts.append(part.upper())
        else:
            parts.append(part.capitalize())
    return " ".join(parts)


def _scenario_title(record: dict, variant: str) -> str:
    outcomes = record.get("outcome") or []
    capability = outcomes[0].get("capability", "") if outcomes else ""
    if variant.endswith("-corner-left") or capability == "walk-corner-left":
        return "Walk around the corner (left)"
    if variant.endswith("-corner-right") or capability == "walk-corner-right":
        return "Walk around the corner (right)"
    if capability == "walk-corner" or variant.endswith("-corner"):
        return "Walk around the corner"
    if capability == "walk-hallway" or variant.endswith("-hallway"):
        return "Walk the hallway"
    return (capability or variant).replace("-", " ").capitalize()


def _variant_subtitle(record: dict, variant: str) -> str:
    robot = record.get("robot") if isinstance(record.get("robot"), dict) else {}
    config = _stored_text(robot.get("configuration")) or variant.split("-", 1)[0]
    tokens = variant.split("-")
    sim = ""
    if "mujoco" in tokens:
        sim = "MuJoCo"
    elif "gazebo" in tokens:
        sim = "Gazebo"
    return f"{config} · {sim}" if sim else config


def _record_grade(record: dict) -> str:
    outcomes = record.get("outcome") or []
    grades = [o.get("grade", "") for o in outcomes if isinstance(o, dict)]
    if "FAIL" in grades:
        return "FAIL"
    if "INSUFFICIENT_EVIDENCE" in grades:
        return "INSUFFICIENT_EVIDENCE"
    if grades and all(g == "UNSUPPORTED" for g in grades):
        return "UNSUPPORTED"
    if "PASS" in grades:
        return "PASS"
    return (grades[0] if grades else "") or "UNKNOWN"


def _grade_sort_rank(grade: str) -> int:
    return {
        "FAIL": 0,
        "INSUFFICIENT_EVIDENCE": 1,
        "PASS": 2,
        "UNSUPPORTED": 3,
    }.get(grade, 4)


def _badge_label(grade: str) -> str:
    if grade == "UNSUPPORTED":
        return "Not supported"
    if grade == "INSUFFICIENT_EVIDENCE":
        return "Insufficient evidence"
    return grade


def _badge_html(grade: str) -> str:
    cls = {
        "PASS": "badge-pass",
        "FAIL": "badge-fail",
        "UNSUPPORTED": "badge-muted",
        "INSUFFICIENT_EVIDENCE": "badge-warn",
    }.get(grade, "badge-muted")
    return f'<span class="badge {cls}">{html.escape(_badge_label(grade))}</span>'


def _utc_time_text(created: str) -> str:
    created_dt = _stamp(created)
    return f"{created_dt.day} {created_dt.strftime('%b %Y, %H:%M')} UTC"


def _time_html(created: str) -> str:
    utc_text = _utc_time_text(created)
    return (
        f'<time datetime="{html.escape(created, quote=True)}" '
        f'title="{html.escape(utc_text, quote=True)}">{html.escape(utc_text)}</time>'
    )


def _release_html(record: dict, *, linked: bool = True) -> str:
    commit = record.get("commit") if isinstance(record.get("commit"), dict) else {}
    tag = _stored_text(record.get("release_tag") or record.get("bundle_tag") or commit.get("tag"))
    sha = _stored_text(commit.get("sha")) or ""
    commit_url = safe_href(_stored_text(commit.get("url")))
    if tag is not None:
        if commit_url and "/commit/" in commit_url:
            tag_url = safe_href(commit_url.split("/commit/", 1)[0] + f"/releases/tag/{tag}")
        else:
            tag_url = commit_url
        title = f"Release {tag}"
        if linked and tag_url:
            return (
                f'<a class="release" href="{html.escape(tag_url, quote=True)}" '
                f'title="{html.escape(title, quote=True)}">{html.escape(tag)}</a>'
            )
        return (
            f'<span class="release" title="{html.escape(title, quote=True)}">'
            f"{html.escape(tag)}</span>"
        )
    title = html.escape(
        "No release tag on this commit" + (f": {sha}" if sha else ""),
        quote=True,
    )
    if linked and commit_url:
        return (
            f'<a class="release is-fallback" href="{html.escape(commit_url, quote=True)}" '
            f'title="{title}">commit</a>'
        )
    return f'<span class="release is-fallback" title="{title}">commit</span>'


def _integrity_chip(
    title: str, status: str, prefix: str, linked: bool, fragment: str = EVALUATOR_ANCHOR
) -> str:
    tip = f"{title}: integrity status {status}"
    if status == "INCONCLUSIVE":
        tip += " (not graded)"
    words = "error" if status == "ERROR" else "not graded"
    icon = "\u2715" if status == "ERROR" else "?"
    klass = f"chip chip-integrity chip-integrity-{status.lower()}"
    body = f'<span aria-hidden="true">{icon}</span> {html.escape(title)} {words}'
    if linked:
        target = html.escape(prefix + fragment, quote=True)
        return (
            f'<a class="{klass}" href="{target}" title="{html.escape(tip, quote=True)}">{body}</a>'
        )
    return f'<span class="{klass}" title="{html.escape(tip, quote=True)}">{body}</span>'


def _chips_html(
    record: dict, prefix: str, *, linked: bool = True, extra: str = "", report: object = None
) -> str:
    grade = _record_grade(record)
    evaluators = record.get("failing_evaluators") or []
    items = []
    if grade not in ("PASS", "UNSUPPORTED") and isinstance(evaluators, list):
        for ev in evaluators:
            if not isinstance(ev, dict):
                continue
            title = _stored_text(ev.get("title"))
            href = _stored_text(ev.get("href"))
            if title is None or href is None:
                continue
            if ev.get("not_graded") is True:
                items.append(
                    _integrity_chip(
                        title,
                        "INCONCLUSIVE",
                        prefix,
                        linked,
                        evaluator_fragment(record, ev.get("id"), report),
                    )
                )
            elif linked:
                fragment = evaluator_fragment(record, ev.get("id"), report)
                tip = f"Open the {title} finding in the report"
                items.append(
                    f'<a class="chip" href="{html.escape(prefix + fragment, quote=True)}" '
                    f'title="{html.escape(tip, quote=True)}" '
                    f'aria-label="{html.escape(tip, quote=True)}">{html.escape(title)}</a>'
                )
            else:
                items.append(f'<span class="chip">{html.escape(title)}</span>')
    markers = record.get("integrity_status") or []
    if grade in ("PASS", "UNSUPPORTED") and isinstance(markers, list):
        for marker in markers:
            if not isinstance(marker, dict):
                continue
            title = _stored_text(marker.get("title"))
            status = marker.get("status")
            if title is None or status not in MARKED_INTEGRITY_STATUSES:
                continue
            if _stored_text(marker.get("id")) is None:
                continue
            items.append(
                _integrity_chip(
                    title,
                    status,
                    prefix,
                    linked,
                    evaluator_fragment(record, marker.get("id"), report),
                )
            )
    if extra:
        items.append(extra)
    if not items:
        return ""
    return f'<div class="chips">{"".join(items)}</div>'


def _spec_versions(record: dict, report: object) -> dict:
    generation = _stored_text(((record.get("releases") or {}).get("specs") or {}).get("tag"))
    assessed = _spec_assessments(report)

    def distinct(key: str) -> tuple | None:
        values = sorted({v for spec in assessed if (v := _stored_text(spec.get(key)))})
        return tuple(values) or None

    return {"generation": generation, "tags": distinct("tag"), "revisions": distinct("revision")}


def _spec_change(newer: dict, older: dict) -> str | None:
    def differs(key: str) -> bool:
        return newer[key] is not None and older[key] is not None and newer[key] != older[key]

    def label(value: object) -> str:
        return ", ".join(value) if isinstance(value, tuple) else str(value)

    pieces = []
    if differs("generation"):
        pieces.append(f"{older['generation']} → {newer['generation']}")
    elif differs("tags"):
        pieces.append(f"{label(older['tags'])} → {label(newer['tags'])}")
    if differs("revisions"):
        pieces.append(f"revision {label(older['revisions'])} → {label(newer['revisions'])}")
    return " · ".join(pieces) or None


def _spec_chip_html(change: str | None) -> str:
    if change is None:
        return ""
    return f'<span class="chip chip-spec">Spec changed: {html.escape(change)}</span>'


def _finding_html(record: dict) -> str:
    grade = _record_grade(record)
    if grade in ("PASS", "UNSUPPORTED"):
        return ""
    for ev in record.get("failing_evaluators") or []:
        if isinstance(ev, dict):
            summary = _stored_text(ev.get("summary"))
            if summary is not None:
                klass = "finding finding-neutral" if ev.get("not_graded") is True else "finding"
                return f'<p class="{klass}">{html.escape(summary)}</p>'
    for item in record.get("outcome") or []:
        if isinstance(item, dict):
            finding = _stored_text(item.get("finding"))
            if finding is not None:
                return f'<p class="finding">{html.escape(finding)}</p>'
    return ""


def _styled_document(
    title: str,
    css_href: str,
    skip_id: str,
    skip_label: str,
    masthead: str,
    main_body: str,
    *,
    script_href: str | None = None,
) -> str:
    script = (
        f'<script src="{html.escape(script_href, quote=True)}"></script>\n' if script_href else ""
    )
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n"
        f'<link rel="stylesheet" href="{html.escape(css_href, quote=True)}">\n'
        "<body>\n"
        f'<a class="skip" href="#{html.escape(skip_id, quote=True)}">'
        f"{html.escape(skip_label)}</a>\n"
        f'<header class="masthead"><div class="wrap masthead-inner">{masthead}</div></header>\n'
        f'<main class="page wrap" id="{html.escape(skip_id, quote=True)}">{main_body}</main>\n'
        f"{script}"
        "</body>\n"
        "</html>\n"
    )


def _history_page_html(
    robot: str,
    variant: str,
    records: list[dict],
    reports: dict | None = None,
) -> str:
    newest = records[0]
    robot_title = _robot_title(robot)
    scenario_title = _scenario_title(newest, variant)
    subtitle = _variant_subtitle(newest, variant)
    count = len(records)
    plural = "s" if count != 1 else ""

    releases_dict = newest.get("releases") if isinstance(newest.get("releases"), dict) else {}
    releases_parts = []
    for name, release in releases_dict.items():
        if not isinstance(release, dict):
            continue
        rel_tag = _stored_text(release.get("tag"))
        rel_url = safe_href(_stored_text(release.get("url")))
        if rel_tag and rel_url:
            releases_parts.append(f"{html.escape(name)} {a(rel_url, rel_tag)}")
        elif rel_tag:
            releases_parts.append(f"{html.escape(name)} {html.escape(rel_tag)}")
    releases_suffix = (
        f'<span class="quiet"> · {" · ".join(releases_parts)}</span>' if releases_parts else ""
    )

    robot_info = newest.get("robot") if isinstance(newest.get("robot"), dict) else {}
    repo_str = _stored_text(robot_info.get("repository")) or robot
    repo_short = repo_str.rstrip("/").rsplit("/", 1)[-1]
    robot_tag = _stored_text(robot_info.get("tag"))
    robot_commit = _stored_text(robot_info.get("commit")) or ""
    robot_url = safe_href(_stored_text(robot_info.get("url")))
    robot_label = html.escape(f"{repo_short} {robot_tag or 'commit'}")
    robot_pin_title = (
        ""
        if robot_tag or not robot_commit
        else f' title="{html.escape("Commit " + robot_commit, quote=True)}"'
    )
    if robot_url:
        robot_line = (
            f'<a href="{html.escape(robot_url, quote=True)}"{robot_pin_title}>{robot_label}</a>'
        )
    else:
        robot_line = f"<span{robot_pin_title}>{robot_label}</span>"
    software_version = _stored_text(robot_info.get("software_version"))
    if software_version is not None:
        config = _stored_text(robot_info.get("configuration")) or ""
        sw_label = f"{config} {software_version}".strip()
        robot_line += f'<span class="quiet"> · software {html.escape(sw_label)}</span>'

    masthead = (
        f"{_site_nav_html('../../../index.html', '../../index.html')}"
        '<div class="masthead-top">'
        '<p class="crumb"><a href="../../index.html">← All verification reports</a></p>'
        f"{THEME_SWITCH}"
        "</div>"
        f'<p class="eyebrow">{html.escape(robot_title)} · {html.escape(subtitle)}</p>'
        f"<h1>{html.escape(scenario_title)}</h1>"
        f'<p class="lede">{count} run{plural}, newest first.</p>'
        f'<p class="meta-line">{a("latest/", "Latest report")}{releases_suffix}</p>'
        f'<p class="meta-line">{robot_line}</p>'
    )
    row_cards = []
    for position, record in enumerate(records):
        grade = _record_grade(record)
        row_class = "row is-fail" if grade in ("FAIL", "INSUFFICIENT_EVIDENCE") else "row is-pass"
        run_prefix = f"runs/{record['folder']}/"
        poster = record.get("poster")
        poster_name = (
            _stored_text(poster.get("name")) if isinstance(poster, dict) else None
        ) or "CI"
        below = records[position + 1] if position + 1 < len(records) else None
        change = None
        if below is not None:
            change = _spec_change(
                _spec_versions(record, (reports or {}).get(record["folder"])),
                _spec_versions(below, (reports or {}).get(below["folder"])),
            )
        chips = _chips_html(
            record,
            run_prefix,
            extra=_spec_chip_html(change),
            report=(reports or {}).get(record["folder"]),
        )
        finding = _finding_html(record)
        row_cards.append(
            f'<article class="{row_class}">'
            '<div class="row-top">'
            f"{_badge_html(grade)}"
            "<div>"
            f'<h2 class="title">{html.escape(scenario_title)}</h2>'
            f"{chips}{finding}"
            '<div class="row-meta">'
            f"{_release_html(record)}"
            '<span class="dot">·</span>'
            f"{_time_html(record['run']['created'])}"
            '<span class="dot">·</span>'
            f'<span class="poster">{html.escape(poster_name)}</span>'
            '<span class="dot">·</span>'
            f"{a(run_prefix, 'Report')}"
            "</div>"
            "</div>"
            "</div>"
            "</article>"
        )
    main_body = f'<div class="history">{"".join(row_cards)}</div>'
    return _styled_document(
        f"{robot_title} · {subtitle} · {scenario_title} · run history",
        "../../styles.css",
        "history",
        "Skip to history",
        masthead,
        main_body,
        script_href="../../open.js",
    )


def _index_card_html(robot: str, variant: str, records: list[dict]) -> str:
    newest = records[0]
    grade = _record_grade(newest)
    card_class = "card is-fail" if grade in ("FAIL", "INSUFFICIENT_EVIDENCE") else "card"
    scenario_title = _scenario_title(newest, variant)
    subtitle = _variant_subtitle(newest, variant)
    latest_href = f"{robot}/{variant}/latest/"
    history_href = f"{robot}/{variant}/"
    run_prefix = f"{robot}/{variant}/runs/{newest['folder']}/"
    chips = _chips_html(newest, run_prefix, linked=False)
    finding = _finding_html(newest)
    card_label = f"{scenario_title}, latest report ({_badge_label(grade)})"
    return (
        f'<article class="{card_class}">'
        '<div class="entry-main">'
        f"{_badge_html(grade)}"
        '<div class="entry-body">'
        f'<h3 class="title"><a class="card-link" href="{html.escape(latest_href, quote=True)}" '
        f'aria-label="{html.escape(card_label, quote=True)}">{html.escape(scenario_title)}</a></h3>'
        f'<p class="sub">{html.escape(subtitle)}</p>'
        f"{chips}{finding}"
        "</div>"
        '<div class="entry-side">'
        f'<div class="side-meta">{_release_html(newest, linked=False)}'
        f'<span class="dot">·</span>{_time_html(newest["run"]["created"])}</div>'
        '<div class="links"><a class="history-link" '
        f'href="{html.escape(history_href, quote=True)}">'
        f"History ({len(records)})</a></div>"
        "</div>"
        "</div>"
        "</article>"
    )


def _archived_history_html() -> str:
    return (
        '<p class="archived-history">'
        "Earlier history before per-repository reports is preserved in the "
        f'<a href="{html.escape(ARCHIVED_REPORTS_URL, quote=True)}">'
        "archived verification reports</a>.</p>"
    )


def _index_page_html(history: dict[tuple[str, str], list[dict]]) -> str:
    robots = {robot for robot, _ in history}
    robot = _require_single_robot(robots)
    robot_title = _robot_title(robot) if robot else "Living Proof Labs"

    variants = [(variant, records) for (_, variant), records in sorted(history.items())]
    ordered = sorted(
        variants,
        key=lambda item: (
            _grade_sort_rank(_record_grade(item[1][0])),
            _scenario_title(item[1][0], item[0]).lower(),
            _variant_subtitle(item[1][0], item[0]).lower(),
            item[0],
        ),
    )
    total_count = len(ordered)
    all_latest = [recs[0] for _, recs in ordered]

    if total_count == 0:
        body_html = '<p class="status">No verification reports published yet.</p>'
    else:
        fail_count = sum(1 for r in all_latest if _record_grade(r) == "FAIL")
        insuff_count = sum(1 for r in all_latest if _record_grade(r) == "INSUFFICIENT_EVIDENCE")
        pass_count = sum(1 for r in all_latest if _record_grade(r) == "PASS")
        unsup_count = sum(1 for r in all_latest if _record_grade(r) == "UNSUPPORTED")
        unknown_count = total_count - fail_count - insuff_count - pass_count - unsup_count

        if fail_count:
            noun = "result" if fail_count == 1 else "results"
            status_line = (
                f'<p class="status"><strong>{fail_count} latest {noun} failed.</strong> '
                "Failing scenarios are listed first below.</p>"
            )
        elif all(_record_grade(r) == "PASS" for r in all_latest):
            noun = "result" if total_count == 1 else "results"
            Verb = "passes" if total_count == 1 else "pass"
            status_line = f'<p class="all-pass">All {total_count} latest {noun} {Verb}.</p>'
        else:
            labels = {
                "PASS": "passed",
                "INSUFFICIENT_EVIDENCE": "insufficient evidence",
                "UNSUPPORTED": "not supported",
            }
            tally = {label: 0 for label in (*labels.values(), "unknown")}
            for rec in all_latest:
                tally[labels.get(_record_grade(rec), "unknown")] += 1
            summary = ", ".join(f"{c} {lbl}" for lbl, c in tally.items() if c)
            noun = "result" if total_count == 1 else "results"
            status_line = f'<p class="status">{total_count} latest {noun}: {summary}.</p>'

        scenario_noun = "scenario" if total_count == 1 else "scenarios"
        count_parts = [f"{total_count} {scenario_noun}"]
        if fail_count:
            count_parts.append(f'<span class="fail-count">{fail_count} failed</span>')
        if insuff_count:
            count_parts.append(f'<span class="fail-count">{insuff_count} insufficient</span>')
        if pass_count:
            count_parts.append(f'<span class="pass-count">{pass_count} passed</span>')
        if unsup_count:
            count_parts.append(f'<span class="pass-count">{unsup_count} not supported</span>')
        if unknown_count:
            count_parts.append(f'<span class="counts">{unknown_count} unknown</span>')
        counts_html = '<span class="count-sep">·</span>'.join(count_parts)

        dots = []
        cards = []
        assert robot is not None
        for variant, records in ordered:
            newest = records[0]
            grade = _record_grade(newest)
            scenario_title = _scenario_title(newest, variant)
            pip_cls, pip_status = {
                "FAIL": ("pip-fail", "failed"),
                "PASS": ("pip-pass", "passed"),
                "INSUFFICIENT_EVIDENCE": ("pip-warn", "insufficient evidence"),
                "UNSUPPORTED": ("pip-muted", "not supported"),
            }.get(grade, ("pip-muted", grade.lower()))
            dots.append(
                f'<span class="pip {pip_cls}" '
                f'title="{html.escape(f"{scenario_title}: {pip_status}", quote=True)}"></span>'
            )
            cards.append(_index_card_html(robot, variant, records))

        latest_record = max(
            all_latest,
            key=lambda r: (r["run"]["created"], r.get("folder", "")),
        )
        meta_latest = (
            f"{_release_html(latest_record, linked=False)}"
            '<span class="dot">·</span>'
            f"{_time_html(latest_record['run']['created'])}"
        )
        summary_bar = (
            '<div class="summary-bar">'
            f'<div class="counts">{counts_html}</div>'
            f'<span class="dots">{"".join(dots)}<span class="pip-more" hidden></span></span>'
            f'<span class="meta-latest">{meta_latest}</span>'
            "</div>"
        )
        scenarios_section = (
            '<section aria-labelledby="scenarios">'
            '<h2 class="scenarios-label" id="scenarios">Scenarios</h2>'
            f'<div class="stack">{"".join(cards)}</div>'
            "</section>"
        )
        body_html = status_line + summary_bar + scenarios_section

    masthead = (
        f"{_site_nav_html('../index.html', 'index.html', on_reports_index=True)}"
        '<div class="masthead-top">'
        f'<p class="eyebrow">{html.escape(robot_title)}</p>'
        f"{THEME_SWITCH}"
        "</div>"
        "<h1>Verification reports</h1>"
        '<p class="lede">The latest result for each scenario, with failures first.</p>'
    )
    page_title = f"{robot_title} · verification reports" if robot else "Verification reports"
    return _styled_document(
        page_title,
        "styles.css",
        "results",
        "Skip to results",
        masthead,
        body_html + _archived_history_html(),
        script_href="open.js",
    )
