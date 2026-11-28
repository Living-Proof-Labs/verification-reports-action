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
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

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


def declared_evaluator(lines: list[str]) -> str:
    """The declaration the loader was given.

    capability-verifier v3 records that text on a missing integrity result.
    The loader writes no evaluator id when it refuses before producing a record.
    """
    declared = [line.strip() for line in lines if isinstance(line, str) and line.strip()]
    if not declared:
        raise RuntimeError("a missing evaluator result needs the declaration line")
    return "\n".join(declared)


def record_missing(bundle: dict, stderr: str, *, evaluator: str) -> str:
    from capability_verifier.collect import _attach_not_run

    reason = last_stderr_line(stderr)
    _attach_not_run(bundle, "failure", reason, evaluator=evaluator)
    return reason


def write_integrity_file(report_dir: Path, bundle: dict) -> None:
    from capability_verifier.collect import integrity_report_lines

    lines = integrity_report_lines(bundle)
    report_dir.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    (report_dir / "integrity-lines.txt").write_text(text + "\n", encoding="utf-8")


def place_lines(report_dir: Path) -> None:
    from capability_verifier.collect import place_integrity_lines

    place_integrity_lines(report_dir)


def write_report_checksums(report_dir: Path, bundle: dict | None = None) -> None:
    """Hash report files, thresholds.json, and each cited advisory details file.

    Details files come from ``bundle.assessment.advisory_results``. One result
    or a list is accepted. Only the cited relative paths are listed, not a glob.
    """
    from details_files import DetailsPathError, cited_details_uris, safe_relative

    lines = []
    seen: set[str] = set()

    def add(relative: str, data: bytes) -> None:
        if relative in seen:
            return
        seen.add(relative)
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {relative}")

    for name in sorted(path.name for path in report_dir.glob("report.*") if path.is_file()):
        add(name, (report_dir / name).read_bytes())
    thresholds = sorted(
        path.relative_to(report_dir).as_posix()
        for path in report_dir.rglob("thresholds.json")
        if path.is_file() and not path.is_symlink()
    )
    for relative in thresholds:
        add(relative, (report_dir / relative).read_bytes())
    cited: list[str] = []
    for uri in cited_details_uris(bundle or {}):
        try:
            relative = safe_relative(uri).as_posix()
        except DetailsPathError as exc:
            raise RuntimeError(str(exc)) from exc
        if relative not in seen and relative not in cited:
            cited.append(relative)
    for relative in sorted(cited):
        current = report_dir
        for part in PurePosixPath(relative).parts:
            current = current / part
            if current.is_symlink():
                raise RuntimeError(f"cited details file {relative} is a symlink")
        if not current.is_file():
            raise RuntimeError(f"cited details file {relative} is missing")
        add(relative, current.read_bytes())
    (report_dir / "SHA256SUMS").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )


def loader_command(
    lines_file: Path,
    evidence: Path,
    out: Path,
    bundle: Path,
    report_dir: Path,
    media_dir: Path | None,
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
        "--report-dir",
        str(report_dir),
    ]
    if media_dir is not None:
        command += ["--media-dir", str(media_dir)]
    return command


def reporter_docker_command(
    image: str,
    manifest: Path,
    report_dir: Path,
    links: Path | None,
    *,
    text: bool = False,
    fmt: str | None = None,
    output: str | None = None,
) -> list[str]:
    """Run the image's own entrypoint as this user, with a writable working directory.

    The reporter image's entrypoint is the CLI and its default user is root. A
    root-owned report cannot be updated by the collect job, so the container
    uses the current uid and writes under ``/out``.
    """
    mounts = [
        "-v",
        f"{manifest.parent.resolve()}:/bundle:ro",
        "-v",
        f"{report_dir.resolve()}:/out",
    ]
    link_args: list[str] = []
    if links is not None and links.is_file():
        mounts += ["-v", f"{links.parent.resolve()}:/links:ro"]
        link_args = ["--evidence-links", f"/links/{links.name}"]
    command = [
        "docker",
        "run",
        "--rm",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-w",
        "/out",
        *mounts,
        image,
    ]
    if text:
        return command + ["render", "/out/report.json"]
    return command + [
        "render",
        f"/bundle/{manifest.name}",
        *link_args,
        "--format",
        fmt or "",
        "-o",
        f"/out/{output}",
    ]


def render_with_reporter(
    image: str, manifest: Path, report_dir: Path, links: Path | None, runner=None
) -> None:
    """Render into an empty directory, then replace the downloaded report files.

    Reporter 3.17.0 refuses to overwrite an ``-o`` target. The capability-report
    artifact already holds ``report.json``, ``report.md`` and ``report.html``, so
    the container writes into a fresh directory mounted at ``/out``.
    """

    def run(command):
        if runner is not None:
            return runner(command)
        return subprocess.run(command, capture_output=True, text=True, check=False)

    report_dir.mkdir(parents=True, exist_ok=True)
    names = ("report.json", "report.md", "report.html", "report.txt")
    with tempfile.TemporaryDirectory(prefix="vra-reporter-") as scratch_name:
        scratch = Path(scratch_name)
        for fmt, name in (
            ("json", "report.json"),
            ("markdown", "report.md"),
            ("html", "report.html"),
        ):
            completed = run(
                reporter_docker_command(image, manifest, scratch, links, fmt=fmt, output=name)
            )
            _require_ran(completed, f"reporter {fmt}")
        completed = run(reporter_docker_command(image, manifest, scratch, links, text=True))
        _require_ran(completed, "reporter text")
        (scratch / "report.txt").write_text(completed.stdout or "", encoding="utf-8")
        missing = [name for name in names if not (scratch / name).is_file()]
        if missing:
            raise RuntimeError(f"reporter left no {', '.join(missing)}")
        for name in names:
            shutil.move(scratch / name, report_dir / name)


def _require_ran(completed, label: str) -> None:
    code = getattr(completed, "returncode", 0)
    if code not in (0, None):
        detail = (getattr(completed, "stderr", "") or "").strip()
        raise RuntimeError(f"{label} exited {code}. {detail}")


def resolve_caller_evaluators(value: str, root: Path) -> dict:
    """Same caller input as the plan: empty, a relative path, or the list itself."""
    text = value or ""
    if not text.strip() or text.strip() == "[]":
        return {"lines": [], "input": "", "run": False}
    single = "\n" not in text and "\r" not in text
    trimmed = text.strip()
    if single and "@" not in trimmed and not trimmed.startswith("-"):
        if trimmed.startswith("/") or "\\" in trimmed or ".." in trimmed.split("/"):
            raise RuntimeError(
                f"evaluators path {trimmed!r} must be a relative path in the caller repository."
            )
        file = (root / trimmed).resolve()
        base = root.resolve()
        if file != base and base not in file.parents:
            raise RuntimeError(
                f"evaluators path {trimmed!r} must stay inside the caller repository."
            )
        if not file.is_file():
            raise RuntimeError(f"evaluators file {trimmed} was not found in the caller repository.")
        return _declaration(file.read_text(encoding="utf-8"))
    return _declaration(text)


def _declaration(text: str) -> dict:
    substantive = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    items = []
    yaml_list = any(line.startswith("- ") for line in substantive)
    for line in substantive:
        if line in {"[]", "---", "-"}:
            continue
        if yaml_list:
            if not line.startswith("- "):
                raise RuntimeError(f"evaluators list expected '- <line>', found {line!r}.")
            item = line[2:].strip()
            if len(item) >= 2 and item[0] == item[-1] and item[0] in {"'", '"'}:
                item = item[1:-1]
        else:
            item = line
        if item:
            items.append(item)
    if not items:
        return {"lines": [], "input": "", "run": False}
    return {"lines": items, "input": "\n".join(items), "run": True}


def gather_evaluator_results(
    parent: Path, prefix: str, configuration: str, staging: Path
) -> Path | None:
    """One directory of every evaluator-results artifact for this configuration."""
    if not parent.is_dir():
        return None
    needle = f"{prefix}evaluator-results-"
    suffix = f"-{configuration}"
    matches = [
        child
        for child in sorted(parent.iterdir())
        if child.is_dir() and child.name.startswith(needle) and child.name.endswith(suffix)
    ]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for source in matches:
        shutil.copytree(source, staging / source.name, symlinks=False)
    return staging


def attach_scenario_results(bundle_path: Path, results_dir: Path | None, report_dir: Path) -> None:
    """Append scenario and media results before the loader adds Run Integrity.

    The verified bundle is written by the verify job, before capability-verifier's
    report job runs collect(). Re-rendering that bundle alone drops those results.
    """
    if results_dir is None or not results_dir.is_dir():
        return
    from capability_verifier.collect import collect

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    collect(bundle, results_dir, report_dir)
    bundle_path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")


def artifact_dir(parent: Path, prefix: str, kind: str, configuration: str) -> Path | None:
    if not parent.is_dir():
        return None
    direct = parent / f"{prefix}{kind}-{configuration}"
    return direct if direct.is_dir() else None


def record_apply_failure(
    bundle_path: Path, report_dir: Path, reason: str, *, evaluator: str
) -> None:
    """NOT RUN on the original report, so collect still finds integrity-lines.txt."""
    try:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        bundle = {}
    if not isinstance(bundle, dict):
        bundle = {}
    from capability_verifier.collect import _attach_not_run

    _attach_not_run(bundle, "failure", reason, evaluator=evaluator)
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    write_integrity_file(report_dir, bundle)
    place_lines(report_dir)
    write_report_checksums(report_dir)


TRIAL_KINDS = frozenset({"scenario"})


def executable_lines(lines: list[str], prepare=None) -> list[str]:
    """The declaration lines the loader still has to run in the collect job.

    The trial workflows already ran every ``scenario`` line and uploaded its results,
    and ``attach_scenario_results`` places them. The loader runs a scenario line
    again when it is given one, and would place its results a second time. The kind
    is read from each line's descriptor, so nothing here names an evaluator.
    ``prepare`` is capability-verifier's own validation, which installs nothing.
    """
    if prepare is None:
        from capability_verifier.evaluators import prepare
    prepared = prepare("\n".join(lines) + "\n")
    return [
        item.declaration.line for item in prepared if item.document.get("kind") not in TRIAL_KINDS
    ]


_SITE_PATH = re.compile(r"[A-Za-z0-9._~!$&'()*+,;=@%/-]{1,240}")
_VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".avi"}


def _site_path(root: Path, file: Path, folder: str) -> str | None:
    """The path the site publishes ``file`` at, or None when no page may point at it."""
    relative = f"evidence/{folder}/{file.relative_to(root).as_posix()}"
    if not _SITE_PATH.fullmatch(relative) or "artifact" in relative.lower():
        return None
    return relative


def _trial_video(download: Path, prefix: str, configuration: str, entry: dict) -> str | None:
    seed, status = entry.get("seed"), entry.get("status")
    if type(seed) is not int or status not in {"PASS", "FAIL"}:
        return None
    root = download / "video"
    if not root.is_dir():
        return None
    name = re.compile(
        rf"{re.escape(prefix)}video-{re.escape(configuration)}-seed-0*{seed}-{status.lower()}"
    )
    found = [
        file
        for directory in sorted(root.iterdir())
        if directory.is_dir() and name.fullmatch(directory.name)
        for file in sorted(directory.rglob("*"))
        if file.is_file() and file.suffix.lower() in _VIDEO_SUFFIXES
    ]
    if len(found) != 1:
        return None
    directory = next(parent for parent in found[0].parents if parent.parent == root)
    return _site_path(directory, found[0], "video")


def _trial_recordings(download: Path, prefix: str, configuration: str) -> list[tuple[str, str]]:
    """(name, site path) of each trial recording, not the robot-side copies."""
    root = download / "mcap" / f"{prefix}mcap-{configuration}"
    if not root.is_dir():
        return []
    found = []
    for file in sorted(root.rglob("*.mcap")):
        if not file.is_file() or file.name.endswith(".robot.mcap"):
            continue
        path = _site_path(root, file, "mcap")
        if path:
            found.append((file.stem, path))
    return found


def site_evidence_links(
    links: Path | None, download: Path, prefix: str, configuration: str, out: Path
) -> Path | None:
    """Give the reporter the site paths of the media this job copies into the run folder.

    The verifier workflow uploads each link as a GitHub Actions artifact address. The
    reporter never renders those, so a published page showed 'video' and the MCAP entry as
    plain text. The collect job copies the same files under ``evidence/``, so each link that
    names one is given that relative path. Any other link is passed through unchanged.
    """
    if links is None or not links.is_file():
        return links
    document = json.loads(links.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("links"), list):
        return links
    resolved: list = []
    for entry in document["links"]:
        if not isinstance(entry, dict) or entry.get("path") or not entry.get("url"):
            resolved.append(entry)
        elif entry.get("kind") == "video" and (
            path := _trial_video(download, prefix, configuration, entry)
        ):
            resolved.append({**entry, "path": path})
        elif (
            entry.get("kind") == "mcap"
            and "seed" not in entry
            and (recordings := _trial_recordings(download, prefix, configuration))
        ):
            for name, path in recordings:
                label = entry.get("label") if len(recordings) == 1 else f"MCAP {name}"
                resolved.append({**entry, "label": label, "path": path})
        else:
            resolved.append(entry)
    target = out / "evidence-links.json"
    target.write_text(json.dumps({**document, "links": resolved}, indent=2) + "\n", "utf-8")
    return target


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
    results_dir: Path | None = None,
    configuration: str = "",
    runner=None,
    render=None,
    select=None,
) -> bool:
    """Run one configuration. Returns True when the collect job must fail after upload.

    ``select`` narrows the declaration lines to those the loader has to run here. A line
    it refuses is recorded as NOT RUN like any other refusal. With none left the report
    already is what the trial jobs wrote, so nothing is run or rendered again.
    """
    if not lines:
        return False
    runner = subprocess.run if runner is None else runner
    render = render_with_reporter if render is None else render
    evaluator = declared_evaluator(lines)
    try:
        if select is not None:
            lines = select(lines)
            if not lines:
                return False
        attach_scenario_results(bundle_path, results_dir, report_dir)
        out.mkdir(parents=True, exist_ok=True)
        lines_file = out / "lines.txt"
        lines_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        command = loader_command(lines_file, evidence, out, bundle_path, report_dir, media_dir)
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
            record_missing(bundle, stderr, evaluator=evaluator)
            bundle_path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
        else:
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        image = reporter_image(provenance_records)
        render(image, bundle_path, report_dir, links)
        write_integrity_file(report_dir, bundle)
        place_lines(report_dir)
        write_report_checksums(report_dir, bundle)
        return failed
    except AssertionError:
        raise
    except Exception as exc:
        reason = f"{configuration}: {exc}" if configuration else str(exc)
        print(f"evaluators: {reason}", file=sys.stderr)
        record_apply_failure(bundle_path, report_dir, reason, evaluator=evaluator)
        return True


def _write_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")


def run_downloaded(
    plan: dict, download: Path, out_root: Path, runner=None, render=None, select=None
) -> bool:
    """Run every verifier configuration that has declaration lines. True means fail after upload.

    ``select`` is passed to :func:`apply_configuration`. The job entry point passes
    :func:`executable_lines`; without it every declared line goes to the loader.
    """
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
            out.mkdir(parents=True, exist_ok=True)
            links = site_evidence_links(links, download, prefix, str(configuration), out)
            results_dir = gather_evaluator_results(
                download / "evaluator-results",
                prefix,
                str(configuration),
                out / "scenario-results",
            )
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
                    results_dir=results_dir,
                    configuration=str(configuration),
                    runner=runner,
                    render=render,
                    select=select,
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
        if "INPUT_EVALUATORS" in os.environ:
            caller = Path(os.environ.get("INPUT_CALLER_ROOT") or ".")
            declared = resolve_caller_evaluators(os.environ.get("INPUT_EVALUATORS") or "", caller)
            plan["evaluators"] = {"lines": declared["lines"], "input": declared["input"]}
        failed = run_downloaded(plan, download, download / "evaluator-out", select=executable_lines)
    except Exception as exc:
        print(f"evaluators: {exc}", file=sys.stderr)
        _write_output("failed", "true")
        return 0
    _write_output("failed", "true" if failed else "false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
