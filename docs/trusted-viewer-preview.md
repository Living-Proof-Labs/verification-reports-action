# Retained report embed — local implementation handoff

This branch adds an opt-in implementation, not a production policy relaxation.
`src/trusted_viewer.py` sanitizes incoming HTML using the existing strict policy,
then generates exactly one iframe from a caller pin. Evaluator input remains data.
The selected report checksum, advisory data checksum, result/run/system identity
and advisory trial-to-observation binding must all match. The raw data anchor stays.
Only a caller-supplied static viewer matching its expected SHA-256 is installed.
The source tag and commit are recorded caller assertions; asset acquisition must
verify them independently. No source download is hidden in the installer.

The iframe uses scripts and same-origin access because the pinned Health loader
fetches adjacent same-origin data. This is trusted code, not a sandbox for hostile
scripts; pin review is essential. Top navigation, forms and popups are not granted.
Do not broaden the incoming HTML allowlist to accept iframes.

`revalidate(folder, current_caller_pin)` is the restore gate. It checks the pinned
viewer, copied JSON, exact generated frame and sanitized page, refuses extra files
and symlinks, then returns the ordinary report HTML for existing validation. A
manifest restored with a page must never be used as its own trust authority.

## Reproduce without a simulation or network call

Prepare a retained directory with the original `index.html`, `report.json`, and
cited JSON, and independently reviewed viewer bytes. The caller pin is separate
from evaluator input. Its fields are exercised in `tests/test_trusted_viewer.py`.

```sh
python tools/retained_viewer_preview.py --run-dir RETAINED_RUN \
  --caller-pin REVIEWED_PIN.json --viewer VERIFIED_VIEWER.html --output NEW_PREVIEW
python3 -m http.server 8000 --bind 127.0.0.1 --directory NEW_PREVIEW
```

The helper copies only the selected report and data; no MCAP decoding, simulation,
report regrading, release or deployment occurs. Both fresh insertion and restored
validation execute. It is a development tool, not a standalone product CLI.

Tested retained input: public G1 run 46083905519, stock trial 1,
`walk-hallway-unitree-g1-stock-20261127T234321Z`. Its details SHA-256 is
`9e6e7bfe280966e37072c938f4d30ac4f09782bca2dec676900b3db41169f2ec`.
Trusted viewer: generated from the reviewed Health v1.2.0 source commit
`1c21eeb5d3aafdf00e8aa13c55e544a56fe5ab14`, viewer SHA-256
`9c08b355bd6a90f80a79f1d4369282050473db5ae092ae7ddb41ecf46f490995`.
This is a retained historical example, not a promise that that URL persists.

## Production wiring reserved for coordination with #72

No existing Action inputs, restoration code or workflow are changed here.
After #72, wire a reviewed caller registry into `site/action.yml` and
`src/site_action.py`. Before ordinary restored-run validation, call `revalidate`
with that registry's pin; sanitize the returned source, never the iframe itself.
Install after normal incoming-report sanitation. Regenerate `SHA256SUMS` with
`write_published_checksums`; ordinary file-set checking must still run. No evaluator
may choose the executable viewer. Test publish/restore twice and modified pins.

The standard docs workflow requires a successful main-push Verify at the release
commit and obtains `g1-verification-report` from that run. Verify invokes real
`g1-smoke`. Re-dispatching the current workflow cannot select old G1 input for a
new release. The smallest publishing change is an explicit **retained source**
input (source workflow run + artifact ID + expected artifact digest + report run),
independent of the new site-code release identity. Validate the old source's repo,
completed success, artifact digest and selected report identity; do not pretend
its head is the new release. Use the existing Pages upload/deploy path after build,
with history restore enabled. This belongs with #72's `docs.yml` wiring, not an
unreviewed dispatch or a new simulation. Required integration tests: wrong source
repo/run/digest refusal; retained source and new release provenance kept distinct;
no invocation of report.yml/g1-smoke; existing grade/JSON unchanged; second restore
revalidates the exact caller pin. Those publishing changes/tests are **not yet
implemented** by this local-only patch.
