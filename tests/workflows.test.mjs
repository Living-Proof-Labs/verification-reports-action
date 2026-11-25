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
  assert.match(verifyYml, /g1-smoke:[\s\S]*?needs: version/);
  assert.match(verifyYml, /version: \$\{\{ needs\.version\.outputs\.semver-version \}\}/);
  assert.match(verifyYml, /name: smoke-report-\$\{\{ needs\.version\.outputs\.semver-version \}\}/);
  assert.match(verifyYml, /name: g1-smoke-report-\$\{\{ needs\.version\.outputs\.semver-version \}\}/);
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
    / {2}g1-smoke:[\s\S]*?\n {4}permissions:\n {6}contents: read\n {6}pull-requests: write\n {4}uses: \.\/\.github\/workflows\/report\.yml\n/
  );
  assert.match(
    verifyYml,
    / {2}site-smoke:[\s\S]*?\n {4}permissions:\n {6}contents: read\n {4}steps:\n/
  );
  assert.match(verifyYml, /uses: \.\/site/);
  assert.doesNotMatch(verifyYml, /pages: write/);
  assert.match(verifyYml, /^permissions:\n {2}contents: read\nconcurrency:/m);
  assert.match(verifyYml, /definitions: tests\/fixtures\/robot\/verifications\/\*\.toml/);
  assert.match(verifyYml, /definitions: tests\/smoke\/g1\/\*\.toml/);
});

test('the report workflow calls both verifier procedures and passes no secrets', () => {
  const reportYml = readFileSync(new URL('../.github/workflows/report.yml', import.meta.url), 'utf8');
  assert.match(reportYml, /^on:\n {2}workflow_call:/m);
  assert.match(
    reportYml,
    /uses: Living-Proof-Labs\/capability-verifier\/\.github\/workflows\/walk-hallway\.yml@e6a1ba347c525b62abf0644b610562b7bfb390d9 # v2\.12\.5/
  );
  assert.match(
    reportYml,
    /uses: Living-Proof-Labs\/capability-verifier\/\.github\/workflows\/scenario\.yml@e6a1ba347c525b62abf0644b610562b7bfb390d9 # v2\.12\.5/
  );
  const walkHallway = reportYml.slice(reportYml.indexOf('\n  walk-hallway:\n'), reportYml.indexOf('\n  scenario:\n'));
  const scenario = reportYml.slice(reportYml.indexOf('\n  scenario:\n'), reportYml.indexOf('\n  collect:\n'));
  for (const input of ['verifier-commit', 'verifier-image', 'reporter-image', 'evaluator-commit']) {
    const line = `${input}: \${{ matrix.${input.replaceAll('-', '_')} }}`;
    assert.match(walkHallway, new RegExp(line.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    assert.match(scenario, new RegExp(line.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  }
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
  const planStep = reportYml.slice(reportYml.indexOf('- id: plan\n'), reportYml.indexOf('\n  fixture:\n'));
  assert.match(planStep, /GITHUB_TOKEN: \$\{\{ github\.token \}\}/);
  assert.doesNotMatch(planStep, /secrets\./);
  assert.match(reportYml, /refuses before any trial/);
  assert.match(reportYml, / {2}walk-hallway:\n {4}needs: plan\n/);
  assert.match(reportYml, / {2}scenario:\n {4}needs: plan\n/);
  assert.match(reportYml, /test ! -e src\/plan\.mjs/);
  assert.doesNotMatch(reportYml, /run: node src\//);
  assert.match(reportYml, /artifact-name:[\s\S]*?default: verification-report/);
  assert.match(reportYml, /name: \$\{\{ inputs\.artifact-name \}\}/);
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
  const docsYml = readFileSync(new URL('../.github/workflows/docs.yml', import.meta.url), 'utf8');
  const files = {
    'docs.yml': docsYml,
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

test('the Python suite runs on each html.parser behaviour the site policy must not depend on', () => {
  const job = verifyYml.match(/ {2}site-policy:\n[\s\S]*?(?=\n {2}[a-z-]+:\n)/)?.[0] ?? '';
  for (const python of ['3.12.0', '3.12.3', '3.13.5', '3.13', '3.14']) {
    assert.match(job, new RegExp(`'${python.replaceAll('.', '\\.')}'`), `site-policy must run Python ${python}`);
  }
  assert.match(job, /fail-fast: false/);
  assert.match(job, /UV_PYTHON_PREFERENCE: only-managed/);
  assert.match(job, /uv run --locked --no-sync pytest/);
});

// actionlint catches this in CI, but only after a push; this runs in `task test`.
function workflowJobs(text) {
  const body = text.slice(text.search(/^jobs:\n/m));
  const jobs = new Map();
  const heads = [...body.matchAll(/^ {2}([A-Za-z0-9_-]+):\s*$/gm)];
  heads.forEach((head, index) => {
    const block = body.slice(head.index, heads[index + 1]?.index ?? body.length);
    jobs.set(head[1], block);
  });
  return jobs;
}

function declaredOutputs(block, indent) {
  const pad = ' '.repeat(indent);
  const section = block.match(new RegExp(`^${pad}outputs:\\n((?:${pad} {2}.*\\n?)*)`, 'm'));
  if (!section) return new Set();
  return new Set([...section[1].matchAll(new RegExp(`^${pad} {2}([A-Za-z0-9_-]+):`, 'gm'))].map((m) => m[1]));
}

function jobOutputs(block) {
  const local = block.match(/^ {4}uses:\s*\.\/(\S+)/m);
  if (local) {
    const called = readFileSync(new URL(`../${local[1]}`, import.meta.url), 'utf8');
    const trigger = called.match(/^ {2}workflow_call:\n((?: {4}.*\n?)*)/m)?.[1] ?? '';
    return declaredOutputs(trigger, 4);
  }
  if (/^ {4}uses:/m.test(block)) return null; // remote reusable workflow: outputs not visible here
  return declaredOutputs(block, 4);
}

test('every needs.<job>.outputs.<name> names a needed job and an output it declares', () => {
  for (const file of ['docs.yml', 'report.yml', 'verify.yml', 'release.yml']) {
    const text = readFileSync(new URL(`../.github/workflows/${file}`, import.meta.url), 'utf8');
    const jobs = workflowJobs(text);
    assert.ok(jobs.size > 0, `${file}: no jobs found`);
    for (const [name, block] of jobs) {
      const needsLine = block.match(/^ {4}needs:\s*(.+)$/m)?.[1] ?? '';
      const needs = needsLine.replace(/[[\]]/g, '').split(',').map((s) => s.trim()).filter(Boolean);
      for (const ref of block.matchAll(/needs\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)/g)) {
        const [, job, output] = ref;
        assert.ok(needs.includes(job), `${file}: job ${name} reads ${ref[0]} but does not need ${job}`);
        assert.ok(jobs.has(job), `${file}: job ${name} reads outputs of unknown job ${job}`);
        const outputs = jobOutputs(jobs.get(job));
        if (outputs === null) continue;
        assert.ok(
          outputs.has(output),
          `${file}: job ${name} reads ${ref[0]}, but ${job} declares only [${[...outputs].join(', ')}]`,
        );
      }
    }
  }
});
