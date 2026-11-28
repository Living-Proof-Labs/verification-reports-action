# Compatible upgrades and authenticated Pages restoration

The source-review implementation adds an optional `publication-approval` caller
input. It is inactive by default; no workflow, permission, dependency pin, release,
or deployment is changed. Existing explicit `historical-viewers` approvals remain
supported when this new path is not selected.

## Authority and scope

The caller supplies a JSON policy outside the downloaded archive with these fields:

- `repository`, `branch`, `event`, `workflow`, `reusable_workflow`, `job`, and `artifact`:
  the independently approved production publisher identities.
- `source_sha256`: exact SHA-256 fingerprints of the entry and reusable workflow
  files. Review these files' protected-main/stable-release gates and their fixed
  production deploy job before approving them. A workflow change needs review;
  an evaluator declaration version bump does not change these fingerprints.
- `viewer_registry`: caller-owned registry in the same format as
  `config/viewers/evaluators.json`, independent of fresh evaluator selection.
  This authorizes stable evaluator identity/repository/descriptor and executable
  release bytes, never an executable URL supplied by a result or restored file.

The production job already has `actions: read` and `contents: read`. The verifier
uses repository, artifact, run, job and immutable contents API endpoints plus the
job-log endpoint. It does **not** require the REST deployments API or a new token
scope. Deployment success is established by the successful fixed deploy job and
its authenticated log under the caller-approved exact workflow source, not by a
free-form success substring or job name alone.

Only the currently reviewed `actions/deploy-pages` v4.0.5 commit
`d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e` is accepted. Its four-step job layout
must match exactly, including the protected-main release gate. The verifier
requires one timestamped deploy marker, one exact structured masked payload,
matching artifact ID and source commit, deployment creation, and subsequent
success. Logs are fetched from the verified job endpoint, limited to 1 MiB, over
HTTPS; local workspace logs and archived receipts are never inputs. Format changes,
expired logs, unavailable authenticated endpoints, and missing proof fail closed
for decorated history.

The downloaded ZIP must match GitHub's artifact digest. History is safely extracted
into private staging and does not replace the destination until the publication
proof and every decorated run have passed. A nonempty destination is refused.
Each decorated run still requires its complete file manifest, independently pinned
executable, stable repository and descriptor provenance, one exact recorded trial,
data digest, report integrity, and exact native Reporter panel. Only then are exact
version/trial approvals constructed in memory. They are neither saved nor accepted
from history as credentials. Undecorated history retains its existing restore path
and does not require the new deploy-log proof.

## Observed production evidence (2026-11-28)

For release run [46119592031](https://github.com/Living-Proof-Labs/verification-reports-action/actions/runs/46119592031),
the authenticated [deploy job 140901211911](https://github.com/Living-Proof-Labs/verification-reports-action/actions/runs/46119592031/job/140901211911)
finished successfully at source `2e74cced83295995d51ba788a959b2ec0f2945d2`.
Its exact structured log payload names artifact `15253230775` and that commit,
followed by deployment creation and success. The new strict grammar was exercised
against this independently fetched real log. Artifact-detail metadata equals the
run artifact listing. GitHub's ZIP SHA-256 is
`a017a5d2830961136de99ee5e69701c3bade088728eaee5c6107ec8a83b2d6e8`;
the coordinator independently downloaded and matched those bytes through the
GitHub connector. This worker did not repeat that download.

Authenticated source fingerprints at that exact commit:

- `.github/workflows/release.yml`:
  `cbbff6e61ead6bd09701b50a573f3af2f60006fb7a9737f912c06c23030aa145`
- `.github/workflows/docs.yml`:
  `03ab8c7ede263c71f169ae52055256ffca72155881666d06542ff2cbed7d5bae`

These document existing production evidence; they are **not activated policy** or
pins for a future changed workflow. Activation must approve the actual integrated
publisher source and the real released Reporter dependency, independently of this
source-only review.

## Local proof and remaining boundary

Tests run the actual `run_site_action` path, real ZIP/TAR restore, installed Reporter
candidate, and retained real G1 recording. Mocked authenticated GitHub records model
successive production archives. One synthetic compatible evaluator bump (1.2.0 to
1.2.1, not a claimed upstream release) changes only the caller evaluator declaration
and corresponding producer output. The third publication restores both earlier
runs byte-for-byte without another historical approval. Additional cases cover
fresh selection off, media expiry retaining valid JSON/player/fallback, legacy
undecorated history, and missing/wrong publication, source, executable, provenance,
manifest and evidence checks. Grade remains FAIL and raw recording bytes remain
unchanged.

The unchanged production workflow has not run this new restoration implementation.
No three-publication hosted proof, production token exercise of this new path, new
release pin, deployment or activation is claimed. Independent source review and
normal release/production verification remain required.

### Production layout correction

`discover_viewers` is shared by restore detection, approval generation and history
validation. It accepts only `<robot>/<scenario>/runs/<id>/trusted-viewer` and the
production `g1/<robot>/<scenario>/runs/<id>/trusted-viewer` subtree; any other named
viewer path refuses restoration. The authenticated three-publication tests now
package the actual `reports/g1/...` hierarchy, including the refusal cases. Their
caller adapter restores `reports/` and then builds `reports/g1/` in one process.
Production currently performs these in separate processes: activation must retain
the authority in memory across that boundary, not serialize an approval receipt.
This correction does not activate or change that workflow.
