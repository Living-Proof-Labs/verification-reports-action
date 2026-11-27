// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { VERIFIER_WORKFLOW_SHA, VERIFIER_WORKFLOW_TAG } from '../src/definitions.mjs';
import { EVALUATORS_SENTINEL, evaluatorsDeclaration, readEvaluatorsFile, resolveCallerEvaluators } from '../src/evaluators.mjs';
import { writeGithubOutput } from '../src/github-output.mjs';
import { writePlan } from '../src/plan.mjs';

const ROOT = new URL('..', import.meta.url);
const LINE = 'Living-Proof-Labs/livingproof-evaluator-run@v1.9.0/livingproof-evaluator.json';
const FORBIDDEN = ['livingproof-evaluator-run', 'livingproof_evaluator_run', 'livingproof-run-integrity'];
// Health and Scenario names stay in these files for this slice. Later slices shrink this list.
const HEALTH_AND_SCENARIO_ALLOWLIST = [
  'src/provenance.mjs',
  'src/reports_presentation.py',
  'src/reports_site.py',
];

function walk(relative) {
  const absolute = new URL(relative, ROOT);
  const found = [];
  const visit = (current, prefix) => {
    for (const name of readdirSync(current)) {
      const next = path.join(current, name);
      const rel = prefix ? `${prefix}/${name}` : name;
      if (statSync(next).isDirectory()) visit(next, rel);
      else found.push(rel);
    }
  };
  if (statSync(absolute).isDirectory()) visit(absolute.pathname, relative.replace(/\/$/, ''));
  else found.push(relative);
  return found;
}

test('evaluators.yml is one declaration line and an empty list stays a comment', () => {
  const raw = readFileSync(new URL('../evaluators.yml', import.meta.url), 'utf8');
  assert.equal(raw, `- ${LINE}\n`);
  const declared = readEvaluatorsFile(new URL('../evaluators.yml', import.meta.url));
  assert.deepEqual(declared.lines, [LINE]);
  assert.equal(declared.run, true);
  assert.equal(declared.input, LINE);
  const removed = evaluatorsDeclaration(readFileSync(new URL('../evaluators.yml', import.meta.url), 'utf8').replace(LINE, ''));
  assert.deepEqual(removed.lines, []);
  assert.equal(removed.run, false);
  assert.equal(removed.input, EVALUATORS_SENTINEL);
  assert.notEqual(removed.input, '');
  assert.match(removed.input, /^#/);
  assert.equal(evaluatorsDeclaration('').input, EVALUATORS_SENTINEL);
  assert.equal(evaluatorsDeclaration('# only a comment\n').input, EVALUATORS_SENTINEL);
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
  assert.match(collect, new RegExp(`ref: ${VERIFIER_WORKFLOW_SHA}`));
  assert.match(
    collect,
    /uses: astral-sh\/setup-uv@d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86 # v5\.4\.2\n {8}with:/
  );
  const upload = collect.indexOf('actions/upload-artifact');
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
  assert.equal(readFileSync(path.join(root, 'evaluators.yml'), 'utf8').includes('@v1.9.0/'), true);
  const planSource = readFileSync(new URL('../src/plan.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(planSource, /evaluators\.yml/);
  const absent = resolveCallerEvaluators('', root);
  assert.deepEqual(absent.lines, []);
  assert.equal(absent.input, EVALUATORS_SENTINEL);
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
    assert.equal(planned.outputs.evaluators, EVALUATORS_SENTINEL);
    assert.equal(planned.outputs.evaluators_run, 'false');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('a caller path or an inline list is the declaration', () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const fromFile = resolveCallerEvaluators('evaluators.yml', root);
  assert.deepEqual(fromFile.lines, [LINE]);
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

test('an inline empty list is no evaluators', () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const emptyList = resolveCallerEvaluators('[]', root);
  assert.deepEqual(emptyList.lines, []);
  assert.equal(emptyList.run, false);
  assert.equal(emptyList.input, EVALUATORS_SENTINEL);
});

test('action code does not name the run evaluator', () => {
  const hits = [];
  for (const relative of ['src', 'action.yml', '.github/workflows']) {
    for (const file of walk(relative)) {
      const text = readFileSync(new URL(`../${file}`, import.meta.url), 'utf8');
      for (const name of FORBIDDEN) {
        if (text.includes(name)) hits.push(`${file}: ${name}`);
      }
    }
  }
  assert.deepEqual(hits, []);
  assert.deepEqual(HEALTH_AND_SCENARIO_ALLOWLIST, [
    'src/provenance.mjs',
    'src/reports_presentation.py',
    'src/reports_site.py',
  ]);
  const markers = {
    'src/provenance.mjs': 'livingproof-evaluator-scenario',
    'src/reports_presentation.py': 'livingproof-health',
    'src/reports_site.py': 'livingproof-health',
  };
  for (const [file, marker] of Object.entries(markers)) {
    const text = readFileSync(new URL(`../${file}`, import.meta.url), 'utf8');
    assert.match(text, new RegExp(marker));
    for (const name of FORBIDDEN) assert.equal(text.includes(name), false, file);
  }
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
