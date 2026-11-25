<!-- SPDX-License-Identifier: Apache-2.0 -->

# Pinned inputs and provenance

The plan job in [`.github/workflows/report.yml`](../.github/workflows/report.yml) resolves every input from its release tag before a trial starts. `walk-hallway` and `scenario` need that job. When a check fails, the plan step exits and those jobs do not run. Collect requires a successful plan whose provenance statuses are `verified`, `not-published`, `resolved`, or `fixture`, and it does not write a report otherwise.

## Inputs

| Input | Lock field | What is checked |
| --- | --- | --- |
| capability-verifier | `releases.verifier` | Release, tag commit, workflow pin, image digest, attestation. `commit`, `image`, and `digest` in the lock are refused. |
| livingproof-sim-mujoco | `releases.runner` | Release, tag commit, source archive sha256, attestation. `commit`, when present, must equal the peeled tag. |
| robot-capability-specs | `releases.specs` | Release, tag commit, each `files` sha256, `sources.parameters.sha256`, attestation. `commit` must equal the peeled tag. Each `files` entry must equal that file at the tag. `sources.parameters.sha256`, when present, must equal `specs/scenarios/<scenario id>/parameters.json` at the specs commit. `sources.parameters.path` is the robot destination and is not the file that is hashed. |
| livingproof-worlds | `world` | Release, tag commit, `worlds/<id>/<version>/world.json` sha256, attestation. `commit`, when present, must equal the peeled tag. `world.sha256` and `sources.world.sha256`, when present, must equal that file. |
| livingproof-evaluator-scenario | `evaluators.livingproof-evaluator-scenario` | Release, tag commit, source archive sha256, attestation. `commit` in the lock is refused. |
| livingproof-evaluator-run | `evaluators.livingproof-evaluator-run` | Release, tag commit, source archive sha256, attestation. `commit`, when present, must equal the peeled tag. |
| livingproof-capability-report | `releases.reporter` | Release, tag commit, image digest, attestation. `commit` must equal the peeled tag. `image` and `digest` in the lock are refused. |

The tag is read from the definition when the definition has one, and otherwise from the lock. The image name passed to the trial is `ghcr.io/living-proof-labs/capability-verifier-hallway` or `ghcr.io/living-proof-labs/livingproof-capability-report`. The lock does not name it.

## Checks

For each input the plan asks GitHub for the Release named by the tag, peels the git tag to a commit, and then compares:

- **Release.** A missing Release fails. The message names the input, the tag, and that no release was found.
- **Commit.** The expected commit is the peeled tag ref: `GET /repos/{owner}/{repo}/git/ref/tags/{tag}`, following an annotated tag through `GET /git/tags/{sha}` until the object is a commit. When an attestation is published, its `gitCommit` is the primary cross-check and must equal that peeled commit. A difference fails as an attestation commit mismatch and no report is written. `target_commitish` counts only when it is a full commit SHA and that SHA equals the peeled commit. A full SHA that differs fails as a moved tag. Release notes are not scanned for commits. When the attestation is absent and `target_commitish` is not that SHA, the peeled tag is recorded and the pin's status is `resolved`.
- **sha256 or digest.** File pins use the `SHA256SUMS` asset on the Release when it lists the path. Image pins use the `image:version@sha256:…` line in the release notes when it names that image, compared with the registry manifest digest. Source pins use a `source.tar.gz` or `SOURCE` line in `SHA256SUMS` when present, compared with the sha256 of the tag archive. A published value that differs from the bytes fails. The message names the input, the path or image, and the expected and found values.
- **Attestation.** The plan reads `GET /repos/{owner}/{repo}/attestations/{digest}`. HTTP 404 means no attestation was published. HTTP 200 means an attestation exists: its subject digest, source repository, and commit must all match, or the run fails. The failure names which of the three disagreed and the expected and found values. The plan does not record a mismatched attestation. Any HTTP status other than 200 or 404 fails the run. The plan does not add a sigstore verifier. It trusts the attestations the GitHub API returns for that subject.

`contents: read` is the workflow permission. The plan does not ask for `attestations: read`, because every caller of this reusable workflow would have to grant that permission before the plan job could start.

The capability-verifier `uses:` lines stay a full commit SHA. GitHub rejects an expression there, so the plan cannot swap the called workflow. After the tag peels, that commit must be `e6a1ba347c525b62abf0644b610562b7bfb390d9` (`v2.12.5`). Any other commit fails before a trial. Renovate's GitHub Actions manager bumps that one line, SHA and `# vX.Y.Z` comment together. The lock's verifier tag must peel to the same commit. No lock hash is rewritten.

## Tag bump

capability-verifier v2.12.5 accepts `verifier-commit`, `verifier-image`, `reporter-image`, and `evaluator-commit`. The plan passes the commit and the `image:semver@sha256` ref it resolved. The lock fields those inputs replace are refused when present: `releases.verifier.commit`, `releases.verifier.image`, `releases.verifier.digest`, `releases.reporter.image`, `releases.reporter.digest`, and `evaluators.livingproof-evaluator-scenario.commit`. A one-line edit of the verifier tag or the scenario-evaluator tag therefore needs no relock of a commit or digest.

These fields stay, because v2.12.5 still reads them and has no input for them. A value that differs from the resolved tag is refused:

- `releases.runner.commit`
- `releases.specs.commit` and `releases.specs.files`
- `world.commit`, `world.sha256`, and `sources.world.sha256`
- `sources.parameters.sha256`
- `releases.reporter.commit`

`repin --check` requires a commit on every `releases.*` entry, including the runner, the specs, and the reporter. `fetch_hallway_specs.py` checks out `releases.specs.commit` and, when `world.commit` is present, `world.commit`. `sources.world` is fetched at the world commit. `sources.parameters.sha256` is compared with `specs/scenarios/<scenario id>/parameters.json` at the specs commit, the same file capability-verifier fetches. The lock's `sources.parameters.path` is only where that file is written in the robot repository. A runner, specs, or world tag edit that leaves the old commit or hashes in the lock is refused before a trial. The runner tag must also still match the robot `pyproject.toml` source, which `repin --check` compares with `releases.runner.tag`.

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

The plan step sets `GITHUB_TOKEN` from `github.token`. `contents: read` covers the release, tag, and attestation reads. That token is sent only to `api.github.com`. The codeload archive and the registry token request do not receive it. A `bundle_url` is fetched only from `https://tmaproduction.blob.core.windows.net`, with no token, with redirects refused, with a 30 second timeout, and with the body capped at 16 MiB.
