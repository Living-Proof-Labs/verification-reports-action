// SPDX-License-Identifier: Apache-2.0
import { mkdirSync, rmSync } from 'node:fs';
import path from 'node:path';
import { ReportError, planDefinitions } from './definitions.mjs';
import { runFixtureDefinition } from './fixture.mjs';
import { writeGithubOutput } from './github-output.mjs';
import { gradeMap, outputsFromGrades } from './report.mjs';

const WORKFLOW_GUIDANCE =
  'This composite action cannot call capability-verifier walk-hallway.yml or scenario.yml. Those workflows use matrices, containers and separate runners. Call .github/workflows/report.yml instead.';

export function runAction(env = process.env, root = process.cwd()) {
  const outputDir = (env.INPUT_OUTPUT_DIR ?? '').trim();
  if (!outputDir) {
    throw new ReportError(
      'output-dir must be a non-empty directory path. Pass inputs.output-dir or use the default (artifacts/verification-report).'
    );
  }
  const version = (env.INPUT_VERSION ?? '').trim();
  const plan = planDefinitions(
    {
      definitions: env.INPUT_DEFINITIONS,
      repository: env.INPUT_REPOSITORY,
      ref: env.INPUT_REF,
    },
    env,
    root
  );
  const verifier = plan.definitions.filter((item) => item.execution === 'capability-verifier');
  if (verifier.length > 0) {
    const names = verifier.map((item) => item.path).join(', ');
    throw new ReportError(`${WORKFLOW_GUIDANCE} Definitions that need the verifier: ${names}.`);
  }
  rmSync(outputDir, { recursive: true, force: true });
  mkdirSync(outputDir, { recursive: true });
  for (const item of plan.definitions) {
    try {
      runFixtureDefinition(item, { root, outputDir, env, version });
    } catch (error) {
      rmSync(path.join(outputDir, item.id), { recursive: true, force: true });
      rmSync(path.join(outputDir, `.partial-${item.id}`), { recursive: true, force: true });
      throw error;
    }
  }
  const grades = gradeMap(outputDir, plan);
  const outputs = outputsFromGrades(grades, outputDir);
  writeGithubOutput(env, outputs);
  return { reportPath: outputDir, status: outputs.status, grade: outputs.grade, grades, outputs };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    const result = runAction();
    console.log(`Living Proof Verification Reports: wrote ${result.reportPath} (${result.status})`);
  } catch (error) {
    console.error(error.message);
    process.exit(1);
  }
}
