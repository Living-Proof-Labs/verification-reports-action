# verification-reports-action

Reusable **Living Proof Verification Reports** GitHub Action. Runs robot capability verification for a pinned robot configuration and publishes a traceable verification report section for that robot's documentation.

Tracked in [Living-Proof-Labs/.github#164](https://github.com/Living-Proof-Labs/.github/issues/164) (repository setup in [Living-Proof-Labs/.github#165](https://github.com/Living-Proof-Labs/.github/issues/165)).

## Status

- **Root runner (`action.yml`)**: currently a runnable placeholder template (`src/run.mjs`) while verification runner execution and provenance checks are tracked in [#3](https://github.com/Living-Proof-Labs/verification-reports-action/issues/3) and [#4](https://github.com/Living-Proof-Labs/verification-reports-action/issues/4).
- **Reports section publisher (`site/action.yml`)**: builds a static single-robot `reports/` directory tree (`index.html`, per-scenario history, and immutable per-run folders) that a robot repository's documentation build can include directly ([#5](https://github.com/Living-Proof-Labs/verification-reports-action/issues/5)).

## Reports section step (`site/action.yml`)

Use `Living-Proof-Labs/verification-reports-action/site` after verification run folders have been generated and alongside the robot repository's documentation build.

### Expected input per run folder

`runs-dir` points to a directory of per-scenario run folders, each containing:
- `report.html`, `report.json`, `report.md`, `report.txt`
- `SHA256SUMS`
- `run.json`
- optional `thresholds.json` grading inputs and `evidence/` files (such as `.mcap` and `.mp4`)

### Generated `reports/` layout

- `reports/index.html`: single-robot index showing the latest result per scenario with failures first, plus a link to [archived verification reports](https://docs.livingprooflabs.com/livingproof-verification/reports/index.html) for earlier history.
- `reports/<robot>/<variant>/index.html`: scenario run history ordered newest first, with a `Latest report` link (`latest/`).
- `reports/<robot>/<variant>/runs/<run-id>/`: immutable per-run directory holding the scrubbed report pages, `run.json`, `SHA256SUMS`, grading inputs, and retained evidence.
- `reports/site.json`: machine-readable publication marker.

### History across deployments and pull-request runs

- **Main deployments (`restore-history: auto`)**: restores earlier `reports/` runs from the calling repository's last successfully deployed GitHub Pages artifact (`github-pages` or `github-pages-*`) without committing run history to Git. Grant `actions: read` to the build job so it can list and download the previous Pages artifact.
- **Evidence retention**: copied MCAP, video, and media files older than `evidence-retention-days` (default `7`) are pruned on subsequent builds while report pages, `run.json`, and `SHA256SUMS` remain valid.
- **Pull requests**: `restore-history: auto` skips restoring Pages history, `upload-artifact: auto` uploads the generated `reports/` tree as a workflow artifact, `artifact-only` is set to `true`, and nothing is deployed.

### `site` inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `runs-dir` | Yes | — | Directory of per-scenario run folders (`report.{html,json,md,txt}`, `SHA256SUMS`, `run.json`). |
| `output-dir` | No | `reports` | Destination path for the static `reports/` tree (for example `artifacts/docs/html/reports`). |
| `evidence-retention-days` | No | `7` | Days to retain copied MCAP, video, and media files before pruning. |
| `restore-history` | No | `auto` | Restore earlier runs from the last deployed Pages artifact (`auto`, `true`, or `false`). |
| `site-url` | No | empty | Optional live documentation URL checked when no Pages artifact is listed. |
| `repository` | No | `github.repository` | Repository `owner/name` whose deployed Pages artifact holds earlier runs. |
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

### Example workflow step

```yaml
- uses: Living-Proof-Labs/verification-reports-action/site@v1.1.0
  with:
    runs-dir: artifacts/verification-runs
    output-dir: artifacts/docs/html/reports
    evidence-retention-days: '7'
    site-url: https://docs.livingprooflabs.com/livingproof-unitree-g1/
```

## Root runner inputs and outputs (placeholder)

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `pins-path` | No | `pins.json` | Path to the robot pins manifest. |
| `robot-manifest` | No | `robot.json` | Path to the robot manifest. |
| `runner-config` | No | `runner.json` | Path to the runner configuration. |
| `output-dir` | No | `artifacts/verification-report` | Directory where report artifacts are written. |
| `version` | No | empty | Optional version recorded in the report. |

| Output | Description |
| --- | --- |
| `report-path` | Path to the generated verification report file. |
| `status` | Execution status of the verification report action (`placeholder` in this template). |

## Release

`Verify` runs on pull requests, merge groups, and pushes to `main`. Pull requests into `main` get a unique candidate version from `semantic-release-candidate.yml`: the smoke job records `semver-version` and uploads `smoke-report-<version>`.

After `Verify` succeeds for a push to `main`, `Release` runs. The `release` job tags that commit and publishes the GitHub release. `post-release` runs only when `release` publishes (`published == 'true'`), checks out `git-tag`, and runs this action with the published `version`.

## Development

Prerequisites: Node.js 22+, Python 3.12, [uv](https://docs.astral.sh/uv/), and [Task](https://taskfile.dev/) 3.54.0.

```sh
task setup
task verify
```

## License

[Apache-2.0](LICENSE).
