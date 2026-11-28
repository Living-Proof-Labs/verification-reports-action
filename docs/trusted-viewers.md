# Caller-pinned replay inputs (local integration candidate)

Health owns the reviewed immutable player. Reporter owns the entire report page
and embedded wrapper. The Action fetches the caller-pinned release, stages its
bytes beside an unchanged recording, and invokes the installed Reporter CLI.
No evaluator chooses executable code or URLs. This change contains no player,
iframe renderer, HTML insertion or HTML removal.

`./site` accepts `trusted-viewers`, a caller-owned registry with exact evaluator
id/version/publisher/kind, recording format, and viewer repository/tag/commit/asset/
SHA-256. `config/viewers/evaluators.json` pins Health v1.4.0. Empty input leaves the
ordinary path unchanged. Fresh produced runs alone are selected; ambiguous identity,
changed manifest, wrong bytes or invalid Reporter trial binding fail before installing
a player. Temporary page output is directly inside its verified asset root, matching
Reporter #139. The original portable reports and recording are not rewritten by the
viewer adapter. Existing site publication still has its own pre-existing transformations.

## Not release-ready

Reporter #139 must release before updating `site/requirements.in` and the compiled
hash lock. Those files intentionally retain the real v3.17.0 pin in this local
candidate. No temporary/candidate dependency pin is committed. With that old runtime,
explicit viewer use fails closed because the new CLI option does not exist.

Restored decorated pages currently fail closed before build. Reporter-owned validation
for restoration is still required; this candidate does not strip or regenerate historical
HTML or relax incoming executable-content rejection. Default undecorated history remains
on the ordinary path. Do not merge this intermediate candidate.

The native Reporter page also needs parity review for existing Action-owned additions
(Pinned inputs, site navigation and provenance). Those cannot be copied by Action HTML
manipulation. This local adapter deliberately does not reproduce them.

The current public run 46110994356 report has a source-bundle digest mismatch and is
correctly refused by Reporter. The raw main G1 artifact ZIP download returned Forbidden;
its earlier generation-stage provenance has not been independently verified. Do not
silently recalculate the digest. Local on/off proof uses the previously prepared explicit
fixture report with genuine retained recording, not a claim about current hosted input.

## Normal generation provenance proof

The stale digest has a reproducible normal-path cause: `stampReportInputs` parsed
and reserialized the full Python-generated report in JavaScript. Values such as
`27.0`, `0.0` and `1.0` inside `source_bundle` became integer spellings; Reporter's
canonical digest distinguishes them. The local revision now replaces only the
top-level `inputs` JSON field, retaining every nested field's raw JSON bytes.
This neither updates nor forgives an existing source digest.

Proof used the installed Reporter candidate on its original G1 corner manifest,
then the ordinary `assembleVerifierDefinition` path with artifact checksums. The
new report renders successfully after assembly; source digest remains
`ab31a6d1a95b163902a625616a322ac694944194e81ed5c435ac5a1cee21a2f7`, and decisions
and summaries equal the freshly generated input. This is a recorded G1 fixture
and a local artifact-shaped test, not a newly hosted simulation or reconstruction
of a historical published report. Regression tests also cover escaped keys,
nested punctuation, repeated stamping and integers beyond JavaScript precision.

## Exact Reporter features still needed

1. Compose the caller's approved existing report page with a bound viewer while
   preserving its navigation, Pinned inputs and provenance additions, OR provide
   typed inputs for those existing additions in native full-page rendering.
   Current `render` only has the run-history link slot, not those three additions.
   Action cannot replace `index.html` with native output without losing them, nor
   copy the additions into the new output without prohibited HTML manipulation.
2. Validate an already published viewer-bearing page and its pinned assets/binding
   without rewriting the page. Action can verify the complete file manifest and
   pass data/pins; Reporter must own admission of its generated wrapper. Current
   `render` is not a validation interface. Existing `recheck_restored_run` rejects
   iframe content; simply skipping it or allowing arbitrary iframe markup would
   weaken the existing trust boundary. The local fail-closed guard remains until
   that narrow validation interface exists. Therefore this candidate still cannot
   promise a second production publication after enabling viewers.

These are concrete composition/validation seams on the existing renderer, not a
request for the unrelated #75 site/index/history command or a history migration.
