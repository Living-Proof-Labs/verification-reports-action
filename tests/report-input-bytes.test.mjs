// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import { withReportInputs } from '../src/report.mjs';

test('input stamping preserves hashed source bytes and replaces only top-level inputs', () => {
  const source = '{"threshold":27.0,"zero":0.0,"big":9007199254740993,"text":"comma, brace} quote\\\"","inputs":[1]}';
  const raw = `{"source_bundle":${source},"source_bundle_sha256":"unchanged","inputs":["old"]}`;
  const output = withReportInputs(raw, [{ repo: 'a/b' }]);
  assert.ok(output.includes(`"source_bundle":${source}`));
  assert.deepEqual(JSON.parse(output).inputs, [{ repo: 'a/b' }]);
  const twice = withReportInputs(output, []);
  assert.ok(twice.includes(`"source_bundle":${source}`));
  assert.deepEqual(JSON.parse(twice).inputs, []);
});

test('escaped top-level input key is replaced and nested punctuation is retained', () => {
  const output = withReportInputs('{"inpu\\u0074s":[0], "other": {"a,b":[{},[]]}}', [2]);
  assert.deepEqual(JSON.parse(output), { other: { 'a,b': [{}, []] }, inputs: [2] });
  assert.deepEqual(JSON.parse(withReportInputs('{}', [])), { inputs: [] });
  for (const raw of ['[]', 'null', '{bad']) assert.throws(() => withReportInputs(raw, []));
});
