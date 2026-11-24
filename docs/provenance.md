<!-- SPDX-License-Identifier: Apache-2.0 -->

# Pinned inputs and provenance

The plan job in [`.github/workflows/report.yml`](../.github/workflows/report.yml) resolves every input from its release tag before a trial starts. `walk-hallway` and `scenario` need that job. When a check fails, the plan step exits and those jobs do not run. Collect requires a successful plan whose provenance statuses are `verified`, `not-published`, `resolved`, or `fixture`, and it does not write a report otherwise.

## Inputs

| Input | Lock field | What is checked |
| --- | --- | --- |
| capability-verifier | `releases.verifier` | Release, tag commit, workflow pin, image digest, attestation |
| livingproof-sim-mujoco | `releases.runner` | Release, tag commit, source archive sha256, attestation |
| robot-capability-specs | `releases.specs` | Release, tag commit, each `files` sha256, attestation |
| livingproof-worlds | `world` | Release, tag commit, `worlds/<id>/<version>/world.json` sha256, attestation |
| livingproof-evaluator-scenario | `evaluators.livingproof-evaluator-scenario` | Release, tag commit, source archive sha256, attestation |
| livingproof-evaluator-run | `evaluators.livingproof-evaluator-run` | Release, tag commit, source archive sha256, attestation |
| livingproof-capability-report | `releases.reporter` | Release, tag commit, image digest, attestation |

The tag is read from the definition when the definition has one, and otherwise from the lock. The image name defaults to `ghcr.io/living-proof-labs/capability-verifier-hallway` and `ghcr.io/living-proof-labs/livingproof-capability-report` when the lock does not name one. Only `ghcr.io` images are accepted.

## Checks

For each input the plan asks GitHub for the Release named by the tag, peels the git tag to a commit, and then compares:

- **Release.** A missing Release fails. The message names the input, the tag, and that no release was found.
- **Commit.** When an attestation is published, its `gitCommit` must equal the peeled tag. A difference fails as an attestation commit mismatch and no report is written. Otherwise the expected commit is `target_commitish` when that field is a full commit, or the last full commit SHA in the release notes. A difference fails as a moved tag. When none of those sources name a commit, the peeled tag is recorded and the pin's status is `resolved`.
- **sha256 or digest.** File pins use the `SHA256SUMS` asset on the Release when it lists the path. Image pins use the `image:version@sha256:…` line in the release notes when it names that image, compared with the registry manifest digest. Source pins use a `source.tar.gz` or `SOURCE` line in `SHA256SUMS` when present, compared with the sha256 of the tag archive. A published value that differs from the bytes fails. The message names the input, the path or image, and the expected and found values.
- **Attestation.** The plan reads `GET /repos/{owner}/{repo}/attestations/{digest}`. HTTP 404 means no attestation was published. HTTP 200 means an attestation exists: its subject digest, source repository, and commit must all match, or the run fails. The failure names which of the three disagreed and the expected and found values. The plan does not record a mismatched attestation. Any HTTP status other than 200 or 404 fails the run. The plan does not add a sigstore verifier. It trusts the attestations the GitHub API returns for that subject.

`contents: read` is the workflow permission. The plan does not ask for `attestations: read`, because every caller of this reusable workflow would have to grant that permission before the plan job could start.

The capability-verifier `uses:` lines stay a full commit SHA. GitHub rejects an expression there, so the plan cannot swap the called workflow. After the tag peels, that commit must be `0c00c388c693012392da0c1cd391046c752e9b3c` (`v2.11.4`). Any other commit fails before a trial. Renovate's GitHub Actions manager bumps that one line, SHA and `# vX.Y.Z` comment together. The lock's verifier tag must peel to the same commit. No lock hash is rewritten.

## Tag bump

For now, bumping a tag also means updating that input's commit, digest, and file sha256 in the lock to the values the tag resolves to. A tag-only edit fails before `plan.json` is written. The tag-only Renovate bump is a follow-up: `capability-verifier` `scenario.yml` will take the resolved commits and digests as inputs, and `assertLockAgrees` in `src/provenance.mjs` can then be removed.

Fixture definitions under `tests/fixtures/robot/verifications/`, when this repository calls its own workflow, skip the network and record `status: fixture`. They are not a robot release. A robot repository's definitions are checked.

## Status

Each input's `status` is one of:

| Status | Meaning |
| --- | --- |
| `verified` | The attestation matched the subject digest, the source repository, and the commit. |
| `not-published` | HTTP 404. The commit was checked against the release. |
| `resolved` | HTTP 404, and the release named no commit. The peeled tag is recorded. |
| `fixture` | This repository's own fixture. The release was not checked. |

## Records

`report.json` and `run.json` both gain `inputs`. Each entry has `name`, `repository`, `tag`, `commit`, `digest`, `status`, and `attestation`. `report.md`, `report.html`, and `report.txt` include a Pinned inputs section. Each line is `name: status (meaning)`, so a verified pin reads differently from one that was not. A failed check does not produce that file.

`status: resolved` can appear on the same input as `attestation: not-published`. The attestation field records the HTTP 404. `resolved` means the release named no commit, so the peeled tag is what was recorded. `status: not-published` with that same attestation field means the 404 happened and the commit was checked against the release.

A missing Release and a tag that does not peel are failures. They are not stored as `not-published`.

Source pins hash the codeload archive of the peeled commit. A pin with more than one file hashes a synthetic digest of those file hashes. Those subjects are not what a published attestation names, so the lookup returns HTTP 404 and the pin stays `not-published` until a later change uses a subject the release actually publishes. Image digests and a single file's sha256 can match.

The plan step sets `GITHUB_TOKEN` from `github.token`. `contents: read` covers the release, tag, and attestation reads. That token is sent only to `api.github.com`. The codeload archive and the registry token request do not receive it.
