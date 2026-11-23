# verification-reports-action

Reusable **Living Proof Verification Reports** workflow and site publisher. A robot repository calls the workflow to get one verification report for the definitions in that repository, and uses the `site` step to publish a single-robot `reports/` section inside its documentation.

Tracked in [Living-Proof-Labs/.github#164](https://github.com/Living-Proof-Labs/.github/issues/164). The workflow interface choice is recorded in [docs/decisions/0001-composite-vs-reusable-workflow.md](docs/decisions/0001-composite-vs-reusable-workflow.md).

## Reusable workflow

This is a reusable workflow, `.github/workflows/report.yml`, because the runs go through capability-verifier's `walk-hallway.yml` and `scenario.yml`. Those workflows use matrices, container images and separate runners. A composite action is one step in the caller's job and cannot call them.

`action.yml` remains for this repository's fixture tests. Any other caller that uses the root composite action is told to call the workflow.

Robot repositories pin a semantic-release tag, or a full commit SHA with a `# vX.Y.Z` comment. The workflow passes no secrets. `GITHUB_TOKEN` is the credential. Every job has `contents: read`. The two jobs that call capability-verifier also have `pull-requests: write`, because `walk-hallway.yml`'s `scenario` job and `scenario.yml`'s `comment` job declare it. With `comment: false` that job never writes. Callers of this workflow grant the same pair on the calling job. `merge_group` works when the caller workflow listens for it; the robot commit is that event's SHA. The workflow checks its own scripts out from `job.workflow_repository` at `job.workflow_sha` and checks the caller out separately for definitions and locks.

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

## Reports section step (`site/action.yml`)

Use `Living-Proof-Labs/verification-reports-action/site` after downloading the `verification-report` artifact so a robot repository's documentation build can include `reports/` directly.

### Generated `reports/` layout

- `reports/index.html`: single-robot index of the latest result per scenario ordered with failures first, plus a link to [archived verification reports](https://docs.livingprooflabs.com/livingproof-verification/reports/index.html) for earlier history.
- `reports/<robot>/<variant>/index.html`: scenario run history ordered newest first, with a `Latest report` link (`latest/`).
- `reports/<robot>/<variant>/runs/<run-id>/`: immutable per-run directory holding the scrubbed report pages, `run.json`, `SHA256SUMS`, grading inputs, and retained evidence.
- `reports/site.json`: machine-readable publication marker.

### History across deployments and pull-request runs

- **Main deployments (`restore-history: auto`)**: restores earlier `reports/` runs from the calling repository's last successfully deployed GitHub Pages artifact (`github-pages` or `github-pages-*`) without committing run history to Git. Grant `actions: read` to the build job so it can list and download the previous Pages artifact.
- **Evidence retention**: copied MCAP, video, and media files older than `evidence-retention-days` (default `7`) are pruned on subsequent builds while report pages, `run.json`, and `SHA256SUMS` remain valid.
- **Pull requests**: `restore-history: auto` skips restoring Pages history, `upload-artifact: auto` uploads the generated `reports/` tree as a workflow artifact, `artifact-only` is set to `true`, and nothing is deployed.

### Example `site` step

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

### `site` inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `runs-dir` | Yes | — | Directory of per-scenario run folders (`report.{html,json,md,txt}`, `SHA256SUMS`, `run.json`). |
| `output-dir` | No | `reports` | Destination path for the static `reports/` tree (for example `artifacts/docs/html/reports`). |
| `evidence-retention-days` | No | `7` | Days to retain copied MCAP, video, and media files before pruning. |
| `restore-history` | No | `auto` | Restore earlier runs from the last deployed Pages artifact (`auto`, `true`, or `false`). |
| `site-url` | No | empty | Optional live documentation URL checked when no Pages artifact is listed. |
| `branch` | No | `main` | Branch whose deployed Pages artifacts hold earlier runs. |
| `pages-artifact` | No | empty | Exact Pages artifact name (matches `github-pages` and `github-pages-*` when empty). |
| `pages-job` | No | empty | Pages deploy job name (matches `pages`, `deploy`, and `deploy-pages` when empty). |
| `reset-history` | No | `false` | Start fresh without restoring earlier runs (`true` or `false`). |
| `upload-artifact` | No | `auto` | Upload the built `reports/` directory as a workflow artifact (`auto` uploads on `pull_request`). |
| `artifact-name` | No | empty | Workflow artifact name when uploaded (defaults to `verification-reports-site-<run_id>-<attempt>`). |
| `artifact-retention-days` | No | `14` | Retention days for the uploaded workflow artifact. |
| `github-token` | No | `github.token` | Token used for read-only Pages artifact lookup and download. |

### `site` outputs

| Output | Description |
| --- | --- |
| `reports-dir` | Path to the generated static `reports/` directory. |
| `index-path` | Path to `reports/index.html`. |
| `runs-count` | Total number of stored runs in the generated `reports/` tree. |
| `artifact-only` | `true` on `pull_request` and `merge_group` runs (artifact only, never deployed); `false` otherwise. |

## Release

`Verify` runs on pull requests, merge groups, and pushes to `main`. Pull requests into `main` get a unique candidate version from `semantic-release-candidate.yml`. The smoke jobs run the report workflow on the fixture robot, upload `smoke-report-<version>`, and build the static `reports/` section from `verification-report`.

After `Verify` succeeds for a push to `main`, `Release` runs. The `release` job tags that commit and publishes the GitHub release. `post-release` runs only when `release` publishes (`published == 'true'`), calls this workflow with the published `version`, and `post-release-check` checks out `git-tag`.

## Development

Prerequisites: Node.js 22+, Python 3.12, [uv](https://docs.astral.sh/uv/), and [Task](https://taskfile.dev/) 3.54.0.

```sh
task setup
task verify
```

## License

[Apache-2.0](LICENSE).
