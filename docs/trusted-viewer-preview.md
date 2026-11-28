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

## Local integration on Action #72

This integration branch is based on #72 at
`6103258229a9b846fae02754eb8c3f378d2189b0`; it does not change that remote branch.
`site/action.yml` accepts a caller-owned `trusted-viewers` registry. The site
entry point validates and strips existing generated embeds before ordinary build,
then reinstalls from caller pins and regenerates only affected run checksums.
An omitted declaration preserves ordinary behavior; an existing embed without
its current declaration is refused. A declared run absent from history is skipped.
The caller registry and vendored Apache-2.0 viewer are under `config/retained/`.
The viewer bytes are generated from the released Health source identified above,
not supplied by an evaluator or downloaded from a report.

The documentation workflow retains its current-protected-main, immutable tag,
and published stable release checks in both production and deploy. It adds two
mutually exclusive, optional inputs selecting reviewed files in released code:

- `retained_source: config/retained/g1-46083905519.json` downloads an exact
  successful main-push Verify artifact, verifies repository/workflow/run/commit,
  artifact ID, expiry and archive digest, then uses the normal site builder.
- `retained_history: config/retained/g1-published-history.json` requires normal
  restored Pages history and refuses reset-history. It verifies the exact old
  published manifest and all bytes, sanitizes retained pages, inserts the viewer,
  and checks the complete resulting tree. This is a published-history migration,
  never represented as a raw Verify artifact. If history is missing or changed,
  it refuses rather than rebuilding or simulating.

Neither path calls the report workflow or dispatches Verify. The normal path,
without either input, still chooses the new release's Verify artifact. Future
normal builds revalidate the caller declaration for the historical embedded run.
The workflow does not waive normal merge or release CI requirements.

The exact source artifact is 15238557586 from Verify 46083905519, source commit
`b09453aeb35c5071368b53fe1ad32810f805b9a2`, API archive digest
`b252d60d4664b7caa059beb0dafa2a7225a5fb42fa14624d3d3695d31654f0f5`.
The authorized archive download returned 403 locally; its bytes are unverified.
The complete public run was independently downloaded and matched published
manifest SHA-256 `dc706f47eb7ebbae263b53f721ea9639c65761bf34e4ebb2fa18b2440f4a8eab`.
Its migration and subsequent restore/reapply passed locally with original report,
raw JSON and evidence bytes unchanged. Hosted restoration availability is still
unverified; a missing retained Pages artifact is a blocker, not permission to reset.

After #72 merges, transplant the two local commits onto its integrated main,
review the workflow delta independently, and release through existing protections.
Only a released version containing this code can use these inputs. No dispatch,
push, deployment, or simulation was performed to prepare this patch.
