// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { VERIFIER_API_SHA, VERIFIER_API_TAG, VERIFIER_WORKFLOW_SHA, VERIFIER_WORKFLOW_TAG } from '../src/definitions.mjs';
import * as evaluators from '../src/evaluators.mjs';
import { evaluatorsDeclaration, readEvaluatorsFile, resolveCallerEvaluators } from '../src/evaluators.mjs';
import { writeGithubOutput } from '../src/github-output.mjs';
import { writePlan } from '../src/plan.mjs';

const ROOT = new URL('..', import.meta.url);
const SCENARIO_LINE = 'Living-Proof-Labs/livingproof-evaluator-scenario@v1.6.0/livingproof-evaluator.json';
const LINE = 'Living-Proof-Labs/livingproof-evaluator-run@v1.9.2/livingproof-evaluator.json';
const HEALTH_LINE = 'Living-Proof-Labs/livingproof-evaluator-health@v1.2.0/livingproof-evaluator.json';
const LINES = [SCENARIO_LINE, LINE, HEALTH_LINE];
const FORBIDDEN = [
  'livingproof-evaluator-run',
  'livingproof_evaluator_run',
  'livingproof-run-integrity',
  'livingproof-evaluator-scenario',
  'livingproof_evaluator_scenario',
  'livingproof-scenario',
];
const HEALTH_NAMES = [
  'livingproof-health',
  'livingproof-evaluator-health',
  'livingproof_evaluator_health',
  'health.json',
];
// No action file may name an evaluator. The allowlist is empty.
const NAME_ALLOWLIST = [];

function walk(relative) {
  const absolute = new URL(relative, ROOT);
  const found = [];
  const visit = (current, prefix) => {
    for (const name of readdirSync(current)) {
      const next = path.join(current, name);
      const rel = prefix ? `${prefix}/${name}` : name;
      if (name === '__pycache__' || name === 'node_modules') continue;
      if (statSync(next).isDirectory()) visit(next, rel);
      else found.push(rel);
    }
  };
  if (statSync(absolute).isDirectory()) visit(absolute.pathname, relative.replace(/\/$/, ''));
  else found.push(relative);
  return found;
}

test('evaluators.yml is one line per evaluator and an empty list stays a comment', () => {
  const raw = readFileSync(new URL('../evaluators.yml', import.meta.url), 'utf8');
  assert.equal(raw, LINES.map((line) => `- ${line}`).join('\n') + '\n');
  const declared = readEvaluatorsFile(new URL('../evaluators.yml', import.meta.url));
  assert.deepEqual(declared.lines, LINES);
  assert.equal(declared.run, true);
  assert.equal(declared.input, LINES.join('\n'));
  let emptied = raw;
  for (const line of LINES) emptied = emptied.replace(line, '');
  const removed = evaluatorsDeclaration(emptied);
  assert.deepEqual(removed.lines, []);
  assert.equal(removed.run, false);
  assert.equal(removed.input, '');
  assert.equal(evaluatorsDeclaration('').input, '');
  assert.equal(evaluatorsDeclaration('# only a comment\n').input, '');
  assert.equal(evaluatorsDeclaration('# only a comment\n').run, false);
});

test('an empty declaration is the empty string: capability-verifier v3 reads that as no evaluators', () => {
  // v3 runs no evaluators for an empty input and refuses a comment-only one.
  assert.equal('EVALUATORS_SENTINEL' in evaluators, false);
  const report = readFileSync(new URL('../.github/workflows/report.yml', import.meta.url), 'utf8');
  assert.doesNotMatch(report, /comment is passed/);
  assert.doesNotMatch(readFileSync(new URL('../src/evaluators.mjs', import.meta.url), 'utf8'), /sentinel/i);
  assert.equal(resolveCallerEvaluators('[]', fileURLToPath(new URL('..', import.meta.url))).input, '');
});

test('the report workflow passes the plan lines and still allows fifteen minutes', () => {
  const report = readFileSync(new URL('../.github/workflows/report.yml', import.meta.url), 'utf8');
  const walk = report.slice(report.indexOf('\n  walk-hallway:\n'), report.indexOf('\n  scenario:\n'));
  const scenario = report.slice(report.indexOf('\n  scenario:\n'), report.indexOf('\n  collect:\n'));
  const collect = report.slice(report.indexOf('\n  collect:\n'));
  for (const block of [walk, scenario]) {
    assert.match(block, /evaluators: \$\{\{ needs\.plan\.outputs\.evaluators \}\}/);
    assert.doesNotMatch(block, /evaluators:\s*''/);
    assert.match(block, new RegExp(`${VERIFIER_WORKFLOW_SHA} # ${VERIFIER_WORKFLOW_TAG.replaceAll('.', '\\.')}`));
  }
  assert.match(report, /evaluators: \$\{\{ steps\.plan\.outputs\.evaluators \}\}/);
  assert.match(report, /evaluators_run: \$\{\{ steps\.plan\.outputs\.evaluators_run \}\}/);
  assert.match(collect, /timeout-minutes: 15/);
  assert.match(collect, /uv sync --locked/);
  assert.match(collect, /action\/src\/evaluators_run\.py/);
  assert.match(collect, new RegExp(`ref: ${VERIFIER_API_SHA} # ${VERIFIER_API_TAG.replaceAll('.', '\\.')}`));
  assert.match(
    collect,
    /uses: astral-sh\/setup-uv@d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86 # v5\.4\.2\n {8}if: needs\.plan\.outputs\.evaluators_run == 'true'\n {8}with:/
  );
  const upload = collect.lastIndexOf('actions/upload-artifact');
  const fail = collect.indexOf('Fail when an evaluator declaration produced no provenance');
  assert.ok(upload !== -1 && fail > upload);
  const uploadBlock = collect.slice(upload, fail);
  assert.doesNotMatch(uploadBlock, /\n {8}if:/);
  const failBlock = collect.slice(fail, fail + 500);
  assert.match(failBlock, /if: steps\.evaluators\.outputs\.failed == 'true'/);
  assert.match(failBlock, /run: exit 1/);
  const evaluatorsStep = collect.slice(collect.indexOf('- id: evaluators'), collect.indexOf('- id: collect'));
  assert.doesNotMatch(evaluatorsStep, /continue-on-error/);
  assert.match(collect, /pattern: vra-\*-evaluator-results-\*/);
  assert.match(report, /evaluators:[\s\S]*?default: ''/);
  assert.doesNotMatch(report, /default: evaluators\.yml/);
});

test('no evaluators input ignores this repository file', async () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  assert.equal(readFileSync(path.join(root, 'evaluators.yml'), 'utf8').includes('@v1.9.2/'), true);
  const planSource = readFileSync(new URL('../src/plan.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(planSource, /evaluators\.yml/);
  const absent = resolveCallerEvaluators('', root);
  assert.deepEqual(absent.lines, []);
  assert.equal(absent.input, '');
  assert.equal(absent.run, false);
  const dir = mkdtempSync(path.join(tmpdir(), 'vra-no-evaluators-'));
  try {
    const planned = await writePlan(
      {
        GITHUB_REPOSITORY: 'Living-Proof-Labs/verification-reports-action',
        GITHUB_SHA: 'a'.repeat(40),
        WORKFLOW_REPOSITORY: 'Living-Proof-Labs/verification-reports-action',
        INPUT_DEFINITIONS: 'tests/fixtures/robot/verifications/*.toml',
        INPUT_PLAN_PATH: path.join(dir, 'plan.json'),
      },
      root,
      { async resolve() { throw new Error('pins are not resolved for a fixture plan'); } },
    );
    assert.deepEqual(planned.plan.evaluators.lines, []);
    assert.equal(planned.outputs.evaluators, '');
    assert.equal(planned.outputs.evaluators_run, 'false');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('a caller path or an inline list is the declaration', () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const fromFile = resolveCallerEvaluators('evaluators.yml', root);
  assert.deepEqual(fromFile.lines, LINES);
  assert.equal(fromFile.run, true);
  const inline = resolveCallerEvaluators(LINE, root);
  assert.deepEqual(inline.lines, [LINE]);
  const yaml = resolveCallerEvaluators(`- ${LINE}\n`, root);
  assert.deepEqual(yaml.lines, [LINE]);
  const verify = readFileSync(new URL('../.github/workflows/verify.yml', import.meta.url), 'utf8');
  const release = readFileSync(new URL('../.github/workflows/release.yml', import.meta.url), 'utf8');
  assert.match(verify, /g1-smoke:[\s\S]*?evaluators: evaluators\.yml/);
  assert.match(verify, /action-smoke:[\s\S]*?evaluators: evaluators\.yml/);
  assert.match(release, /evaluators: evaluators\.yml/);
});

test('advisory off re-runs collect on the same artifacts with non-advisory lines', () => {
  const verify = readFileSync(new URL('../.github/workflows/verify.yml', import.meta.url), 'utf8');
  const report = readFileSync(new URL('../.github/workflows/report.yml', import.meta.url), 'utf8');
  const smoke = verify.slice(verify.indexOf('\n  g1-smoke:\n'), verify.indexOf('\n  g1-smoke-check:\n'));
  const check = verify.slice(verify.indexOf('\n  g1-smoke-check:\n'));
  const collect = report.slice(report.indexOf('\n  collect:\n'));
  assert.match(smoke, /evaluators: evaluators\.yml/);
  assert.match(collect, /name: \$\{\{ inputs\.artifact-name \}\}-evaluated-reports/);
  assert.match(check, /python tools\/drop_advisory_lines\.py evaluators\.yml/);
  const drop = check.slice(
    check.indexOf('Keep the non-advisory declaration lines'),
    check.indexOf('- id: evaluators'),
  );
  assert.match(drop, /GITHUB_TOKEN: \$\{\{ github\.token \}\}/);
  assert.match(check, /src\/evaluators_run\.py/);
  assert.match(check, /node src\/collect\.mjs/);
  assert.match(check, /python tools\/compare_integrity\.py/);
  const verdicts = /python tools\/compare_integrity\.py \\\n\s+--verdicts tests\/fixtures\/g1\/scenario-verdicts\.json \\\n\s+--report (\S+)/g;
  const compared = [...check.matchAll(verdicts)].map((match) => match[1]);
  assert.deepEqual(compared, [
    'artifacts/smoke-report/unitree-g1-stock-mujoco-hallway/stock/report.json',
    'artifacts/advisory-off/unitree-g1-stock-mujoco-hallway/stock/report.json',
  ]);
  assert.match(check, /pattern: vra-\*-capability-report-\*/);
  assert.match(check, /pattern: vra-\*-verified-\*/);
  assert.match(check, /pattern: vra-\*-evidence-\*/);
  assert.doesNotMatch(check, /health/i);
  assert.doesNotMatch(verify, /health-smoke/);
});

test('an inline empty list is no evaluators', () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const emptyList = resolveCallerEvaluators('[]', root);
  assert.deepEqual(emptyList.lines, []);
  assert.equal(emptyList.run, false);
  assert.equal(emptyList.input, '');
});

function evaluatorNames(text) {
  const hits = [...FORBIDDEN, ...HEALTH_NAMES].filter((name) => text.includes(name));
  if (/\bhealth\b/i.test(text)) hits.push('health');
  return hits;
}

test('action code does not name an evaluator', () => {
  const hits = [];
  for (const relative of ['src', 'action.yml', '.github/workflows']) {
    for (const file of walk(relative)) {
      if (NAME_ALLOWLIST.includes(file)) continue;
      const text = readFileSync(new URL(`../${file}`, import.meta.url), 'utf8');
      for (const name of evaluatorNames(text)) hits.push(`${file}: ${name}`);
    }
  }
  assert.deepEqual(hits, []);
  assert.deepEqual(NAME_ALLOWLIST, []);
});

test('the names scan fails when action code names Scenario again', () => {
  assert.deepEqual(evaluatorNames("const pin = 'livingproof-evaluator-scenario';"), [
    'livingproof-evaluator-scenario',
  ]);
  assert.deepEqual(evaluatorNames('import livingproof_evaluator_scenario'), ['livingproof_evaluator_scenario']);
  assert.deepEqual(evaluatorNames("id: 'livingproof-scenario'"), ['livingproof-scenario']);
  assert.deepEqual(evaluatorNames('const pins = [];'), []);
});

test('removing the Scenario line removes it from what both trial workflows are given', () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const raw = readFileSync(new URL('../evaluators.yml', import.meta.url), 'utf8');
  const withLine = resolveCallerEvaluators(raw, root);
  assert.equal(withLine.lines.includes(SCENARIO_LINE), true);
  assert.equal(withLine.input.split('\n').includes(SCENARIO_LINE), true);
  const without = resolveCallerEvaluators(raw.replace(`- ${SCENARIO_LINE}\n`, ''), root);
  assert.equal(without.lines.includes(SCENARIO_LINE), false);
  assert.equal(without.input.includes('livingproof-evaluator-scenario'), false);
  assert.deepEqual(without.lines, [LINE, HEALTH_LINE]);
  const report = readFileSync(new URL('../.github/workflows/report.yml', import.meta.url), 'utf8');
  const walkBlock = report.slice(report.indexOf('\n  walk-hallway:\n'), report.indexOf('\n  scenario:\n'));
  const scenarioBlock = report.slice(report.indexOf('\n  scenario:\n'), report.indexOf('\n  collect:\n'));
  for (const block of [walkBlock, scenarioBlock]) {
    assert.match(block, /evaluators: \$\{\{ needs\.plan\.outputs\.evaluators \}\}/);
    assert.doesNotMatch(block, /evaluator-commit/);
  }
});

test('pins name capability-verifier v3.1.1 and the reporter v3.17.0', () => {
  const report = readFileSync(new URL('../.github/workflows/report.yml', import.meta.url), 'utf8');
  const verify = readFileSync(new URL('../.github/workflows/verify.yml', import.meta.url), 'utf8');
  const lock = JSON.parse(readFileSync(new URL('./smoke/g1/unitree-g1-stock-mujoco-hallway.lock.json', import.meta.url), 'utf8'));
  const sha = '5b702e5b2aa23338ba1d358692c6be96e97546cf';
  assert.equal(VERIFIER_WORKFLOW_SHA, sha);
  assert.equal(VERIFIER_WORKFLOW_TAG, 'v3.1.1');
  assert.equal(VERIFIER_API_SHA, sha);
  assert.equal(VERIFIER_API_TAG, 'v3.1.1');
  assert.match(report, new RegExp(`walk-hallway\\.yml@${sha} # v3\\.1\\.1`));
  assert.match(report, new RegExp(`scenario\\.yml@${sha} # v3\\.1\\.1`));
  assert.match(report, new RegExp(`ref: ${VERIFIER_API_SHA} # v3\\.1\\.1`));
  assert.match(verify, new RegExp(`ref: ${VERIFIER_API_SHA} # v3\\.1\\.1`));
  assert.equal(lock.releases.reporter.tag, 'v3.17.0');
  assert.equal(lock.releases.reporter.commit, '5d6aed964c0eea1094caeec24e797808b2d0c3fb');
  assert.equal(lock.releases.verifier.tag, 'v3.1.1');
});

test('a multiline plan output uses the github delimiter form', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'vra-output-'));
  try {
    const file = path.join(root, 'output.txt');
    writeFileSync(file, '');
    writeGithubOutput({ GITHUB_OUTPUT: file }, { evaluators: 'one\ntwo' });
    const text = readFileSync(file, 'utf8');
    assert.match(text, /^evaluators<<ghadelim_[0-9a-f]+\none\ntwo\nghadelim_[0-9a-f]+\n$/);
    assert.doesNotMatch(text, /contains a newline/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
