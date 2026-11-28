// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import test from 'node:test';
import { buildRunRecord } from '../src/report.mjs';

const SHA = 'd554c8243a12849dc7fe8acc1921bb3d4e5239a7';
const CALLER = 'Living-Proof-Labs/verification-reports-action';
const ROBOT = 'Living-Proof-Labs/livingproof-unitree-g1';

test('buildRunRecord links the tested repository commit, not the caller', () => {
  const record = buildRunRecord({
    item: {
      id: 'unitree-g1-stock-mujoco-hallway',
      path: 'tests/smoke/g1/unitree-g1-stock-mujoco-hallway.toml',
      procedure: 'walk-hallway',
      execution: 'capability-verifier',
      callerRepository: CALLER,
      robot: { repository: ROBOT, ref: SHA },
      lock: { robot: { repository: ROBOT, ref: 'v2.3.34' } },
      provenance: [],
    },
    configuration: 'stock',
    summary: {
      capability: 'walk-hallway',
      system: { id: 'unitree-g1/stock' },
      grade: 'FAIL',
      k: 0,
      n: 1,
      required_passes: 1,
    },
    env: {
      GITHUB_SERVER_URL: 'https://github.com',
      GITHUB_RUN_ID: '46083905519',
      VERIFICATION_CREATED: '2026-11-28T03:42:00Z',
    },
    version: '3.9.3',
  });

  const expected = `https://github.com/${ROBOT}/commit/${SHA}`;
  assert.equal(record.commit.sha, SHA);
  assert.equal(record.commit.url, expected);
  assert.equal(record.commit.url.includes('verification-reports-action'), false);
  assert.equal(record.robot.repository, ROBOT);
  assert.equal(record.robot.url, `https://github.com/${ROBOT}/tree/${SHA}`);
});
