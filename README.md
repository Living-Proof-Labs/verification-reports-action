# verification-reports-action

Reusable **Living Proof Verification Reports** GitHub Action. Runs robot capability verification for a pinned robot configuration and produces a traceable verification report.

Tracked in [Living-Proof-Labs/.github#164](https://github.com/Living-Proof-Labs/.github/issues/164) (repository setup in [Living-Proof-Labs/.github#165](https://github.com/Living-Proof-Labs/.github/issues/165)).

## Status

This repository currently provides a runnable placeholder template (`action.yml` and `src/run.mjs`) to validate repository onboarding, testing, and release automation. Provenance verification, simulator execution, and full report rendering are tracked in child issues of [Living-Proof-Labs/.github#164](https://github.com/Living-Proof-Labs/.github/issues/164).

## Intended inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `pins-path` | No | `pins.json` | Path to the robot pins manifest. |
| `robot-manifest` | No | `robot.json` | Path to the robot manifest. |
| `runner-config` | No | `runner.json` | Path to the runner configuration. |
| `output-dir` | No | `artifacts/verification-report` | Directory where report artifacts are written. |

## Intended outputs

| Output | Description |
| --- | --- |
| `report-path` | Path to the generated verification report file. |
| `status` | Execution status of the verification report action (`placeholder` in this template). |

## Example usage

Once released, reference a published release tag:

```yaml
- uses: Living-Proof-Labs/verification-reports-action@v1.0.0
  with:
    pins-path: pins.json
    robot-manifest: robot.json
    runner-config: runner.json
    output-dir: artifacts/verification-report
```

## Development

Prerequisites: Node.js 22+ and [Task](https://taskfile.dev/) 3.54.0.

```sh
task setup
task verify
```

## License

[Apache-2.0](LICENSE).
