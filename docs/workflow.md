<!-- SPDX-License-Identifier: Apache-2.0 -->

# Workflow and site

The [README](../index.md) is the short tour: what the action does, a caller job, the real G1 example, and how an evaluator line is declared. This page keeps the longer notes.

## Caller job

Pin [`.github/workflows/report.yml`](https://github.com/Living-Proof-Labs/verification-reports-action/blob/main/.github/workflows/report.yml) to a release tag, or to a full commit SHA with a `# vX.Y.Z` comment. The calling job grants `contents: read` and `pull-requests: write`. With `comment: false` the comment job never writes. `merge_group` works when the caller workflow listens for it; the robot commit is that event's SHA.

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
    permissions:
      contents: read
      pull-requests: write
    uses: Living-Proof-Labs/verification-reports-action/.github/workflows/report.yml@<commit> # vX.Y.Z
    with:
      definitions: verifications/*.toml
      evaluators: evaluators.yml
```

The same list can be inlined instead of a path:

```yaml
      evaluators: |
        - Living-Proof-Labs/livingproof-evaluator-run@v1.9.0/livingproof-evaluator.json
```

Leave `evaluators` unset when the run should not declare any.

## How the evaluators input is passed

Run Integrity is declared by the caller, not by a default in this action. The `evaluators` input is either a path in the caller repository or the list itself. Empty means no evaluators: the plan passes a `#` comment so capability-verifier skips its built-in run-integrity job, and the collect job does not install an evaluator or add a Run Integrity card.

A path such as `evaluators.yml` is a YAML list in the caller repository. Each item is one declaration line. The line format is `owner/repo@vX.Y.Z/path.json`, or the same shape with a 40-character commit in place of the tag. Blank lines and `#` comments are not items. This action joins the items with newlines and passes that text to the `evaluators` input of `walk-hallway.yml` and `scenario.yml`. It does not check the line. capability-verifier refuses a branch name, a short commit, or a relative path, and that refusal is the run's failure reason.

An inline list is the same lines, one per line or as a YAML list, passed directly in `evaluators`.

This repository's own smoke passes `evaluators: evaluators.yml`. Other callers do not inherit that file. An evaluator that reads recorded media needs `media: true`.

When the list has items, the collect job checks out capability-verifier, appends the scenario evaluator results onto the verified bundle, runs the loader on the downloaded evidence, and re-renders the report with the reporter version the lock pins. The loader's exit code is not used. A missing or unreadable provenance file is recorded as Run Integrity NOT RUN (failure), the report is uploaded, and the collect job then fails.

The descriptor schema and the Health example are linked from the README. Pinned release checks are in [Provenance](provenance.md).

## Reports section step (`site/action.yml`)

Use [`Living-Proof-Labs/verification-reports-action/site`](https://github.com/Living-Proof-Labs/verification-reports-action/blob/main/site/action.yml) after downloading the `verification-report` artifact so a robot repository's documentation build can include `reports/` directly.

### Generated `reports/` layout

- `reports/index.html`: single-robot index of the latest result per scenario, with failures first.
- `reports/<robot>/<variant>/index.html`: scenario run history ordered newest first, with a `Latest report` link (`latest/`).
- `reports/<robot>/<variant>/runs/<run-id>/`: immutable per-run directory holding the scrubbed report pages, `run.json`, `SHA256SUMS`, grading inputs, and retained evidence.
- `reports/site.json`: machine-readable publication marker.

A run folder's `SHA256SUMS` can be recomputed by whoever wrote the folder, so the site checks what it publishes itself:

- **Report pages**: each `report.html` and run `index.html` must be valid UTF-8 and fit an allowlist of the reporter's own markup, checked with an HTML parser. Allowed links are `https://` or relative (media `src` and `poster` attributes must be relative), CSS may only embed fonts, and scripts must be the reporter's own, pinned by SHA-256. A page that fits is published byte for byte. Anything else fails the build, except an unknown reporter script, which is removed with a warning. A page larger than 8 MiB (8,388,608 bytes of UTF-8) fails the build before it is parsed. That is more than ten times the largest page the reporter has published. Deeply nested markup does not slow the check down: its cost grows with the page size, not with the square of the nesting depth.
- **Evidence**: files a browser would run, such as `.html`, `.htm`, `.xhtml`, `.svg`, `.xml` or `.js`, are not copied.

### History across deployments and pull-request runs

- **Main deployments (`restore-history: auto`)**: restores `reports/` runs from the calling repository's last successfully deployed GitHub Pages artifact (`github-pages` or `github-pages-*`) without committing run history to Git. Grant `actions: read` to the build job so it can list and download the previous Pages artifact.
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
| `output-dir` | No | `reports` | Destination path for the static `reports/` tree (for example `artifacts/docs/html/reports`). It is resolved inside `GITHUB_WORKSPACE`, with symlinks followed. Paths that escape the workspace, name the workspace root, or contain `..`, `.git` or a control character are refused. |
| `evidence-retention-days` | No | `7` | Days to retain copied MCAP, video, and media files before pruning. |
| `restore-history` | No | `auto` | Restore runs from the last deployed Pages artifact (`auto`, `true`, or `false`). |
| `site-url` | No | empty | Optional live documentation URL checked when no Pages artifact is listed. |
| `branch` | No | `main` | Branch whose deployed Pages artifacts hold runs from previous deployments. |
| `pages-artifact` | No | empty | Exact Pages artifact name (matches `github-pages` and `github-pages-*` when empty). |
| `pages-job` | No | empty | Pages deploy job name (matches `pages`, `deploy`, and `deploy-pages` when empty). |
| `reset-history` | No | `false` | Start fresh without restoring runs from the previous deployment (`true` or `false`). It is read only while history is being restored, so it does nothing when `restore-history` is off. |
| `exclude-runs` | No | empty | Whitespace- or comma-separated list of restored run folder names (for example `43954868926`) to drop from restored history before building. An entry matches that run folder in every scenario. Fails if an entry is not a plain run folder name or matches a run in `runs-dir`. |
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

### Recovering from a restored run that fails the check

Every site build re-checks the runs it restores from the last GitHub Pages deployment. If a previously published run fails the HTML or evidence policy—for example `error: restored .../runs/<run-id>/report.html: holds a <script> that is not a known reporter script` or `error: restored .../runs/<run-id>/evidence/...: evidence of this type is not published`—the build fails closed and blocks later deployments until that run is no longer in the restored history.

1. **Option 1 (recommended): exclude the failing run ID with `exclude-runs`.**
   Add the `<run-id>` folder name from the error message to `exclude-runs` in the workflow's `site` step (whitespace- or comma-separated), then run the site build on a branch that deploys Pages (a pull request build is artifact-only, so it does not change what later builds restore). The run folder is dropped from the restored tree in every scenario that has it—even if its `run.json` is unreadable or malformed, as long as the folder path itself is safe—with a `::warning::` per removed folder, and all other restored runs are kept byte for byte. An ID that is not in the restored history only warns. Excluding never applies to a run added by the same build: if a `runs-dir` folder has an excluded ID, the build fails. Once the deployed site no longer has that run, remove the `exclude-runs` entry from the workflow.
2. **Last resort: one deploy with `reset_history`.**
   Unsafe archive paths during restore (such as symlinks, `..`, or archive size and entry caps) and unsafe restored run paths stay fail-closed before `exclude-runs` can apply. For those errors, or when the GitHub Pages artifact has expired, run the Documentation workflow on `main` (`workflow_dispatch`) with `reset_history` set, plus the current release version, tag, and commit. `reset_history` defaults to false, and a normal release passes false, so this is one deploy rather than a committed setting. That deploy skips restored history and drops the published run list. Later releases accumulate history again for as long as the Pages artifact is kept.

The docs deploy keeps that Pages artifact for 90 days (`retention-days: 90`). GitHub clamps the value to the repository or organization maximum when that maximum is lower. Restore refuses to publish when the artifact is gone and the live site still has reports. The artifact published with v3.9.1 expires 29 November 2026 at 08:34 AEDT. The only live G1 run is `46070167027`, so one reset at that point drops nothing else.
