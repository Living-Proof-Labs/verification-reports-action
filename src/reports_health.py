# SPDX-License-Identifier: Apache-2.0
"""Validate a bound Health sidecar and render only advisory measurements."""

import hashlib
import html
import json
import math
import re
from pathlib import Path

from reports_common import SiteError

MAX_HEALTH_BYTES = 4 << 20
SHA = re.compile(r"[a-f0-9]{64}")


def require(value, message):
    if not value:
        raise SiteError(f"Health advisory: {message}")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def document(data):
    try:
        result = json.loads(data, object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as error:
        raise SiteError("Health advisory: invalid JSON") from error

    def finite(value):
        if isinstance(value, float):
            require(math.isfinite(value), "non-finite number")
        elif isinstance(value, dict):
            for item in value.values():
                finite(item)
        elif isinstance(value, list):
            for item in value:
                finite(item)

    finite(result)
    require(isinstance(result, dict), "expected object")
    return result


def _read_sidecar(
    folder: Path, record: dict, report: dict, *, missing_ok=False, verify_inputs=False
):
    reference = record.get("health")
    if reference is None:
        if missing_ok:
            require(
                not (folder / "health.json").exists() and not (folder / "health.json").is_symlink(),
                "unreferenced restored sidecar",
            )
        return None
    require(isinstance(reference, dict), "invalid reference")
    require(record.get("fixture") is False, "Health requires a real recorded run")
    # The Action contract has exactly one fixed sidecar; never follow supplied URLs/paths.
    require(reference.get("path") == "health.json", "unsafe or unsupported sidecar path")
    require(reference.get("advisory") is True, "reference is not advisory")
    expected = reference.get("sha256")
    require(isinstance(expected, str) and SHA.fullmatch(expected), "invalid sidecar digest")
    path = folder / "health.json"
    require(not path.is_symlink(), "symlink sidecar")
    if missing_ok and not path.exists():
        return None  # Older published runs did not retain Health; expiry removes it too.
    require(path.is_file(), "missing sidecar")
    require(path.stat().st_size <= MAX_HEALTH_BYTES, "sidecar exceeds size limit")
    data = path.read_bytes()
    require(len(data) <= MAX_HEALTH_BYTES and digest(data) == expected, "sidecar digest mismatch")
    health = document(data)
    require(health.get("format") == "livingproof-evaluator-health/1", "unsupported format")
    trial = health.get("trial")
    require(isinstance(trial, dict) and trial == reference.get("trial"), "trial binding mismatch")
    require(type(trial.get("index")) is int and trial["index"] > 0, "invalid trial index")
    require(
        trial.get("configuration") == record.get("robot", {}).get("configuration"),
        "configuration mismatch",
    )
    require(
        isinstance(trial.get("observation_sha256"), str)
        and SHA.fullmatch(trial["observation_sha256"]),
        "invalid observation digest",
    )
    source = reference.get("source")
    require(isinstance(source, dict), "missing source provenance")
    require(
        source.get("name") == "livingproof-evaluator-health"
        and source.get("repository") == "Living-Proof-Labs/livingproof-evaluator-health",
        "unexpected evaluator source",
    )
    require(
        isinstance(source.get("tag"), str)
        and re.fullmatch(r"v\d+\.\d+\.\d+", source["tag"])
        and health.get("evaluator_version") == source["tag"][1:],
        "evaluator version mismatch",
    )
    require(
        isinstance(source.get("commit"), str) and re.fullmatch(r"[a-f0-9]{40}", source["commit"]),
        "invalid source commit",
    )
    require(
        isinstance(source.get("digest"), str)
        and re.fullmatch(r"sha256:[a-f0-9]{64}", source["digest"])
        and source.get("status") in {"resolved", "verified", "not-published"},
        "invalid source provenance",
    )
    summaries = report.get("summaries", [])
    systems = {
        r["system"]["id"]
        for r in summaries
        if isinstance(r, dict)
        and isinstance(r.get("system"), dict)
        and isinstance(r["system"].get("id"), str)
    }
    rows = report.get("source_bundle", {}).get("results", [])
    matched = [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("id") == reference.get("report_trial")
        and row.get("original_result", {}).get("index") == trial["index"]
        and row.get("provenance", {}).get("run_id") == trial.get("run_id")
        and row.get("system", {}).get("id") in systems
        and row.get("system", {}).get("id", "").endswith("/" + trial["configuration"])
    ]
    require(
        len(matched) == 1
        and any(
            e.get("sha256") == trial["observation_sha256"]
            for e in matched[0].get("evidence", [])
            if isinstance(e, dict)
        ),
        "report trial/observation mismatch",
    )
    simulator = health.get("simulator")
    robot = health.get("robot")
    require(isinstance(simulator, dict) and isinstance(robot, dict), "missing stream provenance")
    if simulator.get("status") == "recorded simulator observations":
        require(
            simulator.get("provenance") == "simulator"
            and type(simulator.get("sample_count")) is int
            and simulator["sample_count"] > 0,
            "invalid simulator sample count",
        )
        minima = simulator.get("minimum_recorded_base_height_by_frame")
        require(isinstance(minima, dict), "missing base-height measurements")
        for frame, measurement in minima.items():
            require(
                isinstance(frame, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", frame),
                "invalid frame",
            )
            require(isinstance(measurement, dict), "invalid measurement")
            height = measurement.get("height_m")
            require(type(height) in (int, float) and math.isfinite(height), "invalid height")
            require(
                isinstance(measurement.get("time_ns"), str)
                and re.fullmatch(r"\d{1,20}", measurement["time_ns"]),
                "invalid measurement time",
            )
    require(
        robot.get("status")
        in {
            "no robot health telemetry",
            "robot messages present; health interpretation unsupported",
            "robot stream not declared by this media version",
            "declared media file missing",
            "media disabled",
            "media missing",
        },
        "unsupported robot status",
    )
    if robot["status"] == "no robot health telemetry":
        require(
            robot.get("provenance") == "robot" and robot.get("message_counts") == {},
            "invalid empty robot stream",
        )
    if verify_inputs:
        inputs = {}
        for relative, key in (
            ("evidence/manifest.json", "evidence_manifest_sha256"),
            ("evidence/run-manifest.json", "run_manifest_sha256"),
            ("evidence/mcap/media.json", "media_manifest_sha256"),
        ):
            target = folder / relative
            require(
                not any(p.is_symlink() for p in [target, *target.parents] if p != folder.parent),
                "symlink input",
            )
            require(
                target.is_file() and target.stat().st_size <= MAX_HEALTH_BYTES,
                "missing or oversized input manifest",
            )
            raw = target.read_bytes()
            require(digest(raw) == health.get(key), "input manifest digest mismatch")
            inputs[key] = document(raw)
        manifest = inputs["evidence_manifest_sha256"]
        run = inputs["run_manifest_sha256"]
        media = inputs["media_manifest_sha256"]
        require(
            manifest.get("run_id") == run.get("run_id") == media.get("run_id") == trial["run_id"],
            "input run mismatch",
        )
        require(
            manifest.get("configuration", {}).get("name")
            == run.get("configuration", {}).get("name")
            == media.get("configuration")
            == trial["configuration"],
            "input configuration mismatch",
        )
        require(
            run.get("robot", {}).get("commit") == record["robot"].get("commit"),
            "input robot commit mismatch",
        )
        selected = [
            t
            for t in manifest.get("trials", {}).get("results", [])
            if t.get("index") == trial["index"] and t.get("sha256") == trial["observation_sha256"]
        ]
        require(len(selected) == 1, "input observation mismatch")
        relative = selected[0].get("path")
        require(
            isinstance(relative, str)
            and re.fullmatch(r"[A-Za-z0-9_./-]+", relative)
            and not relative.startswith("/")
            and all(p not in {"", ".", ".."} for p in relative.split("/")),
            "unsafe observation path",
        )
        observation = folder / "evidence" / relative
        require(
            not any(p.is_symlink() for p in [observation, *observation.parents]),
            "symlink observation",
        )
        require(
            observation.is_file() and observation.stat().st_size <= MAX_HEALTH_BYTES,
            "missing observation",
        )
        require(
            digest(observation.read_bytes()) == trial["observation_sha256"],
            "observation digest mismatch",
        )
    return data, health


def read_sidecar(
    folder: Path, record: dict, report: dict, *, missing_ok=False, verify_inputs=False
):
    try:
        return _read_sidecar(
            folder, record, report, missing_ok=missing_ok, verify_inputs=verify_inputs
        )
    except (AttributeError, TypeError, KeyError, OverflowError, RecursionError) as error:
        raise SiteError("Health advisory: malformed sidecar or binding metadata") from error


def advisory_html(health, reference):
    simulator = health["simulator"]
    parts = [
        '<section id="health-advisory" '
        'style="margin:2rem auto;padding:1rem;max-width:70rem;overflow-wrap:anywhere">'
        "<h2>Health observations — advisory</h2>",
        "<p>Recorded samples only. These observations do not change the grade, Scenario or "
        "Run Integrity results and do not establish physical robot health.</p>",
        "<h3>Simulator posture and base height</h3>",
    ]
    if simulator.get("status") == "recorded simulator observations":
        parts.append(f"<p>{simulator['sample_count']} recorded simulator samples.</p>")
        for frame, minimum in sorted(simulator["minimum_recorded_base_height_by_frame"].items()):
            seconds = int(minimum["time_ns"]) / 1_000_000_000
            parts.append(
                f"<p>Minimum recorded base height ({html.escape(frame)} frame): "
                f"{minimum['height_m']:g} m at {seconds:g} s.</p>"
            )
    else:
        parts.append("<p>Simulator base-height measurements unavailable.</p>")
    parts.append("<h3>Robot telemetry</h3>")
    if health["robot"]["status"] == "no robot health telemetry":
        parts.append(
            "<p>No robot health telemetry. The recorded robot stream is valid and empty; "
            "this is not a healthy verdict.</p>"
        )
    else:
        parts.append(
            "<p>Robot health assessment unavailable: "
            + html.escape(health["robot"]["status"])
            + ".</p>"
        )
    source = reference["source"]
    code = '<code style="white-space:normal;overflow-wrap:anywhere">'
    parts.append(
        f"<p>Evaluator {html.escape(source['tag'])}; "
        f"source commit {code}{source['commit']}</code>. "
        f"Source provenance: {html.escape(source['status'])}.</p>"
    )
    parts.append(
        '<p><a href="health.json">Recorded Health data</a> · SHA-256 '
        f"{code}{reference['sha256']}</code></p></section>"
    )
    return "".join(parts)
