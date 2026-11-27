# Published run folders from main `9a3712a`

These two run folders were published by the site code at main
`9a3712a7e7396782919ed9975ed7597e7aac4d32` (v3.8.0), then copied here.
They are local publications of the archived run zips, not a fetch of a
deployed site. The source archives stay in
`tests/fixtures/old-runs/health-production/` and
`tests/fixtures/old-runs/health-peer/`. At that commit those directories
were `tests/fixtures/health-production/` and `tests/fixtures/health-peer/`.

`production/` is the legacy run. Its `run.json` has `health` and no
`reporter_input`. The page section is `health-advisory`.

`peer/` is the peer replay. Its `run.json` has `health` and `reporter_input`.
The page section is `evaluator-livingproof-health`. The retained folder is
the `on` publication.

No simulation was run. Main SHA: `9a3712a7e7396782919ed9975ed7597e7aac4d32`.

Production, from a checkout of that commit:

```sh
python3 - << 'PY'
import os, sys, zipfile
from pathlib import Path
root = Path("/agent/repos/verification-reports-action")
sys.path[:0] = [str(root / "src"), str(root / "tools")]
out = Path("/tmp/old-runs-build/production")
out.mkdir(parents=True)
archive = root / "tests/fixtures/health-production/g1-report.zip"
with zipfile.ZipFile(archive) as zipped:
    zipped.extractall(out / "input")
env = {
    **os.environ,
    "GITHUB_WORKSPACE": str(out),
    "GITHUB_EVENT_NAME": "pull_request",
    "GITHUB_RUN_ID": "legacy-publish",
    "INPUT_RUNS_DIR": str(out / "input"),
    "INPUT_OUTPUT_DIR": str(out / "site"),
    "INPUT_RESET_HISTORY": "true",
    "INPUT_RESTORE_HISTORY": "false",
    "GITHUB_OUTPUT": str(out / "outputs"),
}
import site_action
site_action.run_site_action(env)
PY
```

The published run is `/tmp/old-runs-build/production/site/unitree-g1/stock-mujoco-hallway/runs/45754832566`.

Peer, from the same checkout. Unpack both zips, then run the replay tool
that existed at that commit (`tools/prove_health_replay.py`). This tree
deletes that file; check out `9a3712a` to run it.

```sh
python3 - << 'PY'
import zipfile
from pathlib import Path
root = Path("/agent/repos/verification-reports-action/tests/fixtures/health-peer")
dest = Path("/tmp/old-runs-build/peer-src")
dest.mkdir(parents=True, exist_ok=True)
for name in ("g1-report", "reporter-original"):
    with zipfile.ZipFile(root / f"{name}.zip") as zipped:
        zipped.extractall(dest / name)
PY
.venv/bin/python tools/prove_health_replay.py \
  --g1-input /tmp/old-runs-build/peer-src/g1-report \
  --reporter-input /tmp/old-runs-build/peer-src/reporter-original \
  --python /agent/repos/verification-reports-action/.venv/bin/python \
  --output /tmp/old-runs-build/peer
```

The published run is
`/tmp/old-runs-build/peer/on/site/unitree-g1/stock-mujoco-hallway/runs/45760910690`.
