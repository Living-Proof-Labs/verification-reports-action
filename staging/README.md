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

Before a production Action PR, finish backward-compatible restored-history handling
and retention behavior for the new original-input descriptor. Old Health-off runs
must keep their existing path; old Health-on archives lack these newly retained
inputs and require an explicit migration/compatibility decision, never guessed
reconstruction or a source-digest rewrite. This staging proof covers fresh ingestion
and replay, not that release/migration lifecycle.
