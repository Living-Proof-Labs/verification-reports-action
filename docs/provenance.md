<!-- SPDX-License-Identifier: Apache-2.0 -->

# Pinned inputs and provenance

The plan job in [`.github/workflows/report.yml`](../.github/workflows/report.yml) resolves every input from its release tag before a trial starts. `walk-hallway` and `scenario` need that job. When a check fails, the plan step exits and those jobs do not run. Collect requires a successful plan whose provenance results are `passed`, and it does not write a report otherwise.

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
- **Commit.** The expected commit is the attestation's `gitCommit` when an attestation is published, otherwise `target_commitish` when that field is a full commit, otherwise the last full commit SHA in the release notes. The found commit is the peeled tag. A difference fails as a moved tag. When none of those sources name a commit, the peeled tag is recorded and the commit check is `resolved`.
- **sha256 or digest.** File pins use the `SHA256SUMS` asset on the Release when it lists the path. Image pins use the `image:version@sha256:…` line in the release notes when it names that image, compared with the registry manifest digest. Source pins use a `source.tar.gz` or `SOURCE` line in `SHA256SUMS` when present, compared with the sha256 of the tag archive. A published value that differs from the bytes fails. The message names the input, the path or image, and the expected and found values. When nothing is published, the computed digest is recorded and the digest check is `resolved`.
- **Attestation.** The plan reads `GET /repos/{owner}/{repo}/attestations/{digest}`. HTTP 404 is `not-published` and does not fail. A published statement must name the same subject digest and, when it names a source repository, that repository. The plan does not add a sigstore verifier. It trusts the attestations the GitHub API returns for that subject.

`contents: read` is the workflow permission. The plan does not ask for `attestations: read`, because every caller of this reusable workflow would have to grant that permission before the plan job could start. A response other than 200 or 404 fails the input closed.

The capability-verifier `uses:` lines stay a full commit SHA. GitHub rejects an expression there, so the plan cannot swap the called workflow. After the tag peels, that commit must be `0c00c388c693012392da0c1cd391046c752e9b3c` (`v2.11.4`). Any other commit fails before a trial. Renovate's GitHub Actions manager bumps that one line, SHA and `# vX.Y.Z` comment together. The lock's verifier tag must peel to the same commit. No lock hash is rewritten.

## Tag bump

The lock's commit, file sha256, and image digest are not expected values. Changing one `tag` is enough. The definition sha256 in the lock still has to match the definition file, so the tag Renovate edits is the lock's `tag`, not a copy of that tag inside the definition. This repository does not run a lock generator after the bump.

Fixture definitions under `tests/fixtures/robot/verifications/`, when this repository calls its own workflow, skip the network and record `result: fixture`. They are not a robot release. A robot repository's definitions are checked.

## Records

`report.json` and `run.json` both gain `inputs`. Each entry has `name`, `repository`, `tag`, `commit`, `digest`, `result`, `attestation`, and `checks`. `result` is `passed` when the plan finished, or `fixture` for the fixture robot. A failed check does not produce that file.
