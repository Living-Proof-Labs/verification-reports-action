# SPDX-License-Identifier: Apache-2.0
"""Caller-pinned embeds, applied only after ordinary report sanitation.

No evaluator supplies executable content. Restore requires the current caller pin,
not a trust decision based on a manifest recovered from an old Pages artifact.
"""

import hashlib
import html
import json
import re
from urllib.parse import urlencode

from reports_common import SiteError
from reports_render import checked_report_html

DIRECTORY = "trusted-viewer"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_file(path):
    if path.is_symlink() or not path.is_file():
        raise SiteError(f"Expected regular file: {path.name}")
    return path.read_bytes()


def binding(folder, pin):
    for key in ("viewer_sha256", "report_sha256"):
        if not re.fullmatch(r"[a-f0-9]{64}", pin[key]):
            raise SiteError("Caller must pin exact viewer and report SHA-256")
    if not re.fullmatch(r"[a-f0-9]{40}", pin["viewer_commit"]) or not pin["viewer_tag"].startswith(
        "v"
    ):
        raise SiteError("Caller must identify immutable viewer source")
    report_bytes = read_file(folder / "report.json")
    if digest(report_bytes) != pin["report_sha256"]:
        raise SiteError("Report differs from caller-selected report")
    report = json.loads(report_bytes)
    results = [r for r in report["source_bundle"]["results"] if r["id"] == pin["result_id"]]
    if len(results) != 1:
        raise SiteError("Expected one selected report result")
    result = results[0]
    if result["provenance"]["run_id"] != pin["run"] or result["system"]["id"] != pin["system"]:
        raise SiteError("Selected run/system mismatch")
    if pin["system"].rsplit("/", 1)[-1] != pin["configuration"]:
        raise SiteError("Selected configuration mismatch")
    found = []

    def visit(node):
        if isinstance(node, dict):
            if (
                isinstance(node.get("evaluator"), dict)
                and node["evaluator"].get("id") == pin["evaluator_id"]
                and "advisory" in node
            ):
                found.append(node["advisory"])
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(report)
    unique = {json.dumps(a, sort_keys=True): a for a in found}
    if len(unique) != 1:
        raise SiteError("Expected one unambiguous advisory")
    advisory = next(iter(unique.values()))
    trials = [t for t in advisory["trials"] if t["index"] == pin["trial"]]
    if len(trials) != 1 or trials[0]["observation_sha256"] not in {
        e["sha256"] for e in result["evidence"]
    }:
        raise SiteError("Advisory trial is not bound to the selected report evidence")
    details = advisory["details_file"]
    uri = details["uri"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.json", uri):
        raise SiteError("Expected a local JSON basename")
    raw = read_file(folder / uri)
    if digest(raw) != details["sha256"]:
        raise SiteError("Details differ from report-published digest")
    params = urlencode(
        {
            "file": "data/recording.json",
            "sha256": details["sha256"],
            "run": pin["run"],
            "configuration": pin["configuration"],
            "trial": pin["trial"],
        }
    )
    frame = f'<iframe title="Recorded replay" sandbox="allow-scripts allow-same-origin" loading="lazy" src="{DIRECTORY}/viewer.html#{html.escape(params, quote=True)}" style="display:block;width:100%;height:660px;border:1px solid #bcc;max-width:100%"></iframe>'  # noqa: E501
    return uri, raw, frame


def install(folder, pin, viewer_bytes):
    """Apply once to a copied, retained run; never alter report JSON or raw data."""
    if digest(viewer_bytes) != pin["viewer_sha256"]:
        raise SiteError("Viewer differs from trusted caller pin")
    uri, raw, frame = binding(folder, pin)
    original = read_file(folder / "index.html").decode("utf-8")
    clean = checked_report_html(original, "incoming report")  # Arbitrary iframe still fails.
    anchor = f"<a href='{html.escape(uri, quote=True)}'>Recorded data</a>"
    if clean.count(anchor) != 1:
        raise SiteError("Expected one existing raw-data link; ambiguous cards refused")
    target = folder / DIRECTORY
    target.mkdir()  # Refuse overwrite or existing symlink/directory.
    (target / "data").mkdir()
    (target / "viewer.html").write_bytes(viewer_bytes)
    (target / "data/recording.json").write_bytes(raw)
    (target / "sanitized-source.html").write_text(clean, encoding="utf-8")
    (folder / "index.html").write_text(clean.replace(anchor, anchor + frame), encoding="utf-8")
    revalidate(folder, pin)


def revalidate(folder, pin):
    """Validate an existing embed using a freshly supplied caller pin on restore."""
    uri, raw, frame = binding(folder, pin)
    target = folder / DIRECTORY
    if target.is_symlink() or (target / "data").is_symlink():
        raise SiteError("Viewer directories cannot be symlinks")
    if digest(read_file(target / "viewer.html")) != pin["viewer_sha256"]:
        raise SiteError("Restored viewer differs from caller pin")
    if read_file(target / "data/recording.json") != raw:
        raise SiteError("Restored viewer data differs from the report")
    page = read_file(folder / "index.html").decode("utf-8")
    if page.count(frame) != 1:
        raise SiteError("Restored iframe differs from site-generated binding")
    source = page.replace(frame, "")
    if source != read_file(target / "sanitized-source.html").decode("utf-8"):
        raise SiteError("Restored page changed outside generated embed")
    if checked_report_html(source, "restored source") != source:
        raise SiteError("Restored source contains unrecognized script")
    if any(p.is_symlink() for p in target.rglob("*")):
        raise SiteError("Symlink in trusted viewer installation")
    allowed = {"viewer.html", "sanitized-source.html", "data/recording.json"}
    if {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()} != allowed:
        raise SiteError("Unexpected file in trusted viewer installation")
    return source
