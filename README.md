# verification-reports-action

**Start here: [published manual](https://docs.livingprooflabs.com/verification-reports-action/).**

The manual is built from this page and the `docs/` folder, and it is published after each release.

## What it does

Living Proof Verification Reports writes a verification report for your robot from your own CI, on your own runners. The definitions stay in your robot repository, and each change can produce a report you can review. A robot FAIL is a green run with a grade: the report was written, and the grade says how the robot did.

## Who it's for

Robot teams who want a reviewable report on each change.

## See a real report

A published Unitree G1 run, the stock MuJoCo hallway, is here: <https://docs.livingprooflabs.com/verification-reports-action/reports/g1/unitree-g1/stock-mujoco-hallway/runs/46062882502/>.

Every published report is listed at <https://docs.livingprooflabs.com/verification-reports-action/reports/>.

## Quick start

Add one job to the workflow in your robot repository. Pin this action to a release, either `@vX.Y.Z` or a 40-character commit SHA with a `# vX.Y.Z` comment. The calling job needs `contents: read` and `pull-requests: write`.

```yaml
name: Verification report
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  report:
    permissions:
      contents: read
      pull-requests: write
    uses: Living-Proof-Labs/verification-reports-action/.github/workflows/report.yml@vX.Y.Z
    with:
      definitions: verifications/*.toml
      evaluators: evaluators.yml
```

Replace `vX.Y.Z` with the release you want. Put the definition files in the robot repository. Each one is a `livingproof-verification-definition` TOML file with a sibling `.lock.json`. Add `merge_group` to `on:` when the repository uses a merge queue. Longer notes on that job are in [docs/workflow.md](docs/workflow.md).

## The real G1 example

This repository runs that hallway itself. The `g1-smoke` job in [`.github/workflows/verify.yml`](https://github.com/Living-Proof-Labs/verification-reports-action/blob/main/.github/workflows/verify.yml#L198-L219) calls the workflow on `Living-Proof-Labs/livingproof-unitree-g1`. A robot repository writes `uses: Living-Proof-Labs/verification-reports-action/.github/workflows/report.yml@<release>` instead of `uses: ./.github/workflows/report.yml`.

Excerpt from `.github/workflows/verify.yml`:

```yaml
  g1-smoke:
    name: Real G1 smoke
    needs: version
    if: >-
      !cancelled() &&
      (needs.version.result == 'success' || needs.version.result == 'skipped')
    # capability-verifier's comment job declares pull-requests: write.
    # With comment: false it never writes.
    # ref is the peeled commit of livingproof-unitree-g1 v2.3.34.
    # The definition under tests/smoke/g1 asks for one hallway trial.
    permissions:
      contents: read
      pull-requests: write
    uses: ./.github/workflows/report.yml
    with:
      definitions: tests/smoke/g1/*.toml
      output-dir: artifacts/verification-report
      version: ${{ needs.version.outputs.semver-version }}
      repository: Living-Proof-Labs/livingproof-unitree-g1
      ref: d554c8243a12849dc7fe8acc1921bb3d4e5239a7
      artifact-name: g1-verification-report
      evaluators: evaluators.yml
```

The job reads [`evaluators.yml`](https://github.com/Living-Proof-Labs/verification-reports-action/blob/main/evaluators.yml).

Excerpt from `evaluators.yml`:

```yaml
- Living-Proof-Labs/livingproof-evaluator-run@v1.9.0/livingproof-evaluator.json
- Living-Proof-Labs/livingproof-evaluator-health@v1.2.0/livingproof-evaluator.json
```

The hallway definition is [`tests/smoke/g1/unitree-g1-stock-mujoco-hallway.toml`](https://github.com/Living-Proof-Labs/verification-reports-action/blob/main/tests/smoke/g1/unitree-g1-stock-mujoco-hallway.toml), with its lock at [`unitree-g1-stock-mujoco-hallway.lock.json`](https://github.com/Living-Proof-Labs/verification-reports-action/blob/main/tests/smoke/g1/unitree-g1-stock-mujoco-hallway.lock.json).

Excerpt from `tests/smoke/g1/unitree-g1-stock-mujoco-hallway.toml`:

```toml
id = "unitree-g1-stock-mujoco-hallway"
procedure = "walk-hallway"
scenario = "walk-hallway@0.1.0"

[robot]
repository = "Living-Proof-Labs/livingproof-unitree-g1"
ref = "v2.3.34"
configurations = ["stock"]

[world]
id = "hallway-open"
version = "0.1.0"
repository = "Living-Proof-Labs/livingproof-worlds"
tag = "v1.1.0"

[plan]
trials = { count = 1 }
command_speed = 0.5
recording = { max_gap_seconds = 0.05 }
```

## Evaluators

How declarative runs work. Each evaluator is one line, `owner/repo@<vX.Y.Z or 40-hex sha>/path.json`, naming a descriptor validated against `livingproof-evaluator-implementation/1`. Your repository runs everything on its own runners. Adding an evaluator means adding one line.

The Health descriptor is [`livingproof-evaluator.json`](https://github.com/Living-Proof-Labs/livingproof-evaluator-health/blob/v1.2.0/livingproof-evaluator.json). The schema is [`implementation.schema.json`](https://github.com/Living-Proof-Labs/livingproof-evaluator/blob/main/schema/implementation.schema.json). The explainer is [`implementation-descriptor.md`](https://github.com/Living-Proof-Labs/livingproof-evaluator/blob/main/docs/implementation-descriptor.md).

Leave `evaluators` empty when the run should not declare any. An evaluator that reads recorded media needs `media: true`. The report draws each card from that evaluator's result. How a path or an inline list is passed on is in [docs/workflow.md](docs/workflow.md).

## Reference

### Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `definitions` | No | `verifications/*.toml` | Glob of definition files in the caller repository. |
| `output-dir` | No | `artifacts/verification-report` | Directory collected into the report artifact. |
| `artifact-name` | No | `verification-report` | Name of the uploaded report artifact. A second call in the same workflow needs a different name. |
| `version` | No | empty | Optional version recorded in each `run.json`. |
| `repository` | No | caller | Another robot repository. `ref` must then be that repository's full commit SHA. |
| `ref` | No | caller SHA | Robot commit. A release tag is rejected. |
| `media` | No | `true` | Record MCAP and video when capability-verifier records them. |
| `comment` | No | `false` | Ask capability-verifier to comment on a same-repository pull request. The comment job declares `pull-requests: write`. With this false it never writes. |
| `evaluators` | No | empty | Path in the caller repository, or the declaration list itself. Empty means no evaluators. |

### Outputs

| Output | Description |
| --- | --- |
| `report-path` | Directory of the written reports inside the collect job. |
| `status` | `ok` when a report was written for every definition. |
| `grade` | The robot grade when there is one result, otherwise the `grades` JSON map. |
| `grades` | JSON map of `definition/configuration` to robot grade. |

`status` is `ok` when the report was written, including when the grade is `FAIL` or `INSUFFICIENT_EVIDENCE`. For each definition and configuration the artifact contains `report.html`, `report.md`, `report.json`, `report.txt`, `SHA256SUMS` and `run.json`, plus `evidence/` with the trial observations and any MCAP or video the run recorded. `run.json` records the tested commit, which is the caller SHA, the lock's declared robot under `declared_robot`, and each pinned input under `inputs`. `report.json` records the same `inputs`. The files are uploaded as the `verification-report` artifact. Callers download that artifact; the files are not left in the caller job's workspace.

### Checkout

The caller's token must be able to read the workflow repository. Plan, fixture, and collect check those scripts out with the caller's `GITHUB_TOKEN` (`contents: read` is enough when this repository is public or the token has been granted access). If the token cannot read the workflow repository, the Workflow source checkout fails before any definition is planned and no verification report is written. The failure is a checkout error: the repository was not found, or the token does not have permission to read it.

This is a reusable workflow, `.github/workflows/report.yml`, because the runs go through capability-verifier's `walk-hallway.yml` and `scenario.yml`. Those workflows use matrices, container images and separate runners. A composite action is one step in the caller's job and cannot call them.

`action.yml` remains for this repository's fixture tests. Any other caller that uses the root composite action is told to call the workflow. The workflow passes no secrets. `GITHUB_TOKEN` is the credential. The interface choice is recorded in [docs/decisions/0001-composite-vs-reusable-workflow.md](docs/decisions/0001-composite-vs-reusable-workflow.md).

### The site step

Use the [`site` step](https://github.com/Living-Proof-Labs/verification-reports-action/blob/main/site/action.yml) after downloading the report artifact, so a robot repository's documentation build can include `reports/` directly. Layout, inputs, history across deployments, and recovery are in [docs/workflow.md](docs/workflow.md).

```yaml
- uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0
  with:
    name: verification-report
    path: artifacts/verification-report
- uses: Living-Proof-Labs/verification-reports-action/site@<commit> # vX.Y.Z
  with:
    runs-dir: artifacts/verification-report
    output-dir: artifacts/docs/html/reports
    evidence-retention-days: '7'
    site-url: https://docs.livingprooflabs.com/livingproof-unitree-g1/
```

### Provenance

Before any trial, the plan resolves each pinned release and refuses the run when a check fails. Which lock fields stay, which are refused, and what each status means are in [docs/provenance.md](docs/provenance.md).

### Release

`Verify` runs on pull requests, merge groups, and pushes to `main`. Pull requests into `main` get a unique candidate version from `semantic-release-candidate.yml`. The mock robot smoke runs the report workflow on this repository's fixture definitions and uploads `smoke-report-<version>` from the `verification-report` artifact. The real G1 smoke runs the report workflow on the Unitree G1 stock MuJoCo hallway, `Living-Proof-Labs/livingproof-unitree-g1` at `v2.3.34`, with one trial, through capability-verifier. Run Integrity and Health come from `evaluators.yml`. It uploads `g1-smoke-report-<version>` from the `g1-verification-report` artifact. The site smoke job builds the static `reports/` section from the mock report. Each release publishes that report and the real G1 smoke report from the same Verify run: [mock robot report](https://docs.livingprooflabs.com/verification-reports-action/reports/), [Unitree G1 report](https://docs.livingprooflabs.com/verification-reports-action/reports/g1/).

After `Verify` succeeds for a push to `main`, `Release` runs. The `release` job tags that commit and publishes the GitHub release. `post-release` runs only when `release` publishes (`published == 'true'`), calls this workflow on the fixture definitions in `tests/fixtures/robot/verifications/` with the published `version`, and `post-release-check` checks out `git-tag`.

### Development

Prerequisites: Node.js 22+, Python 3.12, [uv](https://docs.astral.sh/uv/), and [Task](https://taskfile.dev/) 3.54.0.

```sh
task setup
task verify
```

Build the manual locally with `task -t Taskfile.runtime.yml docs:build`.

### License

[Apache-2.0](LICENSE).
