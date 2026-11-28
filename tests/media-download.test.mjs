// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const reportYml = readFileSync(new URL('../.github/workflows/report.yml', import.meta.url), 'utf8');
const verifyYml = readFileSync(new URL('../.github/workflows/verify.yml', import.meta.url), 'utf8');
const definitions = readFileSync(new URL('../src/definitions.mjs', import.meta.url), 'utf8');

function downloadStep(text, path) {
  const marker = `path: ${path}`;
  const at = text.indexOf(marker);
  assert.ok(at !== -1, `${path} download is missing`);
  const start = text.lastIndexOf('\n      - ', at);
  const end = text.indexOf('\n      - ', at + marker.length);
  assert.ok(start !== -1, `${path} download has no step`);
  return text.slice(start, end === -1 ? undefined : end);
}

function globToRegExp(pattern) {
  const body = pattern
    .split('*')
    .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('[^/]*');
  return new RegExp(`^${body}$`);
}

function artifactPrefix(id) {
  const match = definitions.match(/artifactPrefix: `([^`]+)`/);
  assert.ok(match, 'artifactPrefix template is missing');
  return match[1].replaceAll('${loaded.id}', id);
}

function uploadedName(id, configuration, kind, extra = '') {
  return `${artifactPrefix(id)}${kind}-${configuration}${extra}`;
}

test('media download names match what the verifier uploads', () => {
  assert.match(definitions, /artifactPrefix: `vra-\$\{loaded\.id\}-`/);
  for (const text of [reportYml, verifyYml]) {
    for (const kind of ['mcap', 'video']) {
      const step = downloadStep(text, `downloaded/${kind}`);
      const found = step.match(/pattern: (\S+)/);
      assert.ok(found, `${kind} download has no pattern`);
      const pattern = globToRegExp(found[1]);
      const id = 'unitree-g1-stock-mujoco-hallway';
      const configuration = 'stock';
      const uploaded =
        kind === 'mcap'
          ? uploadedName(id, configuration, 'mcap')
          : uploadedName(id, configuration, 'video', '-seed-1-pass');
      assert.match(uploaded, pattern, `${uploaded} does not match ${found[1]}`);
      const other =
        kind === 'mcap'
          ? uploadedName(id, configuration, 'video', '-seed-1-fail')
          : uploadedName(id, configuration, 'mcap');
      assert.doesNotMatch(other, pattern);
    }
  }
  const reportMcap = downloadStep(reportYml, 'downloaded/mcap').match(/pattern: (\S+)/)[1];
  const verifyMcap = downloadStep(verifyYml, 'downloaded/mcap').match(/pattern: (\S+)/)[1];
  assert.equal(reportMcap, verifyMcap);
  assert.equal(reportMcap, 'vra-*-mcap-*');
});

test('a failed sibling does not drop an uploaded mcap', () => {
  const walk = reportYml.slice(reportYml.indexOf('\n  walk-hallway:\n'), reportYml.indexOf('\n  scenario:\n'));
  const scenario = reportYml.slice(reportYml.indexOf('\n  scenario:\n'), reportYml.indexOf('\n  collect:\n'));
  assert.match(walk, /fail-fast: false/);
  assert.match(scenario, /fail-fast: false/);
  for (const text of [reportYml, verifyYml]) {
    const step = downloadStep(text, 'downloaded/mcap');
    assert.match(step, /actions\/download-artifact@/);
    assert.match(step, /pattern: vra-\*-mcap-\*/);
    assert.doesNotMatch(step, /\bif:/);
    assert.doesNotMatch(step, /\bname:/);
    assert.doesNotMatch(step, /merge-multiple:/);
  }
  // The pattern selects every uploaded artifact. A sibling that never uploaded
  // is absent, and the one that did upload is still selected.
  const pattern = globToRegExp('vra-*-mcap-*');
  const uploaded = [uploadedName('demo', 'stock', 'mcap')];
  const failedSibling = uploadedName('demo', 'playground', 'mcap');
  const selected = uploaded.filter((name) => pattern.test(name));
  assert.deepEqual(selected, ['vra-demo-mcap-stock']);
  assert.equal(uploaded.includes(failedSibling), false);
  assert.equal(pattern.test(failedSibling), true);
});
