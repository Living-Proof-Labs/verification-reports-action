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
    /uses: Living-Proof-Labs\/collaboration-guidelines\/\.github\/workflows\/semantic-release\.yml@d0d9ae8e684f8a828b774eda01622b7c66e5252e # v9\.9\.10/
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
    /uses: Living-Proof-Labs\/collaboration-guidelines\/\.github\/workflows\/semantic-release-candidate\.yml@d0d9ae8e684f8a828b774eda01622b7c66e5252e # v9\.9\.10/
  );
  assert.match(verifyYml, /action-smoke:[\s\S]*?needs: version/);
  assert.match(verifyYml, /version: \$\{\{ needs\.version\.outputs\.semver-version \}\}/);
  assert.match(verifyYml, /name: smoke-report-\$\{\{ needs\.version\.outputs\.semver-version \}\}/);
  assert.doesNotMatch(verifyYml, /expected-version/);
  assert.doesNotMatch(verifyYml, /uses: \.\/\.github\/workflows\/release\.yml/);
  assert.doesNotMatch(verifyYml, /actions: write/);
  assert.doesNotMatch(verifyYml, /0\.0\.0-placeholder|"placeholder"|status=placeholder/);
  assert.match(verifyYml, / {2}verify:[\s\S]*?\n {4}permissions:\n {6}contents: read\n {6}checks: write\n/);
  assert.match(
    verifyYml,
    / {2}action-smoke:[\s\S]*?\n {4}permissions:\n {6}contents: read\n {6}pull-requests: write\n {4}uses: \.\/\.github\/workflows\/report\.yml\n/
  );
  assert.match(
    verifyYml,
    / {2}site-smoke:[\s\S]*?\n {4}permissions:\n {6}contents: read\n {4}steps:\n/
  );
  assert.match(verifyYml, /uses: \.\/site/);
  assert.doesNotMatch(verifyYml, /pages: write/);
  assert.match(verifyYml, /^permissions:\n {2}contents: read\nconcurrency:/m);
  assert.match(verifyYml, /definitions: tests\/fixtures\/robot\/verifications\/\*\.toml/);
});

test('the report workflow calls both verifier procedures and passes no secrets', () => {
  const reportYml = readFileSync(new URL('../.github/workflows/report.yml', import.meta.url), 'utf8');
  assert.match(reportYml, /^on:\n {2}workflow_call:/m);
  assert.match(
    reportYml,
    /uses: Living-Proof-Labs\/capability-verifier\/\.github\/workflows\/walk-hallway\.yml@0c00c388c693012392da0c1cd391046c752e9b3c # v2\.11\.4/
  );
  assert.match(
    reportYml,
    /uses: Living-Proof-Labs\/capability-verifier\/\.github\/workflows\/scenario\.yml@0c00c388c693012392da0c1cd391046c752e9b3c # v2\.11\.4/
  );
  assert.doesNotMatch(reportYml, /secrets:/);
  assert.match(reportYml, /^permissions:\n {2}contents: read\njobs:/m);
  assert.match(
    reportYml,
    / {2}walk-hallway:[\s\S]*?\n {4}permissions:\n {6}contents: read\n {6}pull-requests: write\n/
  );
  assert.match(
    reportYml,
    / {2}scenario:[\s\S]*?\n {4}permissions:\n {6}contents: read\n {6}pull-requests: write\n/
  );
  assert.match(reportYml, /toJson\(job\)/);
  assert.match(reportYml, /jq -r \.workflow_repository/);
  assert.match(reportYml, /jq -r \.workflow_sha/);
  assert.match(reportYml, /steps\.workflow-source\.outputs\.repository/);
  assert.match(reportYml, /steps\.workflow-source\.outputs\.sha/);
  assert.match(reportYml, /path: action/);
  assert.match(reportYml, /path: caller/);
  assert.match(reportYml, /node "\$\{\{ github\.workspace \}\}\/action\/src\/plan\.mjs"/);
  assert.match(reportYml, /test ! -e src\/plan\.mjs/);
  assert.doesNotMatch(reportYml, /run: node src\//);
  assert.match(reportYml, /name: verification-report/);
  assert.match(releaseYml, /^permissions:\n {2}contents: read\njobs:/m);
  assert.match(
    releaseYml,
    / {2}post-release:[\s\S]*?\n {4}permissions:\n {6}contents: read\n {6}pull-requests: write\n {4}uses: \.\/\.github\/workflows\/report\.yml\n/
  );
  assert.match(releaseYml, /uses: \.\/\.github\/workflows\/report\.yml/);
  assert.doesNotMatch(releaseYml, /0\.0\.0-placeholder|"placeholder"|status=placeholder/);
  assert.match(releaseYml, /ref: \$\{\{ needs\.release\.outputs\.git-tag \}\}/);
  assert.match(releaseYml, /version: \$\{\{ needs\.release\.outputs\.version \}\}/);
});

test('org workflow uses refs are full SHAs with a tag comment', () => {
  const reportYml = readFileSync(new URL('../.github/workflows/report.yml', import.meta.url), 'utf8');
  const files = {
    'report.yml': reportYml,
    'verify.yml': verifyYml,
    'release.yml': releaseYml,
  };
  for (const [name, text] of Object.entries(files)) {
    assert.doesNotMatch(text, /uses:\s*Living-Proof-Labs\/\S+@v\d/);
    for (const match of text.matchAll(/^\s*(?:-\s*)?uses:\s*(\S+)(.*)$/gm)) {
      const ref = match[1];
      if (ref.startsWith('.')) continue;
      const at = ref.lastIndexOf('@');
      assert.ok(at > 0, `${name}: ${ref} has no pin`);
      const target = ref.slice(0, at);
      if (!target.startsWith('Living-Proof-Labs/')) continue;
      assert.match(ref.slice(at + 1), /^[0-9a-f]{40}$/, `${name}: ${ref} must be a full SHA`);
      assert.match(match[2], /#\s*v\d+\.\d+\.\d+/, `${name}: ${ref} needs a tag comment`);
    }
  }
});
