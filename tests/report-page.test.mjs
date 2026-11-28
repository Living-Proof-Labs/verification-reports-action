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

test('the production pages job publishes the G1 report and not the fixture smoke', () => {
  const production = jobBlock(docsYml, 'production', 'deploy');
  const requireReports = readFileSync(new URL('../tools/require_reports.py', import.meta.url), 'utf8');
  assert.doesNotMatch(production, /src\/reports_site\.py restore/);
  const drop = production.indexOf('require_reports.py drop-fixtures artifacts/site/reports');
  const g1Download = production.indexOf('name: g1-verification-report\n');
  const g1Site = production.indexOf('runs-dir: artifacts/g1-smoke-report');
  const publish = production.indexOf('require_reports.py publish-index artifacts/site/reports');
  const check = production.indexOf('require_reports.py check artifacts/site/reports');
  const upload = production.indexOf('actions/upload-pages-artifact@');
  assert.ok(drop !== -1 && g1Download !== -1 && g1Site !== -1);
  assert.ok(publish !== -1 && check !== -1 && upload !== -1);
  assert.ok(g1Download < g1Site && g1Site < drop && drop < publish);
  assert.ok(g1Site < publish && publish < check && check < upload);
  assert.equal(production.split('uses: ./site').length - 1, 1);
  assert.doesNotMatch(production, /name: verification-report\n/);
  assert.doesNotMatch(production, /artifacts\/smoke-report/);
  assert.doesNotMatch(production, /require_reports\.py link/);
  assert.doesNotMatch(production, /MOCK_STATUS/);
  assert.doesNotMatch(production, /mock-site/);
  assert.doesNotMatch(production, /output-dir: artifacts\/site\/reports\n/);
  assert.doesNotMatch(requireReports, /missing the mock report/);
  assert.match(requireReports, /fixture-scenario-stock/);
  assert.match(requireReports, /fixture-hallway-stock/);
  assert.match(requireReports, /this repository's fixture/);
  assert.match(production, /G1_STATUS: \$\{\{ steps\.g1-site\.outcome \}\}/);
  assert.doesNotMatch(production, /test -f artifacts\/site\/reports/);
  assert.match(production, /EVENT_RUN: \$\{\{ github\.event\.workflow_run\.id \}\}/);
  assert.equal(production.split('run-id: ${{ steps.verify-run.outputs.id }}').length - 1, 1);
  assert.match(production, /output-dir: artifacts\/site\/reports\/g1/);
  const resetExpr = "${{ inputs.reset_history && 'true' || 'false' }}";
  assert.equal(production.split(resetExpr).length - 1, 1);
  assert.doesNotMatch(production, /reset-history: 'true'/);
  assert.doesNotMatch(production, /reset-history: 'false'/);
  const g1Inputs = production.slice(production.indexOf('runs-dir: artifacts/g1-smoke-report'));
  assert.match(g1Inputs, /restore-history: 'true'/);
  assert.match(g1Inputs, /restore-root: artifacts\/site\/reports/);
  assert.match(g1Inputs, /publication-approval: config\/viewers\/publication.json/);
  assert.match(g1Inputs, /trusted-viewers: config\/viewers\/evaluators.json/);
  assert.match(g1Inputs, /evaluators: evaluators.yml/);
  assert.doesNotMatch(g1Inputs, /restore-history: auto/);
  const pagesUpload = production.slice(production.indexOf('actions/upload-pages-artifact@'));
  const retention = pagesUpload.match(/retention-days:\s*(\d+)/);
  assert.ok(retention, 'pages artifact retention-days missing');
  assert.ok(Number(retention[1]) >= 90);
  assert.equal(retention[1], '90');
  const resetDefaults = [...docsYml.matchAll(/reset_history:[\s\S]*?\n\s+default: (true|false)/g)].map(
    (match) => match[1],
  );
  assert.deepEqual(resetDefaults, ['false', 'false']);
  assert.match(releaseYml, /reset_history: false/);
  assert.doesNotMatch(releaseYml, /reset_history: true/);
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
