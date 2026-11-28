# SPDX-License-Identifier: Apache-2.0
"""Development-only retained-report preview. No network, simulation or publication."""

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from trusted_viewer import binding, install, revalidate  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--caller-pin", type=Path, required=True)
    parser.add_argument("--viewer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pin = json.loads(args.caller_pin.read_text())
    uri, _, _ = binding(args.run_dir, pin)
    args.output.mkdir()
    for name in ("report.json", "index.html", uri):
        shutil.copyfile(args.run_dir / name, args.output / name)
    install(args.output, pin, args.viewer.read_bytes())
    revalidate(args.output, pin)
    print(f"Retained-only preview: {args.output}/index.html; restore validation passed")


if __name__ == "__main__":
    main()
