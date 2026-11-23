# verification-reports-action

Reusable **Living Proof Verification Reports** workflow. A robot repository calls it and gets one verification report for the definitions in that repository. The robot under test is the caller at the checked-out commit.

Tracked in [Living-Proof-Labs/.github#164](https://github.com/Living-Proof-Labs/.github/issues/164). The interface choice is recorded in [docs/decisions/0001-composite-vs-reusable-workflow.md](docs/decisions/0001-composite-vs-reusable-workflow.md).

## Reusable workflow

This is a reusable workflow, `.github/workflows/report.yml`, because the runs go through capability-verifier's `walk-hallway.yml` and `scenario.yml`. Those workflows use matrices, container images and separate runners. A composite action is one step in the caller's job and cannot call them.

`action.yml` remains for this repository's fixture tests. Any other caller that uses the composite action is told to call the workflow.

Robot repositories pin a semantic-release tag, or a full commit SHA with a `# vX.Y.Z` comment. The workflow passes no secrets. `GITHUB_TOKEN` is the credential. Every job has `contents: read`. The two jobs that call capability-verifier also have `pull-requests: write`, because `walk-hallway.yml`'s `scenario` job and `scenario.yml`'s `comment` job declare it. With `comment: false` that job never writes. Callers of this workflow grant the same pair on the calling job. `merge_group` works when the caller workflow listens for it; the robot commit is that event's SHA.

A robot `FAIL` or `INSUFFICIENT_EVIDENCE` is a successful run. `status` is `ok` when the report was written. `grade` is the robot grade for a single result, and `grades` is a JSON map of `definition/configuration` to grade. The files are uploaded as the `verification-report` artifact. Callers download that artifact; the files are not left in the caller job's workspace.

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

For each definition and configuration the artifact contains `report.html`, `report.md`, `report.json`, `report.txt`, `SHA256SUMS` and `run.json`, plus `evidence/` with the trial observations and any MCAP or video the run recorded. `run.json` records the tested commit, which is the caller SHA, and the lock's declared robot under `declared_robot`.

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
