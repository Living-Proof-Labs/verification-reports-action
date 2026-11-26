# Action #51 replay staging — not a production dependency pin

This local branch stages the Action adapter for Reporter PR #132, exact head
`9807f514b8c7f4bed8acadce1da1f862b8eb6316`. No production dependency or workflow pin
is changed, and this branch is not ready for a normally tested Action PR.

Installed candidate wheel: `livingproof_capability_report-3.15.5.dev17-py3-none-any.whl`,
SHA-256 `b613fdc53cbb25511ed4cd165c52bc9f4e7c4910b597192554124f6b862fd86b`.

## Genuine input and original annotations

Current live-index baseline at investigation: Verify 45760910690, trial
`walk-hallway-unitree-g1-stock-20261126T052209Z`, stock trial 1.

- G1 artifact 15089667971: ZIP SHA-256
  `dd0f42c25b3ed235ccda95b311edbebc94717fdf240b723058e46fefb4aba71e`.
- Original Reporter artifact 15090455237: ZIP SHA-256
  `a14d7d6a106d4ef52da5af419d43ae43d712c166eaa7a6ce7b8bc062fa978e8c`.
- Health SHA-256
  `ef54f3fddeecf66c3678264fd3b5115533a06be2539f2c9648ff3a6742e97038`.

`assemble.mjs` now retains the exact original Reporter JSON and
`integrity-lines.txt` before `stampReportInputs` normalizes numeric spellings.
Both are bound in `run.json` by fixed paths and SHA-256. They remain in the input
archive; private build copies are deleted before the site is published. Original
portable files are not replaced by the newly rendered index.

The missing hero line was a producer annotation, not Reporter API behavior.
Capability Verifier v2.12.5, commit
`e6a1ba347c525b62abf0644b610562b7bfb390d9`, implements it in
`src/capability_verifier/collect.py::place_integrity_lines`. The staged adapter
replays that escaped annotation after verifying it matches original portable
HTML. It does not infer a new Integrity verdict. Hash-bound observation duration
is supplied through the existing render API. Health alone uses the new typed API.

## Reproduce

Extract the two verified archives into separate directories and install the exact
candidate wheel plus its locked dependencies in an isolated environment. Run:

```sh
python staging/prove_replay.py \
  --g1-input /path/to/extracted-g1 \
  --reporter-input /path/to/extracted-original-reporter \
  --candidate-python /path/to/candidate-env/bin/python \
  --output /path/to/new-proof-directory
python staging/prove_replay_browser.py \
  --replay /path/to/new-proof-directory \
  --baseline-page /path/to/pre-change-generated-run/index.html \
  --output /path/to/screenshots --browser /path/to/chromium
```

The browser command needs Playwright. The proof invokes the actual `src/site_action.py`
entry point used by `./site`; negative variants change temporary copies only.
No new simulation is run. Every output directory must be fresh.

Observed results: on/off pass; bad Health digest, missing/unsafe sidecar,
missing/tampered original input, tampered annotation, and a rehashed original
input with a mismatched grade all refuse without an index. Portable outputs match
on/off; original input files and Health bytes remain unchanged. At 390, 430 and
1280 px with JS on/off, three peers fit without horizontal scrolling. Keyboard
activation works. Hero text, Scenario/Integrity detail-panel HTML and evidence
links match on/off; the recorded duration remains present. Both Integrity hero
annotations are retained.

## Immutable-release integration gate

Wait for independent Reporter review, the separate co-install repair and a verified
immutable release. Then replace this prototype import with an isolated install of
that release, pin its verified source/wheel provenance through the existing
conventions, and move these probes into normal CI using retained genuine fixtures.
Do not pin this unpublished candidate on main.

## Explicit archive compatibility

- Fresh Health-on archives **without** `reporter_input` use the prior validated
  advisory section. They do not fabricate a peer or reconstruct a source digest.
  Health-off archives keep the unchanged path. The Reporter candidate is imported
  lazily, only for a fully bound new replay.
- A **present** replay descriptor is a commitment: unknown/malformed descriptors,
  missing files, bad digests or mismatched original data fail. No silent fallback.
- Restored published legacy sections and newer peer pages remain byte-identical
  when still valid. Restore checks the Health sidecar and publication policy,
  without requiring private replay inputs that are deliberately not published.
  A visible legacy section or peer with a missing sidecar is refused; tampering
  still fails the digest check.
- Existing retention removes expired Health data and regenerates the page from
  the portable report without a Health result. Neither private inputs nor a
  guessed replacement peer are needed. An unavailable or damaged original page
  is not a license to reconstruct unverifiable results.

Normal verification: `task verify` passed 223 Node and 499 Python tests. The 28
focused tests in `tests/test_health_display.py` and `tests/test_health_history.py`
cover legacy ingestion, restore, tampering and expiry. Restore tests call the
actual site Action entry point and actual Pages ZIP extraction; only GitHub
transport responses are fixtures, not a live hosted restore.

The genuine current legacy input from run 45760910690 rebuilt through
`src/site_action.py`: all 19 run files matched the pre-staging baseline byte-for-byte.
The existing staged genuine peer output also passed actual restore and expiry:

```sh
uv run python staging/prove_history.py \
  --published-site /path/to/previous-peer-proof/on/site \
  --output /path/to/new-history-proof
```

The immutable Reporter release/install and normal production dependency/CI
integration remain gates before an Action PR. No GitHub mutation or new
production pin is part of this compatibility staging turn.

## Pinned inputs parity

The genuine replay probe exposed a missing producer-owned Pinned inputs section.
The adapter now retains that exact section from the original portable HTML, using
`stampReportInputs`' existing before-body placement. It does not rebuild provenance
from guessed inputs; the normal final HTML publication policy still applies.
Ambiguous sections or insertion locations fail. The portable files remain unchanged.

The browser probe compares both section outerHTML and every link text/href against
Health-off **and** the pre-change generated page, alongside existing Integrity hero,
Scenario/Integrity detail, evidence-link and duration parity checks. This runs at
390, 430 and 1280 CSS pixels with JavaScript enabled and disabled.

Latest local proof: `/tmp/health-replay-pinned` (nine CLI variants),
`/tmp/health-pinned-browser` (six browser combinations), and
`/tmp/health-pinned-history` (restore and expiry). Pre-change comparison input:
`artifacts/live-baseline/unitree-g1/stock-mujoco-hallway/runs/45760910690/index.html`.
