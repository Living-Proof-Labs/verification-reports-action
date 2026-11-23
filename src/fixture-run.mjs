// SPDX-License-Identifier: Apache-2.0
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { ReportError } from './definitions.mjs';
import { runFixtureDefinition } from './fixture.mjs';

export function runFixtureCli(env = process.env, root = process.cwd()) {
  const planPath = env.INPUT_PLAN_PATH;
  const definitionPath = (env.INPUT_DEFINITION ?? '').trim();
  const outputDir = (env.INPUT_OUTPUT_DIR ?? '').trim();
  if (!planPath || !definitionPath || !outputDir) {
    throw new ReportError('fixture-run requires INPUT_PLAN_PATH, INPUT_DEFINITION and INPUT_OUTPUT_DIR');
  }
  const plan = JSON.parse(readFileSync(planPath, 'utf8'));
  const item = plan.definitions.find((entry) => entry.path === definitionPath);
  if (!item) throw new ReportError(`${definitionPath} is not in ${planPath}`);
  if (item.execution !== 'fixture') {
    throw new ReportError(`${definitionPath} is a ${item.execution} definition, not a fixture`);
  }
  return runFixtureDefinition(item, {
    root,
    outputDir,
    env,
    version: (env.INPUT_VERSION ?? '').trim(),
  });
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    const written = runFixtureCli();
    console.log(`Fixture definition wrote ${written}`);
  } catch (error) {
    console.error(error.message);
    process.exit(1);
  }
}
