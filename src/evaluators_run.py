# SPDX-License-Identifier: Apache-2.0
"""Run declaration lines in the collect job and place the integrity lines.

The loader's exit code is not a branch. The branch is
``evaluators-provenance.json`` and each entry's ``outcome``. A missing,
unreadable, or non-list provenance file is a failure: the bundle records
NOT RUN with the loader's last stderr line, the report is rendered, and the
caller fails the job after the artifact upload.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

OUTCOMES = frozenset({"result", "refused", "failure"})
REPORTER_IMAGE = "ghcr.io/living-proof-labs/livingproof-capability-report"


def last_stderr_line(stderr: str) -> str:
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    return lines[-1] if lines else "the evaluator wrote no result"


def load_provenance(path: Path):
    """The provenance list, or None when it is missing, unreadable, or not that list."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list):
        return None
    for entry in payload:
        if not isinstance(entry, dict) or entry.get("outcome") not in OUTCOMES:
            return None
    return payload


def classify(provenance, exit_code: int) -> str:
    """``recorded`` when provenance is a list of outcomes. ``exit_code`` is unused."""
    del exit_code
    if provenance is None:
        return "missing"
    return "recorded"


def reporter_image(records: list) -> str:
    """The reporter image the lock's provenance record names. Not this repo's dev pin."""
    record = next(
        (
            item
            for item in records
            if isinstance(item, dict) and item.get("name") == "livingproof-capability-report"
        ),
        None,
    )
    if record is None:
        raise RuntimeError("resolved pins are missing the reporter")
    tag = str(record.get("tag") or "")
    if tag.startswith("v"):
        tag = tag[1:]
    digest = str(record.get("digest") or "")
    image = f"{REPORTER_IMAGE}:{tag}@{digest}"
    if not digest.startswith("sha256:") or tag == "":
        raise RuntimeError(f"reporter image is not a resolved ref. found {image}")
    return image


def record_missing(bundle: dict, stderr: str) -> str:
    from capability_verifier.collect import _attach_not_run

    reason = last_stderr_line(stderr)
    _attach_not_run(bundle, "failure", reason)
    return reason


def write_integrity_file(report_dir: Path, bundle: dict) -> None:
    from capability_verifier.collect import integrity_report_lines

    lines = integrity_report_lines(bundle)
    if not lines:
        return
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "integrity-lines.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def place_lines(report_dir: Path) -> None:
    from capability_verifier.collect import place_integrity_lines

    place_integrity_lines(report_dir)


def write_report_checksums(report_dir: Path) -> None:
    """Hash report.* and thresholds.json, the files capability-verifier sums after placement."""
    lines = []
    for name in sorted(path.name for path in report_dir.glob("report.*") if path.is_file()):
        digest = hashlib.sha256((report_dir / name).read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}")
    thresholds = sorted(
        path.relative_to(report_dir).as_posix()
        for path in report_dir.rglob("thresholds.json")
        if path.is_file()
    )
    for relative in thresholds:
        digest = hashlib.sha256((report_dir / relative).read_bytes()).hexdigest()
        lines.append(f"{digest}  {relative}")
    (report_dir / "SHA256SUMS").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )


def loader_command(
    lines_file: Path, evidence: Path, out: Path, bundle: Path, media_dir: Path | None
) -> list[str]:
    command = [
        "uv",
        "run",
        "--no-sync",
        "python",
        "-m",
        "capability_verifier.evaluators",
        "run",
        "--lines",
        str(lines_file),
        "--evidence-manifest",
        str(evidence),
        "--out",
        str(out),
        "--bundle",
        str(bundle),
    ]
    if media_dir is not None:
        command += ["--media-dir", str(media_dir)]
    return command


def render_with_reporter(image: str, manifest: Path, report_dir: Path, links: Path | None) -> None:
    """Render json, markdown, html, and text with the image the lock pinned."""

    def run(command):
        return subprocess.run(command, capture_output=True, text=True, check=False)

    report_dir.mkdir(parents=True, exist_ok=True)
    mounts = ["-v", f"{manifest.parent}:/bundle:ro", "-v", f"{report_dir}:/out"]
    link_args: list[str] = []
    if links is not None and links.is_file():
        mounts += ["-v", f"{links.parent}:/links:ro"]
        link_args = ["--evidence-links", f"/links/{links.name}"]
    base = [
        "docker",
        "run",
        "--rm",
        *mounts,
        "--entrypoint",
        "livingproof-capability-report",
        image,
    ]
    manifest_arg = f"/bundle/{manifest.name}"
    for fmt, name in (("json", "report.json"), ("markdown", "report.md"), ("html", "report.html")):
        completed = run(
            base + ["render", manifest_arg, *link_args, "--format", fmt, "-o", f"/out/{name}"]
        )
        _require_ran(completed, f"reporter {fmt}")
    completed = run(base + ["render", "/out/report.json"])
    _require_ran(completed, "reporter text")
    (report_dir / "report.txt").write_text(completed.stdout or "", encoding="utf-8")


def _require_ran(completed, label: str) -> None:
    code = getattr(completed, "returncode", 0)
    if code not in (0, None):
        detail = (getattr(completed, "stderr", "") or "").strip()
        raise RuntimeError(f"{label} exited {code}. {detail}")


def artifact_dir(parent: Path, prefix: str, kind: str, configuration: str) -> Path | None:
    if not parent.is_dir():
        return None
    direct = parent / f"{prefix}{kind}-{configuration}"
    return direct if direct.is_dir() else None


def apply_configuration(
    *,
    lines: list[str],
    evidence: Path,
    bundle_path: Path,
    report_dir: Path,
    out: Path,
    provenance_records: list,
    media_dir: Path | None = None,
    links: Path | None = None,
    runner=None,
    render=None,
) -> bool:
    """Run one configuration. Returns True when the collect job must fail after upload."""
    if not lines:
        return False
    runner = subprocess.run if runner is None else runner
    render = render_with_reporter if render is None else render
    out.mkdir(parents=True, exist_ok=True)
    lines_file = out / "lines.txt"
    lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    command = loader_command(lines_file, evidence, out, bundle_path, media_dir)
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    completed = runner(command, capture_output=True, text=True, env=env)
    stderr = getattr(completed, "stderr", "") or ""
    code = getattr(completed, "returncode", 0)
    if stderr:
        print(stderr, file=sys.stderr, end="" if stderr.endswith("\n") else "\n")
    provenance = load_provenance(out / "evaluators-provenance.json")
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    failed = classify(provenance, code) == "missing"
    if failed:
        record_missing(bundle, stderr)
        bundle_path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    else:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    image = reporter_image(provenance_records)
    render(image, bundle_path, report_dir, links)
    write_integrity_file(report_dir, bundle)
    place_lines(report_dir)
    write_report_checksums(report_dir)
    return failed


def _write_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")


def run_downloaded(plan: dict, download: Path, out_root: Path, runner=None, render=None) -> bool:
    """Run every verifier configuration that has declaration lines. True means fail after upload."""
    declared = plan.get("evaluators") if isinstance(plan.get("evaluators"), dict) else {}
    lines = declared.get("lines") if isinstance(declared.get("lines"), list) else []
    lines = [line for line in lines if isinstance(line, str) and line.strip()]
    if not lines:
        return False
    failed = False
    for item in plan.get("definitions") or []:
        if not isinstance(item, dict) or item.get("execution") != "capability-verifier":
            continue
        prefix = str(item.get("artifactPrefix") or "")
        records = item.get("provenance") if isinstance(item.get("provenance"), list) else []
        for configuration in item.get("configurations") or []:
            evidence_dir = artifact_dir(download / "evidence", prefix, "evidence", configuration)
            verified_dir = artifact_dir(download / "verified", prefix, "verified", configuration)
            report_dir = artifact_dir(
                download / "reports", prefix, "capability-report", configuration
            )
            evidence = evidence_dir / "manifest.json" if evidence_dir else None
            bundle_path = verified_dir / "bundle" / "manifest.json" if verified_dir else None
            if (
                evidence is None
                or bundle_path is None
                or report_dir is None
                or not evidence.is_file()
                or not bundle_path.is_file()
            ):
                where = item.get("path") or item.get("id")
                raise RuntimeError(
                    f"{where}: evidence or verified bundle for {configuration} is missing"
                )
            media = artifact_dir(download / "mcap", prefix, "mcap", configuration)
            media_dir = media if media is not None and (media / "media.json").is_file() else None
            links_dir = artifact_dir(download / "links", prefix, "links", configuration)
            links = links_dir / "evidence-links.json" if links_dir else None
            out = out_root / str(item.get("id") or "definition") / str(configuration)
            failed = (
                apply_configuration(
                    lines=lines,
                    evidence=evidence,
                    bundle_path=bundle_path,
                    report_dir=report_dir,
                    out=out,
                    provenance_records=records,
                    media_dir=media_dir,
                    links=links if links is not None and links.is_file() else None,
                    runner=runner,
                    render=render,
                )
                or failed
            )
    return failed


def main(argv: list[str] | None = None) -> int:
    del argv
    plan_path = Path(os.environ.get("INPUT_PLAN_PATH") or "")
    download = Path(os.environ.get("INPUT_DOWNLOAD_ROOT") or "downloaded")
    if not plan_path.is_file():
        print("evaluators: INPUT_PLAN_PATH is missing", file=sys.stderr)
        _write_output("failed", "true")
        return 0
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    try:
        failed = run_downloaded(plan, download, download / "evaluator-out")
    except Exception as exc:
        print(f"evaluators: {exc}", file=sys.stderr)
        _write_output("failed", "true")
        return 0
    _write_output("failed", "true" if failed else "false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
