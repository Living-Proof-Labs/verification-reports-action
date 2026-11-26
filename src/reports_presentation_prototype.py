# SPDX-License-Identifier: Apache-2.0
"""LOCAL staging only; installed candidate API, no production dependency pin."""

import hashlib
import html
import json
import re

from reports_common import SiteError
from reports_health import advisory_html, document, read_sidecar

INPUT_FILES = {
    "report": "report-render-input.json",
    "integrity_lines": "report-integrity-lines.txt",
}


def prepare_inputs(source, record, report, original_html):
    if "reporter_input" not in record:
        return {}  # Legacy archive: retain the validated advisory, never invent a peer.
    reference = record.get("reporter_input")
    if not isinstance(reference, dict) or reference.get("format") != "livingproof-reporter-input/1":
        raise SiteError("Health replay requires bound original Reporter input")
    result = {}
    for key, name in INPUT_FILES.items():
        entry = reference.get(key)
        path = source / name
        if (
            not isinstance(entry, dict)
            or entry.get("path") != name
            or path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > 4 << 20
        ):
            raise SiteError("Missing or unsafe original Reporter input")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            raise SiteError("Original Reporter input digest mismatch")
        result[name] = data
    original = document(result[INPUT_FILES["report"]])
    for key in ("source_bundle", "source_bundle_sha256", "summaries", "decisions"):
        if original.get(key) != report.get(key):
            raise SiteError(f"Original Reporter input does not match collected {key}")
    lines = result[INPUT_FILES["integrity_lines"]].decode("utf-8").splitlines()
    if not lines or any(
        not line.startswith("Run Integrity: ") or len(line) > 2000 for line in lines
    ):
        raise SiteError("Invalid original Integrity annotations")
    shown = "".join(
        f"<p class='stored-status integrity-status'>{html.escape(line, quote=False)}</p>"
        for line in lines
    )
    if shown not in original_html:
        raise SiteError("Integrity annotations do not match original portable HTML")
    return result


def preserve_pinned_inputs(original_html, page):
    """Retain the producer-owned stamp; the final publication policy still applies."""
    pattern = r'<section id="pinned-inputs">[\s\S]*?</section>\n?'
    sections = re.findall(pattern, original_html)
    if len(sections) > 1 or re.search(pattern, page):
        raise SiteError("Ambiguous Pinned inputs section during Reporter replay")
    if not sections:
        return page
    page, count = re.subn(r"</body\s*>", lambda _: sections[0] + "</body>", page, flags=re.I)
    if count != 1:
        raise SiteError("Reporter replay has no unique Pinned inputs insertion location")
    return page


def prototype_html(folder, record, report, original_html, days):
    validated = read_sidecar(folder, record, report, missing_ok=True)
    if validated is None:
        return original_html
    if "reporter_input" not in record:
        section = advisory_html(validated[1], record["health"])
        return re.sub(
            r"</body\s*>", lambda _: section + "</body>", original_html, count=1, flags=re.I
        )
    from livingproof_capability_report import EvaluatorPresentation, render

    original = document((folder / ("." + INPUT_FILES["report"])).read_bytes())
    lines = (folder / ("." + INPUT_FILES["integrity_lines"])).read_text().splitlines()
    for key in ("source_bundle", "source_bundle_sha256", "summaries", "decisions"):
        if original[key] != report[key]:
            raise SiteError(f"Original Reporter input does not match collected {key}")
    health = validated[1]
    simulator = health["simulator"]
    details = []
    if simulator["status"] == "recorded simulator observations":
        details.append(f"Simulator: {simulator['sample_count']} recorded samples.")
        for frame, minimum in sorted(simulator["minimum_recorded_base_height_by_frame"].items()):
            details.append(
                f"Minimum recorded base height ({frame}): {minimum['height_m']:g} m "
                f"at {int(minimum['time_ns']) / 1_000_000_000:g} s."
            )
    else:
        details.append("Simulator base-height measurements unavailable.")
    if health["robot"]["status"] == "no robot health telemetry":
        details.append(
            "No robot health telemetry. The robot stream is valid and empty; "
            "this is not a healthy verdict."
        )
    else:
        details.append("Robot health assessment unavailable: " + health["robot"]["status"] + ".")
    reference = record["health"]
    details.append(
        f"Source {reference['source']['tag']} at {reference['source']['commit']}; "
        f"provenance {reference['source']['status']}. "
        f"Health SHA-256 {reference['sha256']}."
    )
    contribution = EvaluatorPresentation(
        id="livingproof-health",
        title="Health",
        version=health["evaluator_version"],
        purpose="Recorded simulator posture and base height, separate from robot telemetry. "
        "Optional advisory observations; no effect on grade, Scenario or Run Integrity.",
        details=" ".join(details),
        details_href="health.json",
        source_bundle_sha256=original["source_bundle_sha256"],
    )
    # Reuse the hash-bound observation duration through the established render input.
    manifest = json.loads((folder / "evidence/manifest.json").read_text())
    trial = next(
        t for t in manifest["trials"]["results"] if t["index"] == reference["trial"]["index"]
    )
    observation = json.loads((folder / "evidence" / trial["path"]).read_text())
    observations = {reference["report_trial"]: observation["duration_s"]}
    page = render(
        original,
        "html",
        evaluator_presentations=[contribution],
        observations=observations,
        retention=days,
    )
    # Replay the producer's existing post-render annotation, not a newly inferred verdict.
    # capability-verifier v2.12.5 collect.py place_integrity_lines,
    # commit e6a1ba347c525b62abf0644b610562b7bfb390d9.
    shown = "".join(
        f"<p class='stored-status integrity-status'>{html.escape(line, quote=False)}</p>"
        for line in lines
    )
    page, count = re.subn(
        r"<p class=(['\"])stored-status\1>[^<]*</p>", lambda m: m.group(0) + shown, page
    )
    if not count:
        raise SiteError("Reporter replay has no compatible verdict annotation location")
    page = preserve_pinned_inputs(original_html, page)
    for name in INPUT_FILES.values():
        (folder / ("." + name)).unlink()
    return page
