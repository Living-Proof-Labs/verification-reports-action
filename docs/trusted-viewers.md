# Trusted viewer consumer integration (local candidate)

Action stages the Health-owned release asset and unchanged report-bound JSON.
Reporter owns exact native panel composition and validation. No new Action HTML
renderer, iframe markup, insertion or removal is used. Reporter follow-up candidate
786dfea7487af645a9b2df036e6694a7083a7ab1 is installed only in the isolated local test
environment. Committed requirements retain the real v3.17.0 release; this candidate
cannot ship until the new Reporter API is reviewed, released and pinned normally.

## Publication and restore

Fresh runs use ordinary site processing, including existing navigation, Pinned
inputs, provenance and evidence policy. After verifying the complete file manifest,
Action stages the caller-pinned viewer plus adjacent JSON and invokes Reporter's
`compose_viewer_page` against the resulting native panel. All other HTML bytes stay
with the existing caller-generated page. Raw reports, grade and JSON are preserved.

Before any build/pruning of restored decorated runs, verify the complete manifest,
exact viewer file set, release asset/data digests and trial binding. Reporter checks
the exact decorated native panel and passes its in-memory undecorated projection
to the existing shell validator. No historical page is written during this check.
All other restored report/evidence checks remain. An unchanged old run survives a
normal successive publication byte-for-byte while the new run gets its own viewer.

No declaration gives fresh runs no player. Already-published executable viewers require the separate `historical-viewers`
caller-reviewed allowlist. `trusted-viewers` controls fresh-run selection only and
cannot authorize history. Each historical entry contains the immutable release pin,
exact evaluator identity and `approved_trials`: explicit `run_id`, `configuration`,
positive integer `index` and `observation_sha256` objects. Every restored executable
must match exactly one approval's evaluator/trial/asset digest and a verified native
decorated panel. An unrelated approved asset or clean plain shell is insufficient.
Disabling fresh selection preserves approved history; removing historical trust is
not permission to admit executable bytes blindly. No old unadorned page is backfilled.

## Expiry

Ordinary retention prunes copied MCAP/video/media and updates evidence links. The
recorded JSON and its adjacent unchanged copy remain, so replay remains usable.
For a previously decorated run affected by that ordinary retention update, Reporter
recomposes its exact native panel after the existing expiry policy updates the shell.
This is the explicit normal expiry transition, not a general historical rerender or
migration. Nonexpired restored pages remain byte-identical.

A missing/modified JSON payload, mismatched trial, extra executable asset, altered
viewer or untrusted iframe fails BEFORE normal publication/pruning. Even a rewritten
complete manifest cannot authorize a missing recorded payload. No broken interactive
card is silently published and no checksum or trial check is skipped.

## Evidence and boundaries

Installed candidate integration tests use the existing genuine 574-sample G1
recording and an explicit valid local report fixture. Two successive publication
IDs and later expiry date are test wrappers around that same retained trial; they
are not new simulator runs. Tests prove byte-identical first-run restore; successful
next publication; shell pins/provenance/navigation; unchanged raw data and FAIL;
normal media expiry with working JSON; missing payload refusal without new output;
extra executable refusal; no-declaration fresh report. No deployment is claimed.

The separate provenance fix is Action #81, head
a3890bde5ea8ce1f7dfa87eaa4196a985d865312. Do not amend that review from this branch.
Reporter #139 remains separate and untouched. #75's site/index/history refactor is
not a dependency. Release compatibility for old renderer fragments must remain
pinned; unknown fragments fail rather than being rewritten. Runtime package pins
must be resolved from real immutable releases before pushing this Action revision.
