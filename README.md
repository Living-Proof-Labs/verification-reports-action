# verification-reports-action

**Start here: [published manual](https://docs.livingprooflabs.com/verification-reports-action/).**
The manual is built from this README and the `docs/` folder by the Documentation workflow and deployed
to Pages after each release. Until the first post-merge deployment, build it locally with
`task -t Taskfile.runtime.yml docs:build` or download the pull request's documentation artifact.

Reusable **Living Proof Verification Reports** workflow and site publisher. A robot repository calls the workflow to get one verification report for the definitions in that repository, and uses the `site` step to publish a single-robot `reports/` section inside its documentation.

Tracked in [Living-Proof-Labs/.github#164](https://github.com/Living-Proof-Labs/.github/issues/164). The workflow interface choice is recorded in [docs/decisions/0001-composite-vs-reusable-workflow.md](docs/decisions/0001-composite-vs-reusable-workflow.md).

## Reusable workflow

This is a reusable workflow, `.github/workflows/report.yml`, because the runs go through capability-verifier's `walk-hallway.yml` and `scenario.yml`. Those workflows use matrices, container images and separate runners. A composite action is one step in the caller's job and cannot call them.

`action.yml` remains for this repository's fixture tests. Any other caller that uses the root composite action is told to call the workflow.

Robot repositories pin a semantic-release tag, or a full commit SHA with a `# vX.Y.Z` comment. The workflow passes no secrets. `GITHUB_TOKEN` is the credential. Every job has `contents: read`. The two jobs that call capability-verifier also have `pull-requests: write`, because `walk-hallway.yml`'s `scenario` job and `scenario.yml`'s `comment` job declare it. With `comment: false` that job never writes. Callers of this workflow grant the same pair on the calling job. `merge_group` works when the caller workflow listens for it; the robot commit is that event's SHA. The workflow checks its own scripts out from `job.workflow_repository` at `job.workflow_sha` and checks the caller out separately for definitions and locks.

The caller's token must be able to read the workflow repository. Plan, fixture, and collect check those scripts out with the caller's `GITHUB_TOKEN` (`contents: read` is enough when this repository is public or the token has been granted access). If the token cannot read the workflow repository, the Workflow source checkout fails before any definition is planned and no verification report is written. The failure is a checkout error: the repository was not found, or the token does not have permission to read it.

A robot `FAIL` or `INSUFFICIENT_EVIDENCE` is a successful run. `status` is `ok` when the report was written. `grade` is the robot grade for a single result, and `grades` is a JSON map of `definition/configuration` to grade. The files are uploaded as the `verification-report` artifact. Callers download that artifact; the files are not left in the caller job's workspace.

## Pinned inputs

Before any trial, the plan job resolves each input from its release tag and refuses the run when the check fails. The message names the input and the expected and found values. No `report.json` is written, and `walk-hallway` and `scenario` do not start, because both need the plan job. The inputs are capability-verifier, livingproof-sim-mujoco, robot-capability-specs, the livingproof-worlds world, livingproof-evaluator-scenario, livingproof-evaluator-run, and livingproof-capability-report together with its image.

The tag is the pin people edit. The plan resolves each tag, then passes four values into capability-verifier v2.12.5: `verifier-commit`, `verifier-image`, `reporter-image`, and `evaluator-commit`. Those values are the commits and image refs the plan already checked. The lock must not carry the fields they replace. A present field is refused before `plan.json` is written:

- `releases.verifier.commit`, `releases.verifier.image`, and `releases.verifier.digest`
- `releases.reporter.image` and `releases.reporter.digest`
- `evaluators.livingproof-evaluator-scenario.commit`

A one-line edit of the verifier tag, or of the livingproof-evaluator-scenario tag, needs no relock of those fields. The trial runs the commit and image the new tag resolved to.

These fields stay in the lock. capability-verifier v2.12.5 still reads them, and the plan refuses when one disagrees with the tag it resolved:

- `releases.runner.commit`
- `releases.specs.commit` and `releases.specs.files`
- `world.commit`, `world.sha256`, and `sources.world.sha256`
- `sources.parameters.sha256`
- `releases.reporter.commit`

A one-line edit of the runner, specs, or world tag that leaves the old commit or hashes in place is refused. `releases.reporter.commit` stays for the same reason: v2.12.5 does not take a reporter commit input, and `repin --check` still requires that commit. `sources.parameters.sha256` is the hash of `specs/scenarios/<scenario id>/parameters.json` at the specs commit. `sources.parameters.path` is the path in the robot repository and is not hashed.

## Migration

<a id="migration"></a>

Every lock published before this release is refused before a trial. Drop these six fields:

- `releases.verifier.commit`
- `releases.verifier.image`
- `releases.verifier.digest`
- `releases.reporter.image`
- `releases.reporter.digest`
- `evaluators.livingproof-evaluator-scenario.commit`

Set `releases.verifier.tag` to `v2.12.5`. The plan resolves that tag and passes the commit and the image. Leave the kept fields in place: `releases.runner.commit`, `releases.specs.commit`, `releases.specs.files`, `world.commit`, `world.sha256`, `sources.world.sha256`, `sources.parameters.sha256`, and `releases.reporter.commit`.

`report.json` and `run.json` each record `inputs`: repository, tag, commit, digest, and `status` for every pin. The same status line is written into `report.md`, `report.html`, and `report.txt` under "Pinned inputs", as `name: status (meaning)`.

| Status | Meaning |
| --- | --- |
| `verified` | An attestation was published and its subject digest, source repository, and commit all matched. |
| `not-published` | The attestations API returned HTTP 404. The commit was checked against the release instead. |
| `resolved` | No attestation was published and the release named no commit. The peeled tag is what the report records. |
| `fixture` | This repository's own fixture. The release was not checked. |

`verified` is the status that means the attestation matched. The other three stay visible so a reader can tell a verified pin from one that was not. A published attestation that does not match the digest, the source repository, or the commit fails the run and writes no report. Any attestations response other than HTTP 200 or HTTP 404 fails the run. A missing GitHub Release, or a tag that does not peel to a commit, fails the run as well. That 404 is not recorded as `not-published`.

`status: resolved` can sit beside `attestation: not-published` on the same input. The attestation field means the attestations API returned HTTP 404. `resolved` means the release also named no commit, so the report records the peeled tag. That pin is not verified. `status: not-published` with the same attestation field means HTTP 404 as well, and the commit was checked against the release.

Source pins (livingproof-sim-mujoco and both evaluators) use the sha256 of the codeload archive of the peeled commit as the digest they ask GitHub to attest. A specs pin with more than one file uses a synthetic sha256 of those file hashes. Neither value is a subject a real attestation names, so those pins stay `not-published` until a later change asks for a subject the release actually publishes. An image pin uses the registry digest, and a single-file pin uses that file's sha256. Those can match.

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
| `output-dir` | No | `artifacts/verification-report` | Directory collected into the report artifact. |
| `artifact-name` | No | `verification-report` | Name of the uploaded report artifact. A second call in the same workflow needs a different name. |
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

## Reports section step (`site/action.yml`)

Use `Living-Proof-Labs/verification-reports-action/site` after downloading the `verification-report` artifact so a robot repository's documentation build can include `reports/` directly.

### Generated `reports/` layout

- `reports/index.html`: single-robot index of the latest result per scenario ordered with failures first, plus a link to [archived verification reports](https://docs.livingprooflabs.com/livingproof-verification/reports/index.html) for earlier history.
- `reports/<robot>/<variant>/index.html`: scenario run history ordered newest first, with a `Latest report` link (`latest/`).
- `reports/<robot>/<variant>/runs/<run-id>/`: immutable per-run directory holding the scrubbed report pages, `run.json`, `SHA256SUMS`, grading inputs, and retained evidence.
- `reports/site.json`: machine-readable publication marker.

A run folder's `SHA256SUMS` can be recomputed by whoever wrote the folder, so the site checks what it publishes itself:

- **Report pages**: each `report.html` and run `index.html` must fit an allowlist of the reporter's own markup, checked with an HTML parser. Allowed links are `https://` or relative, CSS may only embed fonts, and scripts must be the reporter's own, pinned by SHA-256. A page that fits is published byte for byte. Anything else fails the build, except an unknown reporter script, which is removed with a warning. A page larger than 8 MiB (8,388,608 bytes of UTF-8) fails the build before it is parsed. That is more than ten times the largest page the reporter has published. Deeply nested markup does not slow the check down: its cost grows with the page size, not with the square of the nesting depth.
- **Evidence**: files a browser would run, such as `.html`, `.htm`, `.xhtml`, `.svg`, `.xml` or `.js`, are not copied.

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
| `output-dir` | No | `reports` | Destination path for the static `reports/` tree (for example `artifacts/docs/html/reports`). It is resolved inside `GITHUB_WORKSPACE`, with symlinks followed. Paths that escape the workspace, name the workspace root, or contain `..`, `.git` or a control character are refused. |
| `evidence-retention-days` | No | `7` | Days to retain copied MCAP, video, and media files before pruning. |
| `restore-history` | No | `auto` | Restore earlier runs from the last deployed Pages artifact (`auto`, `true`, or `false`). |
| `site-url` | No | empty | Optional live documentation URL checked when no Pages artifact is listed. |
| `branch` | No | `main` | Branch whose deployed Pages artifacts hold earlier runs. |
| `pages-artifact` | No | empty | Exact Pages artifact name (matches `github-pages` and `github-pages-*` when empty). |
| `pages-job` | No | empty | Pages deploy job name (matches `pages`, `deploy`, and `deploy-pages` when empty). |
| `reset-history` | No | `false` | Start fresh without restoring earlier runs (`true` or `false`). |
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
   Add the `<run-id>` folder name from the error message to `exclude-runs` in the workflow's `site` step (whitespace- or comma-separated), then run the site build on a branch that deploys Pages (a pull request build is artifact-only, so it does not change what later builds restore). The run folder is dropped from the restored tree in every scenario that has it, with a `::warning::` per removed folder, and all other restored runs are kept byte for byte. An ID that is not in the restored history only warns. Excluding never applies to a run added by the same build: if a `runs-dir` folder has an excluded ID, the build fails. Once the deployed site no longer has that run, remove the `exclude-runs` entry from the workflow.
2. **Last resort: `reset-history: true`.**
   If history cannot be recovered selectively, set `reset-history: true` for one deployment to start a fresh `reports/` tree without restoring earlier runs, then remove it. This drops all previously published run history.

## Release

`Verify` runs on pull requests, merge groups, and pushes to `main`. Pull requests into `main` get a unique candidate version from `semantic-release-candidate.yml`. The mock robot smoke runs the report workflow on this repository's fixture definitions and uploads `smoke-report-<version>` from the `verification-report` artifact. The real G1 smoke runs the report workflow on the Unitree G1 stock MuJoCo hallway, `Living-Proof-Labs/livingproof-unitree-g1` at `v2.3.34`, with one trial, through capability-verifier and livingproof-evaluator-run. It uploads `g1-smoke-report-<version>` from the `g1-verification-report` artifact. The site smoke job builds the static `reports/` section from the mock report.

After `Verify` succeeds for a push to `main`, `Release` runs. The `release` job tags that commit and publishes the GitHub release. `post-release` runs only when `release` publishes (`published == 'true'`), calls this workflow on the fixture definitions in `tests/fixtures/robot/verifications/` with the published `version`, and `post-release-check` checks out `git-tag`.

## Development

Prerequisites: Node.js 22+, Python 3.12, [uv](https://docs.astral.sh/uv/), and [Task](https://taskfile.dev/) 3.54.0.

```sh
task setup
task verify
```

## License

[Apache-2.0](LICENSE).
