// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { chmodSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';

const docsYml = readFileSync(new URL('../.github/workflows/docs.yml', import.meta.url), 'utf8');
const RELEASE_COMMIT = 'a'.repeat(40);
const EXPECTED_REPO = 'Living-Proof-Labs/verification-reports-action';

function verifyRunScript() {
  const marker = docsYml.indexOf('id: verify-run');
  assert.ok(marker !== -1, 'verify-run step missing');
  const runAt = docsYml.indexOf('\n        run: |\n', marker);
  assert.ok(runAt !== -1, 'verify-run script missing');
  const lines = [];
  for (const line of docsYml.slice(runAt + '\n        run: |\n'.length).split('\n')) {
    if (line.startsWith('          ')) {
      lines.push(line.slice(10));
      continue;
    }
    if (line.trim() === '') {
      lines.push('');
      continue;
    }
    break;
  }
  while (lines.length > 0 && lines[lines.length - 1] === '') lines.pop();
  return `${lines.join('\n')}\n`;
}

function fakeGh(scenario) {
  return `#!/bin/bash
set -euo pipefail
if [ "\${1:-}" = "run" ]; then
  event=""
  branch=""
  prev=""
  for arg in "$@"; do
    if [ "$prev" = "--event" ]; then event="$arg"; fi
    if [ "$prev" = "--branch" ]; then branch="$arg"; fi
    prev="$arg"
  done
  if [ "$event" = "push" ] && [ "$branch" = "main" ]; then
    printf '%s\\n' '[{"databaseId":333,"headSha":"${RELEASE_COMMIT}","event":"push","headBranch":"main"}]'
  elif [ "$event" != "push" ]; then
    printf '%s\\n' '[{"databaseId":111,"headSha":"${RELEASE_COMMIT}","event":"merge_group","headBranch":"main"}]'
  else
    printf '%s\\n' '[{"databaseId":222,"headSha":"${RELEASE_COMMIT}","event":"push","headBranch":"feature"}]'
  fi
  exit 0
fi
if [ "\${1:-}" = "api" ]; then
  id="\${2##*/}"
  sha="${RELEASE_COMMIT}"
  repo="${EXPECTED_REPO}"
  if [ "${scenario}" = "bad-sha" ]; then sha="${'b'.repeat(40)}"; fi
  if [ "${scenario}" = "bad-repo" ]; then repo="Someone/verification-reports-action"; fi
  if [ "${scenario}" = "empty" ]; then printf '%s\\n' '[]'; exit 0; fi
  printf '%s\\n' "{\\"head_sha\\":\\"$sha\\",\\"head_repository\\":{\\"full_name\\":\\"$repo\\"},\\"id\\":$id}"
  exit 0
fi
echo "unexpected gh command: $*" >&2
exit 9
`;
}

function runScript(env, scenario) {
  const root = mkdtempSync(path.join(tmpdir(), 'verify-run-'));
  const gh = path.join(root, 'gh');
  writeFileSync(gh, fakeGh(scenario));
  chmodSync(gh, 0o755);
  const output = path.join(root, 'github-output.txt');
  writeFileSync(output, '');
  const result = spawnSync('bash', ['-c', verifyRunScript()], {
    encoding: 'utf8',
    env: {
      ...process.env,
      PATH: `${root}:${process.env.PATH}`,
      GH_TOKEN: 'test-token',
      GITHUB_OUTPUT: output,
      RELEASE_COMMIT,
      EVENT_RUN: '',
      ...env,
    },
  });
  return {
    status: result.status,
    stderr: result.stderr,
    output: readFileSync(output, 'utf8'),
  };
}

test('a release keeps the triggering workflow_run id', () => {
  const result = runScript({ EVENT_RUN: '4242' }, 'match');
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.output, 'id=4242\n');
});

test('dispatch lookup keeps only a push to main', () => {
  const result = runScript({}, 'match');
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.output, 'id=333\n');
});

test('dispatch lookup requires the push event filter', () => {
  const script = verifyRunScript();
  assert.match(script, /--event push/);
  const result = runScript({}, 'match');
  assert.equal(result.output, 'id=333\n', result.stderr);
});

test('dispatch lookup requires the main branch filter', () => {
  const script = verifyRunScript();
  assert.match(script, /--branch main/);
  const result = runScript({}, 'match');
  assert.equal(result.output, 'id=333\n', result.stderr);
});

test('dispatch lookup refuses a headSha that is not the release commit', () => {
  const result = runScript({}, 'bad-sha');
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, new RegExp(`Expected headSha ${RELEASE_COMMIT}`));
  assert.match(result.stderr, new RegExp(`Found ${'b'.repeat(40)}`));
});

test('dispatch lookup refuses a fork repository', () => {
  const result = runScript({}, 'bad-repo');
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, new RegExp(`Expected repository ${EXPECTED_REPO}`));
  assert.match(result.stderr, /Found Someone\/verification-reports-action/);
});

test('dispatch lookup refuses when no push to main exists', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'verify-run-empty-'));
  const gh = path.join(root, 'gh');
  writeFileSync(gh, '#!/bin/bash\nif [ "${1:-}" = "run" ]; then echo "[]"; exit 0; fi\nexit 9\n');
  chmodSync(gh, 0o755);
  const output = path.join(root, 'github-output.txt');
  writeFileSync(output, '');
  const result = spawnSync('bash', ['-c', verifyRunScript()], {
    encoding: 'utf8',
    env: {
      ...process.env,
      PATH: `${root}:${process.env.PATH}`,
      GH_TOKEN: 'test-token',
      GITHUB_OUTPUT: output,
      RELEASE_COMMIT,
      EVENT_RUN: '',
    },
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, new RegExp(`Expected a successful push to main of ${EXPECTED_REPO} at ${RELEASE_COMMIT}`));
  assert.match(result.stderr, /Found none/);
});
