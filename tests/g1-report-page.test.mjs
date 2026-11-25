// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const docsYml = readFileSync(new URL('../.github/workflows/docs.yml', import.meta.url), 'utf8');
const releaseYml = readFileSync(new URL('../.github/workflows/release.yml', import.meta.url), 'utf8');
const readme = readFileSync(new URL('../README.md', import.meta.url), 'utf8');
const manual = readFileSync(new URL('../docs/live-g1-report.md', import.meta.url), 'utf8');
const mkdocs = readFileSync(new URL('../mkdocs.yml', import.meta.url), 'utf8');

const LIVE_REPORT = 'https://docs.livingprooflabs.com/verification-reports-action/reports/';

function jobBlock(text, name, next) {
  const start = text.indexOf(`\n  ${name}:\n`);
  const end = text.indexOf(`\n  ${next}:\n`);
  assert.ok(start !== -1 && end > start, `${name} job missing`);
  return text.slice(start, end);
}

test('the release manual publishes the Verify run G1 report with ./site', () => {
  const production = jobBlock(docsYml, 'production', 'deploy');
  const download = production.indexOf('name: g1-verification-report');
  const site = production.indexOf('uses: ./site');
  const index = production.indexOf('test -f artifacts/site/reports/index.html');
  const upload = production.indexOf('actions/upload-pages-artifact@');
  assert.ok(download !== -1 && site !== -1 && index !== -1 && upload !== -1);
  assert.ok(download < site && site < index && index < upload);
  assert.match(production, /run-id: \$\{\{ github\.event\.workflow_run\.id \}\}/);
  assert.match(production, /output-dir: artifacts\/site\/reports/);
  assert.match(production, /reset-history: 'true'/);
  assert.match(production, /actions: read/);
  assert.match(releaseYml, /deploy-docs:[\s\S]*?actions: read/);
  assert.equal(readme.includes(LIVE_REPORT), true);
  assert.equal(manual.includes(LIVE_REPORT), true);
  assert.match(mkdocs, /Live G1 report: docs\/live-g1-report\.md/);
});
