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
  const badSha = 'b'.repeat(40);
  return `#!/bin/bash
set -euo pipefail
if [ "\${1:-}" = "run" ]; then
  if [ "${scenario}" = "garbage-list" ]; then
    echo 'not json'
    exit 0
  fi
  if [ "${scenario}" = "empty-id" ]; then
    echo '[{"databaseId":null}]'
    exit 0
  fi
  status_filter=""
  workflow=""
  event=""
  branch=""
  limit=20
  prev=""
  for arg in "$@"; do
    case "$prev" in
      --status) status_filter="$arg" ;;
      --workflow) workflow="$arg" ;;
      --event) event="$arg" ;;
      --branch) branch="$arg" ;;
      --limit) limit="$arg" ;;
    esac
    prev="$arg"
  done
  ids=""
  keep() {
    local id="$1" st="$2" wf="$3" ev="$4" br="$5"
    if [ -n "$status_filter" ] && [ "$st" != "$status_filter" ]; then return 0; fi
    if [ -n "$workflow" ] && [ "$wf" != "$workflow" ]; then return 0; fi
    if [ -n "$event" ] && [ "$ev" != "$event" ]; then return 0; fi
    if [ -n "$branch" ] && [ "$br" != "$branch" ]; then return 0; fi
    ids="$ids $id"
  }
  keep 106 failure verify.yml push main
  keep 102 success docs.yml push main
  keep 111 success verify.yml merge_group main
  keep 222 success verify.yml push feature
  keep 333 success verify.yml push main
  count=0
  out="["
  for id in $ids; do
    if [ "$count" -ge "$limit" ]; then break; fi
    if [ "$count" -gt 0 ]; then out="$out,"; fi
    out="$out{\\"databaseId\\":$id}"
    count=$((count + 1))
  done
  printf '%s\\n' "$out]"
  exit 0
fi
if [ "\${1:-}" = "api" ]; then
  if [ "${scenario}" = "garbage-api" ]; then
    echo 'not json'
    exit 0
  fi
  id="\${2##*/}"
  if [ -z "$id" ]; then id="0"; fi
  sha="${RELEASE_COMMIT}"
  repo="${EXPECTED_REPO}"
  conclusion="success"
  run_status="completed"
  event="push"
  branch="main"
  case "$id" in
    106) conclusion="failure" ;;
    111) event="merge_group" ;;
    222) branch="feature" ;;
  esac
  if [ "${scenario}" = "bad-sha" ]; then sha="${badSha}"; fi
  if [ "${scenario}" = "bad-repo" ]; then repo="Someone/verification-reports-action"; fi
  if [ "${scenario}" = "bad-conclusion" ]; then conclusion="failure"; fi
  if [ "${scenario}" = "bad-status" ]; then run_status="in_progress"; fi
  if [ "${scenario}" = "bad-event" ]; then event="pull_request"; fi
  if [ "${scenario}" = "bad-branch" ]; then branch="feature"; fi
  jq -n \\
    --arg sha "$sha" \\
    --arg repo "$repo" \\
    --arg conclusion "$conclusion" \\
    --arg status "$run_status" \\
    --arg event "$event" \\
    --arg branch "$branch" \\
    --argjson id "$id" \\
    '{head_sha:$sha,head_repository:{full_name:$repo},conclusion:$conclusion,status:$status,event:$event,head_branch:$branch,id:$id}'
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

test('dispatch lookup requires --status success', () => {
  const result = runScript({}, 'match');
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.output, 'id=333\n');
});

test('dispatch lookup requires the verify workflow', () => {
  const result = runScript({}, 'match');
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.output, 'id=333\n');
});

test('dispatch lookup refuses a run that did not succeed', () => {
  const result = runScript({}, 'bad-conclusion');
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /Expected conclusion success/);
  assert.match(result.stderr, /Found failure/);
});

test('dispatch lookup refuses a run that is not completed', () => {
  const result = runScript({}, 'bad-status');
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /Expected status completed/);
  assert.match(result.stderr, /Found in_progress/);
});

test('dispatch lookup refuses an event that is not push', () => {
  const result = runScript({}, 'bad-event');
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /Expected event push/);
  assert.match(result.stderr, /Found pull_request/);
});

test('dispatch lookup refuses a branch that is not main', () => {
  const result = runScript({}, 'bad-branch');
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /Expected head_branch main/);
  assert.match(result.stderr, /Found feature/);
});

test('garbage gh output stops before a run id is chosen', () => {
  for (const scenario of ['garbage-list', 'empty-id', 'garbage-api']) {
    const result = runScript({}, scenario);
    assert.notEqual(result.status, 0, scenario);
    assert.equal(result.output, '', `${scenario} wrote ${JSON.stringify(result.output)}`);
  }
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
