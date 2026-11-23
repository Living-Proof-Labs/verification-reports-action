// SPDX-License-Identifier: Apache-2.0
import { existsSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';

const requiredFiles = [
  'AGENTS.md',
  'LICENSE',
  'README.md',
  'Taskfile.yml',
  'action.yml',
  'site/action.yml',
  'package.json',
  'pyproject.toml',
  'uv.lock',
  'renovate.json',
  'src/run.mjs',
  'src/reports_site.py',
  'src/site_action.py',
  'tools/site_fixture.py',
  'tests/action.test.mjs',
  'tests/site-action.test.mjs',
  'tests/workflows.test.mjs',
  'tests/test_site.py',
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

for (const script of [
  'src/run.mjs',
  'tools/check.mjs',
  'tests/action.test.mjs',
  'tests/site-action.test.mjs',
  'tests/workflows.test.mjs',
]) {
  execFileSync(process.execPath, ['--check', script], { stdio: 'inherit' });
}

console.log(
  'Static checks passed: repository files, action metadata, and JavaScript syntax are valid.'
);
