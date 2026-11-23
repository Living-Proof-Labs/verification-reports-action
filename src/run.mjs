// SPDX-License-Identifier: Apache-2.0
import { appendFileSync, mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';

export function runAction(env = process.env) {
  const pinsPath = (env.INPUT_PINS_PATH ?? 'pins.json').trim();
  const robotManifest = (env.INPUT_ROBOT_MANIFEST ?? 'robot.json').trim();
  const runnerConfig = (env.INPUT_RUNNER_CONFIG ?? 'runner.json').trim();
  const outputDir = (env.INPUT_OUTPUT_DIR ?? '').trim();

  if (!outputDir) {
    throw new Error(
      'output-dir must be a non-empty directory path. Pass inputs.output-dir or use the default (artifacts/verification-report).'
    );
  }

  mkdirSync(outputDir, { recursive: true });
  const reportPath = path.join(outputDir, 'report.json');
  const payload = {
    schema_version: '0.0.0-placeholder',
    status: 'placeholder',
    note: 'Template implementation. Provenance checks and report generation are tracked in Living-Proof-Labs/.github#164.',
    inputs: {
      pins_path: pinsPath,
      robot_manifest: robotManifest,
      runner_config: runnerConfig,
    },
  };
  writeFileSync(reportPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');

  if (env.GITHUB_OUTPUT) {
    appendFileSync(env.GITHUB_OUTPUT, `report-path=${reportPath}\nstatus=placeholder\n`, 'utf8');
  }

  return { reportPath, status: 'placeholder', payload };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    const result = runAction();
    console.log(`Living Proof Verification Reports (placeholder): wrote ${result.reportPath}`);
  } catch (error) {
    console.error(error.message);
    process.exit(1);
  }
}
