// SPDX-License-Identifier: Apache-2.0
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';

const requiredFiles = [
  'AGENTS.md',
  'LICENSE',
  'README.md',
  'Taskfile.yml',
  'action.yml',
  'site/action.yml',
  'docs/decisions/0001-composite-vs-reusable-workflow.md',
  'docs/provenance.md',
  '.github/workflows/report.yml',
  'package.json',
  'pyproject.toml',
  'uv.lock',
  'renovate.json',
  'src/run.mjs',
  'src/plan.mjs',
  'src/collect.mjs',
  'src/fixture-run.mjs',
  'src/reports_restore.py',
  'src/details_files.py',
  'src/site_action.py',
  'site/requirements.txt',
  'tools/require_reports.py',
  'tools/site_fixture.py',
  'tests/fixtures/site-golden/SHA256SUMS',
  'tests/action.test.mjs',
  'tests/collect.test.mjs',
  'tests/site-action.test.mjs',
  'tests/workflows.test.mjs',
  'tests/test_site.py',
  'tests/test_site_action_site_command.py',
  'tests/fixtures/robot/verifications/fixture-hallway.toml',
  'tests/fixtures/robot/verifications/fixture-scenario.toml',
];

for (const file of requiredFiles) {
  if (!existsSync(file)) {
    throw new Error(`Missing required repository file: ${file}`);
  }
}

for (const actionPath of ['action.yml', 'site/action.yml']) {
  const actionYml = readFileSync(actionPath, 'utf8');
  for (const key of ['name:', 'description:', 'inputs:', 'outputs:', 'runs:', 'using: composite']) {
    if (!actionYml.includes(key)) {
      throw new Error(`${actionPath} is missing required field: ${key}`);
    }
  }
}

for (const directory of ['src', 'tests', 'tools']) {
  for (const name of readdirSync(directory)) {
    if (!name.endsWith('.mjs')) continue;
    execFileSync(process.execPath, ['--check', `${directory}/${name}`], { stdio: 'inherit' });
  }
}

console.log(
  'Static checks passed: repository files, action metadata, and JavaScript syntax are valid.'
);
