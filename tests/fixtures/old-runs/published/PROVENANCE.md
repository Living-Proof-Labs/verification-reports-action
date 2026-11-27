# Published run folders from main `9a3712a`

These two run folders were published by the site code at main
`9a3712a7e7396782919ed9975ed7597e7aac4d32` (v3.8.0), then copied here.
The source archives stay in `tests/fixtures/old-runs/health-production/` and
`tests/fixtures/old-runs/health-peer/`.

`production/` is the legacy run. Its `run.json` has `health` and no
`reporter_input`. The page section is `health-advisory`.

`peer/` is the peer replay. Its `run.json` has `health` and `reporter_input`.
The page section is `evaluator-livingproof-health`.

```sh
# production, from tests/fixtures/old-runs/health-production/g1-report.zip
uv run python -c "import os, zipfile; from pathlib import Path; import site_action; ..."
# peer, from tests/fixtures/old-runs/health-peer/
uv run python tools/prove_health_replay.py \
  --g1-input <unpacked g1-report.zip> \
  --reporter-input <unpacked reporter-original.zip> \
  --python .venv/bin/python \
  --output <peer-output>
```

The production command unpacked `g1-report.zip` and ran `src/site_action.py`
with `INPUT_RESET_HISTORY=true`. The peer command is `tools/prove_health_replay.py`
as above; the retained folder is that command's `on` publication.
No simulation was run. Main SHA: `9a3712a7e7396782919ed9975ed7597e7aac4d32`.
