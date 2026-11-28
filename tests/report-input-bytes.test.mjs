// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import * as report from '../src/report.mjs';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
const withReportInputs = (...args) => report.withReportInputs(...args);

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


test('ordinary stampReportInputs retains the original source-bundle number bytes', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'report-digest-'));
  try {
    const bundle = '{"time":27.0,"bounds":[0.0,1.0]}';
    writeFileSync(path.join(dir, 'report.json'), `{"source_bundle":${bundle},"source_bundle_sha256":"original"}`);
    for (const file of ['report.html','report.md','report.txt']) writeFileSync(path.join(dir,file),'unchanged shell');
    report.stampReportInputs(dir, []);
    assert.ok(readFileSync(path.join(dir,'report.json'),'utf8').includes(`"source_bundle":${bundle}`));
  } finally { rmSync(dir,{recursive:true,force:true}); }
});
