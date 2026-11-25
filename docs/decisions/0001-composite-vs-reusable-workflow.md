<!-- SPDX-License-Identifier: Apache-2.0 -->

# 0001. Composite action or reusable workflow

## Summary

The caller-facing interface is the reusable workflow `.github/workflows/report.yml`. A robot repository calls it at a semantic-release tag, or with a full commit SHA and a `# vX.Y.Z` comment. `walk-hallway` and `scenario` stay capability-verifier reusable workflows, which use matrices, containers and separate runners. A composite action is a single step and cannot call them. The default `GITHUB_TOKEN` is enough. The calling job grants `contents: read` and `pull-requests: write`, because capability-verifier's comment job declares the second and, with `comment: false`, never writes. `merge_group` works from the caller workflow, and a robot `FAIL` is a green run whose grade is an output. Report files come back as the `verification-report` artifact.

## Context

Living Proof Verification Reports replaces the central runs in [livingproof-verification](https://github.com/Living-Proof-Labs/livingproof-verification). That repository called capability-verifier at v2.11.4: `walk-hallway.yml` for hallway definitions and `scenario.yml` for scenario definitions. Each definition is a `livingproof-verification-definition/0.1.0` document with a `livingproof-verification-lock/0.2.0` lock. The robot repository that enables this workflow is the robot under test, at the commit GitHub checked out for the run.

`scenario.yml` is several jobs. The evidence, verify, evaluator, media and report jobs use matrices and container images. `walk-hallway.yml` is a one-job wrapper that calls `scenario.yml`. The report job writes `report.html`, `report.md`, `report.json`, `report.txt` and `SHA256SUMS`. A capability `FAIL` exits 0 there. Trial observations, MCAP and video are separate artifacts.

[va#4](https://github.com/Living-Proof-Labs/verification-reports-action/issues/4) checks pinned releases before trials and records the result on `report.json` and `run.json`. [va#5](https://github.com/Living-Proof-Labs/verification-reports-action/issues/5) builds the docs section from these runs. [va#6](https://github.com/Living-Proof-Labs/verification-reports-action/issues/6) documents the caller snippet, including an exact release pin.

## Options

### Composite action (`action.yml`)

Robot repositories would add a step after checkout:

```yaml
- uses: Living-Proof-Labs/verification-reports-action@<sha> # vX.Y.Z
  with:
    definitions: verifications/*.toml
```

Semantic-release tags and SHA pins work the same way they do for any action. Outputs are step outputs, and files written to `output-dir` stay in that job's workspace for the next step. `merge_group` works when the caller workflow listens for it. The job's `GITHUB_TOKEN` is the only credential, with whatever permissions the caller job set.

A composite action cannot declare a matrix, choose a runner, set job permissions, attach a service, or call a reusable workflow. Those are properties of `scenario.yml`. Re-implementing that pipeline inside one step would fork the verifier. Local `uses: ./` in `verify.yml` is easy to smoke-test, and it cannot exercise the matrices.

### Reusable workflow (`.github/workflows/report.yml`)

Robot repositories call a job:

```yaml
jobs:
  report:
    # capability-verifier's comment job declares pull-requests: write.
    # With comment: false it never writes.
    permissions:
      contents: read
      pull-requests: write
    uses: Living-Proof-Labs/verification-reports-action/.github/workflows/report.yml@<sha> # vX.Y.Z
    with:
      definitions: verifications/*.toml
```

The pin is the same semantic-release tag or commit SHA. GitHub does not allow an expression in `uses`, so this workflow's calls to capability-verifier are pinned to a commit in the workflow file. The lock still records the verifier commit the definition asked for. va#4 is the place that refuses a mismatch and replaces the static call; this workflow leaves that as a later job in front of the matrix, which is the seam va#4 needs.

The workflow calls `walk-hallway.yml` and `scenario.yml` and gives them matrices. Workflow-level permission stays `contents: read`. The two `uses` jobs also grant `pull-requests: write`, which those pinned workflows request on a job. GitHub checks that grant at startup, including when the job's `if` later skips it. No `secrets:` block is declared, so the called workflows receive `GITHUB_TOKEN` and nothing else. `merge_group` is a property of the robot repository's caller workflow; `github.event_name` and `github.sha` inside the called workflow are that caller's event and the checked-out commit. Definitions and locks come from a checkout of the caller. This workflow's scripts come from a checkout of `job.workflow_repository` at `job.workflow_sha`.

Outputs are workflow outputs (`status`, `grade`, `grades`, `report-path`). Files cross the job boundary as the `verification-report` artifact. Callers download it. va#5 can do that from a later job, or from a sibling workflow, without a fifth wrapper around `walk-hallway.yml`. Nesting allows ten reusable workflows; a robot workflow, this workflow, `walk-hallway.yml` and `scenario.yml` are four.

`verify.yml` calls the workflow with `uses: ./.github/workflows/report.yml`, which is the commit under test. That is the smoke test. It is a separate job from the steps that assert on the downloaded artifact.

## Decision

Ship `.github/workflows/report.yml` as the interface robot repositories call. Keep `action.yml` as a composite that runs this repository's fixture definitions for local tests and tells any other caller to use the workflow. The composite cannot call the verifier workflows, so it is not the robot-facing interface.

The robot under test is the caller at `github.sha`. A request to verify the caller at some other ref is rejected. Passing another repository requires that repository's full commit SHA, so a release tag cannot move under the run.

`comment` defaults to false and `media` defaults to true. Workflow permissions stay `contents: read`. The jobs that call capability-verifier also grant `pull-requests: write`, because the pinned `walk-hallway.yml` job `scenario` and `scenario.yml` job `comment` declare it. With `comment: false` that job never writes. Callers grant the same pair on the job that calls this workflow.

## Consequences

- Robot repositories call the workflow. va#6's snippet is a job-level `uses` with a SHA pin and a version comment, and that job grants `contents: read` plus `pull-requests: write`. The caller repository is checked out for definitions and locks. This workflow's `src/` is checked out from `job.workflow_repository` at `job.workflow_sha`, so a robot repository does not need those scripts.
- `status` is `ok` when the report files exist. `grade` is the robot grade for a single result, and `grades` is the map for every definition and configuration. `FAIL` and `INSUFFICIENT_EVIDENCE` keep `status` at `ok`.
- The report directory is the `verification-report` artifact. va#5 should download that artifact. `run.json` sits beside each configuration's report and records the tested commit, which is the caller SHA, plus the lock's declared robot and releases.
- The capability-verifier `uses` lines stay pinned to `e6a1ba347c525b62abf0644b610562b7bfb390d9` (`v2.12.5`) because GitHub rejects an expression in `uses`. The plan job resolves every other pin from its release tag and refuses before `walk-hallway` or `scenario` when the tag, commit, digest, or attestation does not match. It passes the resolved verifier commit, verifier image, reporter image, and scenario-evaluator commit into that release. A verifier tag that peels to any other commit is refused the same way. See [docs/provenance.md](../provenance.md).
- Verify runs a mock robot smoke and a real G1 smoke. The mock smoke uses fixture definitions under `tests/fixtures/robot/` and writes the report from the fixture's trial observations. The real G1 smoke runs the Unitree G1 stock hallway through capability-verifier. Post-release still calls the fixture definitions. Any other repository's definitions go to capability-verifier. va#5's Pages deploy stays a later workflow: this one does not request `pages` or `id-token`.
