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
