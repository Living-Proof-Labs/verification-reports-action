// SPDX-License-Identifier: Apache-2.0
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';

const requiredFiles = [
  'AGENTS.md',
  'LICENSE',
  'README.md',
  'Taskfile.yml',
  'action.yml',
  'docs/decisions/0001-composite-vs-reusable-workflow.md',
  'docs/provenance.md',
  '.github/workflows/report.yml',
  'package.json',
  'renovate.json',
  'src/run.mjs',
  'src/plan.mjs',
  'src/collect.mjs',
  'src/fixture-run.mjs',
  'tests/action.test.mjs',
  'tests/fixtures/robot/verifications/fixture-hallway.toml',
  'tests/fixtures/robot/verifications/fixture-scenario.toml',
];

for (const file of requiredFiles) {
  if (!existsSync(file)) {
    throw new Error(`Missing required repository file: ${file}`);
  }
}

const actionYml = readFileSync('action.yml', 'utf8');
for (const key of ['name:', 'description:', 'inputs:', 'outputs:', 'runs:', 'using: composite']) {
  if (!actionYml.includes(key)) {
    throw new Error(`action.yml is missing required field: ${key}`);
  }
}

for (const directory of ['src', 'tests', 'tools']) {
  for (const name of readdirSync(directory)) {
    if (!name.endsWith('.mjs')) continue;
    execFileSync(process.execPath, ['--check', `${directory}/${name}`], { stdio: 'inherit' });
  }
}

console.log('Static checks passed: repository files, action.yml metadata, and JavaScript syntax are valid.');
