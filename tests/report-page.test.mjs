// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const docsYml = readFileSync(new URL('../.github/workflows/docs.yml', import.meta.url), 'utf8');
const releaseYml = readFileSync(new URL('../.github/workflows/release.yml', import.meta.url), 'utf8');
const readme = readFileSync(new URL('../README.md', import.meta.url), 'utf8');
const manual = readFileSync(new URL('../docs/reports.md', import.meta.url), 'utf8');
const mkdocs = readFileSync(new URL('../mkdocs.yml', import.meta.url), 'utf8');

const LIVE_REPORT = 'https://docs.livingprooflabs.com/verification-reports-action/reports/';

function jobBlock(text, name, next) {
  const start = text.indexOf(`\n  ${name}:\n`);
  const end = text.indexOf(`\n  ${next}:\n`);
  assert.ok(start !== -1 && end > start, `${name} job missing`);
  return text.slice(start, end);
}

test('the release manual publishes the Verify mock and G1 reports with ./site', () => {
  const production = jobBlock(docsYml, 'production', 'deploy');
  const download = production.indexOf('name: verification-report\n');
  const mockSite = production.indexOf('runs-dir: artifacts/smoke-report');
  const g1Download = production.indexOf('name: g1-verification-report\n');
  const g1Site = production.indexOf('runs-dir: artifacts/g1-smoke-report');
  const index = production.indexOf('test -f artifacts/site/reports/index.html');
  const g1Index = production.indexOf('test -f artifacts/site/reports/g1/index.html');
  const upload = production.indexOf('actions/upload-pages-artifact@');
  assert.ok(download !== -1 && mockSite !== -1 && g1Download !== -1 && g1Site !== -1);
  assert.ok(index !== -1 && g1Index !== -1 && upload !== -1);
  assert.ok(download < mockSite && mockSite < g1Download && g1Download < g1Site);
  assert.ok(g1Site < index && index < upload);
  assert.equal(production.split('uses: ./site').length - 1, 2);
  assert.match(production, /run-id: \$\{\{ github\.event\.workflow_run\.id \}\}/);
  assert.match(production, /output-dir: artifacts\/site\/reports\n/);
  assert.match(production, /output-dir: artifacts\/site\/reports\/g1/);
  assert.match(production, /reset-history: 'true'/);
  assert.match(production, /actions: read/);
  assert.match(releaseYml, /deploy-docs:[\s\S]*?actions: read/);
  assert.equal(readme.includes(LIVE_REPORT), true);
  assert.equal(manual.includes(LIVE_REPORT), true);
  assert.equal(
    manual.includes('https://docs.livingprooflabs.com/verification-reports-action/reports/g1/'),
    true
  );
  assert.match(mkdocs, /Reports: docs\/reports\.md/);
});
