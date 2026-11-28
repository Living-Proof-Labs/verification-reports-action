// SPDX-License-Identifier: Apache-2.0
// #75: the action renders no report. Fixture runs copy their committed report files.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import * as report from '../src/report.mjs';

test('report.mjs no longer exports a stub renderer', () => {
  assert.equal(report.renderReport, undefined);
  const source = readFileSync(new URL('../src/report.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /renderReport/);
  assert.doesNotMatch(source, /<!DOCTYPE html>/);
});

test('fixture runs take their report files from the fixture, not from a renderer', () => {
  const fixture = readFileSync(new URL('../src/fixture.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(fixture, /renderReport/);
  for (const id of ['fixture-hallway', 'fixture-scenario']) {
    for (const name of ['report.html', 'report.json', 'report.md', 'report.txt']) {
      const url = new URL(`../tests/fixtures/robot/expected/${id}/stock/${name}`, import.meta.url);
      assert.ok(readFileSync(url, 'utf8').length > 0, `${id}/stock/${name}`);
    }
  }
});
