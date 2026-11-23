// SPDX-License-Identifier: Apache-2.0
import { existsSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';

const requiredFiles = [
  'AGENTS.md',
  'LICENSE',
  'README.md',
  'Taskfile.yml',
  'action.yml',
  'package.json',
  'renovate.json',
  'src/run.mjs',
  'tests/action.test.mjs',
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

for (const script of ['src/run.mjs', 'tools/check.mjs', 'tests/action.test.mjs']) {
  execFileSync(process.execPath, ['--check', script], { stdio: 'inherit' });
}

console.log('Static checks passed: repository files, action.yml metadata, and JavaScript syntax are valid.');
