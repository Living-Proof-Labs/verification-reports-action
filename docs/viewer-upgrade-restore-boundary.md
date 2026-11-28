# Remaining compatible-upgrade restore boundary

Assessment at source candidate 81435b882c38f43d3b23527e26caa42b883d4e02.
No runtime trust change is made by this note or its regression.

Observed cycle, using the existing genuine recording and synthetic compatible
producer-output fixtures (not a claimed v1.2.1 release): first publication succeeds;
one evaluators.yml bump exposes the compatible improved card; third publication
refuses restoring the second run because the independent historical approval still
names the prior evaluator version. Both old run trees remain byte-identical and the
third run is not published. Focused tests reproduce both the successful fresh bump
and this fail-closed boundary.

## Existing authority

- `reports_restore._fetched_run_is_pages_deploy` checks repository identities,
  branch and allowed event; an exact workflow path is checked only if supplied.
- `deployed_pages_artifact` also requires a successful Pages deployment job.
- `restore` downloads the selected GitHub artifact, safely extracts reports and
  returns artifact metadata. This is stronger than accepting a URL from report data.
- However `restore` does not compare downloaded bytes to the GitHub API artifact
  digest, and `site_action` discards its metadata return. A locally supplied/preexisting
  reports directory can reach the same viewer validation path without any such
  authoritative publication context.
- The complete per-run SHA256SUMS check detects byte changes against its manifest;
  it does not authenticate who approved that manifest. Neither run.json nor
  advisory_provenance can independently grant an executable/history approval.

Therefore an automatic version/lineage approval derived from only the restored
files would remove the boundary specifically added by independent review. A current
fresh evaluator pin also cannot authorize all previous versions or prove where an
old report was published. We retain exact historical approval and fail closed.

## Smallest safe next trust decision (not implemented)

Use the existing authenticated GitHub publication channel, not a new permission or
signing system, but make its authority explicit: independently approve the exact
publication workflow/released validator policy; verify repository/run/deployment,
resolved policy revision and artifact ID/archive digest using GitHub API facts;
carry the resulting in-memory verified extraction context to viewer validation.
Only bytes from that authenticated publication could inherit the fact that the
approved publisher validated their evaluator declaration and trial binding. Local
folders, live Pages fetches and self-written receipts cannot mint that context.
Full file manifests, current independent executable digest/identity restrictions,
exact native panel, recording digest and trial checks would remain mandatory.
The new archive proof must be verified again on each restoration; never treat a
receipt read from the archive as authority by itself.

This needs a separately reviewed publication-policy trust rule and consumer tests;
it is not present in the current interfaces, nor a Reporter #141 concern. It can be
a one-time consumer change rather than manual per-evaluator approvals, but cannot
honestly be called a zero-trust-contract-change fix. No activation is safe yet.
Until that choice is implemented, the concrete tradeoff is explicit historical
approvals per newly published evaluator/trial versus autonomous authenticated
publication inheritance. We do not substitute a wildcard, trust result-provided
lineage, invent an executable pin, or silently drop history.
