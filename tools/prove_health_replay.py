import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--g1-input", type=Path, required=True)
parser.add_argument("--reporter-input", type=Path, required=True)
parser.add_argument("--python", required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
source = args.g1_input
original = {
    str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in source.rglob("*")
    if p.is_file()
}
base = args.output
base.mkdir(exist_ok=True)
outputs = {}
for variant in (
    "on",
    "off",
    "bad-digest",
    "missing",
    "unsafe",
    "input-missing",
    "input-tampered",
    "annotation-tampered",
    "input-mismatch",
):
    inputs = base / variant / "input"
    shutil.copytree(source, inputs)
    record = next(inputs.rglob("run.json"))
    data = json.loads(record.read_text())
    captured = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            f"import {{preserveReporterInput}} from '{(root / 'src/assemble.mjs').as_uri()}'; "
            "console.log(JSON.stringify(preserveReporterInput(process.argv[1],process.argv[2])));",
            str(args.reporter_input),
            str(record.parent),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data["reporter_input"] = json.loads(captured.stdout)
    assert (
        record.with_name("report-render-input.json").read_bytes()
        == (args.reporter_input / "report.json").read_bytes()
    )
    if variant == "input-missing":
        record.with_name("report-render-input.json").unlink()
    if variant == "input-tampered":
        record.with_name("report-render-input.json").write_text("{}")
    if variant == "annotation-tampered":
        record.with_name("report-integrity-lines.txt").write_text("Run Integrity: FAIL\n")
    if variant == "input-mismatch":
        p = record.with_name("report-render-input.json")
        bad = json.loads(p.read_text())
        bad["summaries"][0]["grade"] = "PASS"
        p.write_text(json.dumps(bad))
        data["reporter_input"]["report"]["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()

    if variant == "off":
        data.pop("health")
        record.with_name("health.json").unlink()
    if variant == "bad-digest":
        data["health"]["sha256"] = "0" * 64
    if variant == "missing":
        record.with_name("health.json").unlink()
    if variant == "unsafe":
        data["health"]["path"] = "../health.json"
    record.write_text(json.dumps(data))
    subprocess.run(
        [
            args.python,
            "-c",
            "import sys;sys.path.insert(0,sys.argv[1]);import reports_site;"
            "from pathlib import Path;reports_site.write_published_checksums(Path(sys.argv[2]))",
            str(root / "src"),
            str(record.parent),
        ],
        check=True,
    )
    output = base / variant / "site"
    env = {
        **os.environ,
        "INPUT_RUNS_DIR": str(inputs),
        "INPUT_OUTPUT_DIR": str(output),
        "INPUT_RESET_HISTORY": "true",
        "INPUT_RESTORE_HISTORY": "false",
        "GITHUB_WORKSPACE": str(base),
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_RUN_ID": "peer-proof",
    }
    result = subprocess.run(
        [args.python, str(root / "src/site_action.py")],
        env=env,
        capture_output=True,
        text=True,
    )
    if variant in ("on", "off"):
        assert result.returncode == 0, result.stderr
        outputs[variant] = next(output.glob("*/*/runs/*"))
    else:
        assert result.returncode != 0
        assert not list(output.rglob("index.html"))
    print(variant, result.returncode)
for name in ("report.html", "report.json", "report.md", "report.txt"):
    assert (outputs["on"] / name).read_bytes() == (outputs["off"] / name).read_bytes(), name
assert (outputs["on"] / "health.json").read_bytes() == next(
    source.rglob("health.json")
).read_bytes()
assert "evaluator-livingproof-health" not in (outputs["off"] / "index.html").read_text()
assert original == {
    str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in source.rglob("*")
    if p.is_file()
}
print("Portable outputs unchanged on/off; inputs unchanged; exact Health bytes retained")

assert not list(outputs["on"].glob(".*report*"))
assert not (outputs["on"] / "report-render-input.json").exists()
for variant in ("on", "off"):
    page = (outputs[variant] / "index.html").read_text()
    assert page.count("<p class='stored-status integrity-status'>Run Integrity: PASS</p>") == 2
print(
    "Original input retained before rewrites; raw replay inputs absent from public tree; "
    "both Integrity hero annotations retained"
)
