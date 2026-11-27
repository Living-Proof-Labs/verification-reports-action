// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const root = new URL('..', import.meta.url);

function read(name) {
  return readFileSync(new URL(name, root), 'utf8');
}

function fencedExcerpt(readme, label) {
  const marker = `Excerpt from \`${label}\``;
  const at = readme.indexOf(marker);
  assert.notEqual(at, -1, `README is missing ${marker}`);
  const fence = readme.indexOf('\n```', at);
  assert.notEqual(fence, -1, `README is missing a fence after ${marker}`);
  const bodyStart = readme.indexOf('\n', fence + 1) + 1;
  const bodyEnd = readme.indexOf('\n```', bodyStart);
  assert.notEqual(bodyEnd, -1, `README fence after ${marker} is unclosed`);
  return readme.slice(bodyStart, bodyEnd + 1);
}

test('README excerpts match verify.yml, evaluators.yml, and the G1 definition', () => {
  const readme = read('README.md');
  const verifyName = '.github/workflows/verify.yml';
  const verifyExcerpt = fencedExcerpt(readme, verifyName);
  const verifyLines = read(verifyName).split('\n').slice(197, 219).join('\n') + '\n';
  assert.equal(verifyExcerpt, verifyLines);

  const evaluatorsName = 'evaluators.yml';
  const evaluatorsExcerpt = fencedExcerpt(readme, evaluatorsName);
  assert.equal(evaluatorsExcerpt, read(evaluatorsName));

  const tomlName = 'tests/smoke/g1/unitree-g1-stock-mujoco-hallway.toml';
  const tomlExcerpt = fencedExcerpt(readme, tomlName);
  const tomlLines = read(tomlName).split('\n').slice(7, 26).join('\n') + '\n';
  assert.equal(tomlExcerpt, tomlLines);
  assert.ok(read(tomlName).includes(tomlExcerpt));
});

test('README keeps one Evaluators section and the published G1 report', () => {
  const readme = read('README.md');
  const headings = readme.split('\n').filter((line) => line === '## Evaluators');
  assert.deepEqual(headings, ['## Evaluators']);
  assert.equal(readme.includes('## Migration'), false);
  assert.equal(readme.toLowerCase().includes('earlier history'), false);
  assert.equal(/\breplaces\b/i.test(readme), false);
  const stable =
    'https://docs.livingprooflabs.com/verification-reports-action/reports/g1/unitree-g1/stock-mujoco-hallway/latest/';
  assert.ok(readme.includes(stable));
  assert.equal(readme.match(/reports\/(?:[^/\s)"']+\/)*runs\/\d+\//), null);
});
