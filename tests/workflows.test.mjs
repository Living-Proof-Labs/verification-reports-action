// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const verifyYml = readFileSync(new URL('../.github/workflows/verify.yml', import.meta.url), 'utf8');
const releaseYml = readFileSync(new URL('../.github/workflows/release.yml', import.meta.url), 'utf8');

function workflowName(text, file) {
  const match = text.match(/^name:\s*(\S.*?)\s*$/m);
  assert.ok(match, `${file} is missing a top-level name`);
  return match[1];
}

test('Verify is the workflow_run name Release waits for', () => {
  const verifyName = workflowName(verifyYml, 'verify.yml');
  assert.equal(verifyName, 'Verify');
  assert.match(releaseYml, new RegExp(`workflows:\\s*\\[${verifyName}\\]`));
  assert.match(releaseYml, /workflow_run:/);
  assert.match(releaseYml, /types:\s*\[completed\]/);
  assert.match(releaseYml, /branches:\s*\[main\]/);
  assert.doesNotMatch(releaseYml, /workflow_call:/);
  assert.doesNotMatch(releaseYml, /expected-version/);
  assert.doesNotMatch(releaseYml, /expected-commit/);
  assert.doesNotMatch(releaseYml, /current-main/);
});

test('release tags main before post-release consumes its outputs', () => {
  assert.match(releaseYml, /^permissions:\n {2}contents: read/m);
  assert.match(
    releaseYml,
    /uses: Living-Proof-Labs\/collaboration-guidelines\/\.github\/workflows\/semantic-release\.yml@v9\.9\.10/
  );
  const releaseJob = releaseYml.indexOf('\n  release:\n');
  const postReleaseJob = releaseYml.indexOf('\n  post-release:\n');
  assert.ok(releaseJob !== -1 && postReleaseJob !== -1);
  assert.ok(releaseJob < postReleaseJob);
  assert.match(releaseYml, /needs: release/);
  assert.match(releaseYml, /needs\.release\.outputs\.published == 'true'/);
  assert.match(releaseYml, /ref: \$\{\{ needs\.release\.outputs\.git-tag \}\}/);
  assert.match(releaseYml, /version: \$\{\{ needs\.release\.outputs\.version \}\}/);
  assert.match(releaseYml, /uses: \.\//);
  assert.match(releaseYml, /github\.event\.workflow_run\.head_sha == github\.sha/);
});

test('verify forecasts a candidate version and smokes it without sealing a release', () => {
  assert.match(verifyYml, /^on:[\s\S]*?pull_request:/m);
  assert.match(verifyYml, /^on:[\s\S]*?merge_group:/m);
  assert.match(verifyYml, /^on:[\s\S]*?push:/m);
  assert.match(
    verifyYml,
    /uses: Living-Proof-Labs\/collaboration-guidelines\/\.github\/workflows\/semantic-release-candidate\.yml@v9\.9\.10/
  );
  assert.match(verifyYml, /action-smoke:[\s\S]*?needs: version/);
  assert.match(verifyYml, /version: \$\{\{ needs\.version\.outputs\.semver-version \}\}/);
  assert.match(verifyYml, /name: smoke-report-\$\{\{ needs\.version\.outputs\.semver-version \}\}/);
  assert.doesNotMatch(verifyYml, /expected-version/);
  assert.doesNotMatch(verifyYml, /uses: \.\/\.github\/workflows\/release\.yml/);
});
