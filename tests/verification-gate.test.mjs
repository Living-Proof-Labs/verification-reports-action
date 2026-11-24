// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { verificationJobs, verifyResults } from '../tools/verification-gate.mjs';

const verifyYml = readFileSync(new URL('../.github/workflows/verify.yml', import.meta.url), 'utf8');

function evidence(event, baseRef = '') {
  const version = event === 'push' || (event === 'pull_request' && baseRef === 'main') ? 'success' : 'skipped';
  return Object.fromEntries(verificationJobs.map((job) => [job, { result: job === 'version' ? version : 'success' }]));
}

test('pull requests, merge groups, and pushes accept only the version skip the workflow already uses', () => {
  verifyResults(evidence('pull_request', 'main'), 'pull_request', 'main');
  verifyResults(evidence('pull_request', 'feature'), 'pull_request', 'feature');
  verifyResults(evidence('merge_group'), 'merge_group', '');
  verifyResults(evidence('push'), 'push', '');
  assert.throws(() => verifyResults(evidence('merge_group'), 'pull_request', 'main'), /version must be success/);
  assert.throws(() => verifyResults(evidence('pull_request', 'main'), 'merge_group'), /version must be skipped/);
  assert.throws(() => verifyResults(evidence('pull_request', 'main'), 'pull_request', 'feature'), /version must be skipped/);
  for (const result of ['failure', 'cancelled', 'neutral']) {
    const needs = evidence('merge_group');
    needs.version.result = result;
    assert.throws(() => verifyResults(needs, 'merge_group'), new RegExp(`version must be skipped; observed ${result}`));
  }
});

for (const event of ['pull_request', 'merge_group', 'push']) {
  const baseRef = event === 'pull_request' ? 'main' : '';
  for (const result of ['failure', 'cancelled', 'skipped', 'neutral', undefined]) {
    test(`${event}: ${result ?? 'missing'} on a job that must succeed fails`, () => {
      for (const job of verificationJobs.filter((name) => evidence(event, baseRef)[name].result === 'success')) {
        const needs = evidence(event, baseRef);
        if (result === undefined) delete needs[job].result;
        else needs[job].result = result;
        assert.throws(() => verifyResults(needs, event, baseRef), new RegExp(`${job} must be success`));
      }
    });
  }
}

test('missing, extra, unsupported events, and invalid evidence fail', () => {
  const needs = evidence('pull_request', 'main');
  delete needs.verify;
  assert.throws(() => verifyResults(needs, 'pull_request', 'main'), /Missing or unexpected/);
  assert.throws(
    () => verifyResults({ ...evidence('merge_group'), extra: { result: 'success' } }, 'merge_group'),
    /Missing or unexpected/,
  );
  for (const value of [null, [], {}]) assert.throws(() => verifyResults(value, 'pull_request', 'main'));
  assert.throws(() => verifyResults(evidence('push'), 'schedule'), /Unsupported verification event/);
  assert.throws(() => verifyResults(evidence('push'), 'workflow_dispatch'), /Unsupported verification event/);
});

test('the gate job is the org check name and runs for pull_request and merge_group', () => {
  assert.match(verifyYml, /^on:[\s\S]*?pull_request:/m);
  assert.match(verifyYml, /^on:[\s\S]*?merge_group:/m);
  assert.match(verifyYml, /^permissions:\n {2}contents: read\n/m);
  const jobsSection = verifyYml.split('\njobs:\n')[1];
  const jobs = [...jobsSection.matchAll(/^ {2}([\w-]+):$/gm)].map((match) => match[1]);
  assert.deepEqual(
    jobs.filter((job) => job !== 'verification-gate').sort(),
    [...verificationJobs].sort(),
  );
  const gate = jobsSection.split('  verification-gate:')[1];
  assert.match(gate, /name: Repository verification gate\n/);
  assert.match(gate, /if: always\(\)\n/);
  assert.match(gate, /needs: \[verify, version, action-smoke, action-smoke-check, site-smoke\]\n/);
  assert.match(gate, /permissions:\n {6}contents: read\n {4}steps:\n/);
  assert.doesNotMatch(gate, /checks: write|contents: write|pull-requests: write|actions: write/);
  assert.match(gate, /actions\/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4\.4\.0/);
  assert.match(gate, /actions\/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4\.4\.0/);
  assert.match(gate, /run: node tools\/verification-gate\.mjs\n/);
  assert.match(gate, /VERIFICATION_NEEDS: \$\{\{ toJSON\(needs\) \}\}/);
  assert.match(gate, /VERIFICATION_EVENT: \$\{\{ github\.event_name \}\}/);
  assert.match(gate, /VERIFICATION_BASE_REF: \$\{\{ github\.base_ref \}\}/);
  assert.match(verifyYml, /github\.event_name == 'push' \|\|/);
  assert.match(verifyYml, /github\.event_name == 'pull_request' && github\.base_ref == 'main'/);
});

test('the command fails closed on bad evidence and passes a merge-group skip', () => {
  const script = fileURLToPath(new URL('../tools/verification-gate.mjs', import.meta.url));
  const bad = spawnSync(process.execPath, [script], {
    env: { ...process.env, VERIFICATION_NEEDS: '{', VERIFICATION_EVENT: 'merge_group' },
    encoding: 'utf8',
  });
  assert.notEqual(bad.status, 0);
  assert.match(bad.stderr, /JSON/);

  const good = spawnSync(process.execPath, [script], {
    env: {
      ...process.env,
      VERIFICATION_NEEDS: JSON.stringify(evidence('merge_group')),
      VERIFICATION_EVENT: 'merge_group',
      VERIFICATION_BASE_REF: '',
    },
    encoding: 'utf8',
  });
  assert.equal(good.status, 0, good.stderr);
});
