# verification-reports-action

Reusable **Living Proof Verification Reports** workflow. A robot repository calls it and gets one verification report for the definitions in that repository. The robot under test is the caller at the checked-out commit.

Tracked in [Living-Proof-Labs/.github#164](https://github.com/Living-Proof-Labs/.github/issues/164). The interface choice is recorded in [docs/decisions/0001-composite-vs-reusable-workflow.md](docs/decisions/0001-composite-vs-reusable-workflow.md).

## Reusable workflow

This is a reusable workflow, `.github/workflows/report.yml`, because the runs go through capability-verifier's `walk-hallway.yml` and `scenario.yml`. Those workflows use matrices, container images and separate runners. A composite action is one step in the caller's job and cannot call them.

`action.yml` remains for this repository's fixture tests. Any other caller that uses the composite action is told to call the workflow.

Robot repositories pin a semantic-release tag, or a full commit SHA with a `# vX.Y.Z` comment. The workflow passes no secrets. `GITHUB_TOKEN` is the credential. Every job has `contents: read`. The two jobs that call capability-verifier also have `pull-requests: write`, because `walk-hallway.yml`'s `scenario` job and `scenario.yml`'s `comment` job declare it. With `comment: false` that job never writes. Callers of this workflow grant the same pair on the calling job. `merge_group` works when the caller workflow listens for it; the robot commit is that event's SHA. The workflow checks its own scripts out from `job.workflow_repository` at `job.workflow_sha` and checks the caller out separately for definitions and locks.

The caller's token must be able to read the workflow repository. Plan, fixture, and collect check those scripts out with the caller's `GITHUB_TOKEN` (`contents: read` is enough when this repository is public or the token has been granted access). If the token cannot read the workflow repository, the Workflow source checkout fails before any definition is planned and no verification report is written. The failure is a checkout error: the repository was not found, or the token does not have permission to read it.

A robot `FAIL` or `INSUFFICIENT_EVIDENCE` is a successful run. `status` is `ok` when the report was written. `grade` is the robot grade for a single result, and `grades` is a JSON map of `definition/configuration` to grade. The files are uploaded as the `verification-report` artifact. Callers download that artifact; the files are not left in the caller job's workspace.

## Pinned inputs

Before any trial, the plan job resolves each input from its release tag and refuses the run when the check fails. The message names the input and the expected and found values. No `report.json` is written, and `walk-hallway` and `scenario` do not start, because both need the plan job. The inputs are capability-verifier, livingproof-sim-mujoco, robot-capability-specs, the livingproof-worlds world, livingproof-evaluator-scenario, livingproof-evaluator-run, and livingproof-capability-report together with its image.

The tag in the lock is the pin. A Renovate bump changes that tag and leaves the lock's commit, file sha256, and image digest as they were. The plan resolves the new tag from the GitHub Release, the git tag, the published checksum or registry digest, and the attestation when one is published. Nothing in this repository rewrites the lock. The definition file's sha256 in the lock still has to match the definition bytes, so the tag Renovate edits is the lock tag, not a line inside the definition.

`report.json` and `run.json` each record `inputs`: repository, tag, commit, digest, and `status` for every pin. The same status line is written into `report.md`, `report.html`, and `report.txt` under "Pinned inputs", as `name: status (meaning)`.

| Status | Meaning |
| --- | --- |
| `verified` | An attestation was published and its subject digest, source repository, and commit all matched. |
| `not-published` | The attestations API returned HTTP 404. The commit was checked against the release instead. |
| `resolved` | No attestation was published and the release named no commit. The peeled tag is what the report records. |
| `fixture` | This repository's own fixture. The release was not checked. |

`verified` is the status that means the attestation matched. The other three stay visible so a reader can tell a verified pin from one that was not. A published attestation that does not match the digest, the source repository, or the commit fails the run and writes no report. Any attestations response other than HTTP 200 or HTTP 404 fails the run. A missing GitHub Release, or a tag that does not peel to a commit, fails the run as well. That 404 is not recorded as `not-published`.

`status: resolved` can sit beside `attestation: not-published` on the same input. The attestation field means the attestations API returned HTTP 404. `resolved` means the release also named no commit, so the report records the peeled tag. That pin is not verified. `status: not-published` with the same attestation field means HTTP 404 as well, and the commit was checked against the release.

Source pins (livingproof-sim-mujoco and both evaluators) use the sha256 of the codeload tag archive as the digest they ask GitHub to attest. A specs pin with more than one file uses a synthetic sha256 of those file hashes. Neither value is a subject a real attestation names, so those pins stay `not-published` until a later change asks for a subject the release actually publishes. An image pin uses the registry digest, and a single-file pin uses that file's sha256. Those can match.

The plan step passes `GITHUB_TOKEN` from `github.token` so the release, tag, and attestation reads are authenticated. `contents: read` is enough. The token is sent only to `api.github.com`. The check rules are in [docs/provenance.md](docs/provenance.md).

## Example

```yaml
name: Verification report
on:
  pull_request:
  merge_group:
    types: [checks_requested]
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  report:
    # capability-verifier's comment job declares pull-requests: write.
    # With comment: false it never writes.
    permissions:
      contents: read
      pull-requests: write
    uses: Living-Proof-Labs/verification-reports-action/.github/workflows/report.yml@<commit> # vX.Y.Z
    with:
      definitions: verifications/*.toml
```

Replace `<commit>` and `vX.Y.Z` with the published release. The definition files live in the robot repository. Each one is a `livingproof-verification-definition` TOML file with a sibling `.lock.json`. `procedure` is `walk-hallway` or `scenario`.

## Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `definitions` | No | `verifications/*.toml` | Glob of definition files in the caller repository. |
| `output-dir` | No | `artifacts/verification-report` | Directory collected into the `verification-report` artifact. |
| `version` | No | empty | Optional version recorded in each `run.json`. |
| `repository` | No | caller | Another robot repository. `ref` must then be that repository's full commit SHA. |
| `ref` | No | caller SHA | Robot commit. A release tag is rejected. |
| `media` | No | `true` | Record MCAP and video when capability-verifier records them. |
| `comment` | No | `false` | Ask capability-verifier to comment on a same-repository pull request. The comment job declares `pull-requests: write`. With this false it never writes. |

## Outputs

| Output | Description |
| --- | --- |
| `report-path` | Directory of the written reports inside the collect job. |
| `status` | `ok` when a report was written for every definition. |
| `grade` | The robot grade when there is one result, otherwise the `grades` JSON map. |
| `grades` | JSON map of `definition/configuration` to robot grade. |

For each definition and configuration the artifact contains `report.html`, `report.md`, `report.json`, `report.txt`, `SHA256SUMS` and `run.json`, plus `evidence/` with the trial observations and any MCAP or video the run recorded. `run.json` records the tested commit, which is the caller SHA, the lock's declared robot under `declared_robot`, and each pinned input under `inputs`. `report.json` records the same `inputs`.

## Release

`Verify` runs on pull requests, merge groups, and pushes to `main`. Pull requests into `main` get a unique candidate version from `semantic-release-candidate.yml`. The smoke job runs this workflow on the fixture robot and uploads `smoke-report-<version>`.

After `Verify` succeeds for a push to `main`, `Release` runs. The `release` job tags that commit and publishes the GitHub release. `post-release` runs only when `release` publishes (`published == 'true'`), calls this workflow with the published `version`, and `post-release-check` checks out `git-tag`.

## Development

Prerequisites: Node.js 22+ and [Task](https://taskfile.dev/) 3.54.0.

```sh
task setup
task verify
```

## License

[Apache-2.0](LICENSE).
