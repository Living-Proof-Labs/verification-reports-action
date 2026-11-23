// SPDX-License-Identifier: Apache-2.0
import { mkdirSync, readFileSync, rmSync } from 'node:fs';
import path from 'node:path';
import { ReportError } from './definitions.mjs';
import { assembleVerifierDefinition, mergeFixtureArtifacts } from './assemble.mjs';
import { writeGithubOutput } from './github-output.mjs';
import { gradeMap, outputsFromGrades } from './report.mjs';

export function collectReports(env = process.env, root = process.cwd()) {
  const planPath = env.INPUT_PLAN_PATH;
  const outputDir = (env.INPUT_OUTPUT_DIR ?? '').trim();
  if (!planPath || !outputDir) {
    throw new ReportError('collect requires INPUT_PLAN_PATH and INPUT_OUTPUT_DIR');
  }
  const plan = JSON.parse(readFileSync(planPath, 'utf8'));
  const version = (env.INPUT_VERSION ?? '').trim();
  rmSync(outputDir, { recursive: true, force: true });
  mkdirSync(outputDir, { recursive: true });
  const fixtureRoot = env.INPUT_FIXTURE_ROOT
    ? path.resolve(root, env.INPUT_FIXTURE_ROOT)
    : path.join(root, 'downloaded', 'fixture');
  const verifierRoot = env.INPUT_VERIFIER_ROOT
    ? path.resolve(root, env.INPUT_VERIFIER_ROOT)
    : path.join(root, 'downloaded');
  mergeFixtureArtifacts(fixtureRoot, outputDir);
  for (const item of plan.definitions) {
    if (item.execution !== 'capability-verifier') continue;
    assembleVerifierDefinition(item, verifierRoot, outputDir, env, version);
  }
  const grades = gradeMap(outputDir, plan);
  const outputs = outputsFromGrades(grades, outputDir);
  writeGithubOutput(env, outputs);
  return { outputDir, grades, outputs };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    const result = collectReports();
    console.log(`Living Proof Verification Reports: wrote ${result.outputDir} (${result.outputs.status})`);
  } catch (error) {
    console.error(error.message);
    process.exit(1);
  }
}
