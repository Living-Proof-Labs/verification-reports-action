// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { assembleVerifierDefinition } from '../src/assemble.mjs';
import {
  THIS_REPOSITORY,
  expandDefinitions,
  isFixtureCaller,
  parseToml,
  planDefinitions,
} from '../src/definitions.mjs';
import { runFixtureDefinition } from '../src/fixture.mjs';
import { runAction } from '../src/run.mjs';

const SHA = '0123456789abcdef0123456789abcdef01234567';

function callerEnv(overrides = {}) {
  return {
    GITHUB_REPOSITORY: THIS_REPOSITORY,
    GITHUB_SHA: SHA,
    GITHUB_RUN_ID: '99',
    GITHUB_SERVER_URL: 'https://github.com',
    VERIFICATION_CREATED: '2026-11-23T00:00:00Z',
    WORKFLOW_REPOSITORY: THIS_REPOSITORY,
    INPUT_VERSION: '1.2.3',
    ...overrides,
  };
}

function writeDefinition(root, { id, configuration, relative }) {
  const file = relative ?? `tests/fixtures/robot/verifications/${id}.toml`;
  const absolute = path.join(root, file);
  mkdirSync(path.dirname(absolute), { recursive: true });
  const toml = [
    'format = "livingproof-verification-definition/0.1.0"',
    `id = "${id}"`,
    'procedure = "scenario"',
    '',
    '[robot]',
    `configurations = ["${configuration}"]`,
    '',
  ].join('\n');
  writeFileSync(absolute, toml);
  const digest = createHash('sha256').update(toml).digest('hex');
  writeFileSync(
    absolute.replace(/\.toml$/, '.lock.json'),
    `${JSON.stringify({
      format: 'livingproof-verification-lock/0.2.0',
      definition: { sha256: digest },
      procedure: 'scenario',
      configurations: [{ name: configuration }],
    })}\n`
  );
  return file;
}

test('configuration ../../x fails the run and writes nothing outside the output directory', () => {
  const tempRoot = mkdtempSync(path.join(tmpdir(), 'vra-traverse-'));
  try {
    const workspace = path.join(tempRoot, 'nested', 'workspace');
    const outputDir = path.join(workspace, 'out');
    const definition = writeDefinition(workspace, { id: 'escape', configuration: '../../x' });
    const escaped = path.resolve(outputDir, '.partial-escape', '../../x');
    assert.equal(path.relative(outputDir, escaped).startsWith('..'), true);

    assert.throws(
      () =>
        runAction(
          callerEnv({
            INPUT_DEFINITIONS: 'tests/fixtures/robot/verifications/*.toml',
            INPUT_OUTPUT_DIR: outputDir,
          }),
          workspace
        ),
      /configuration "\.\.\/\.\.\/x"/
    );
    assert.equal(existsSync(escaped), false);
    assert.equal(existsSync(path.join(escaped, 'report.json')), false);
    assert.equal(existsSync(outputDir), false);

    assert.throws(
      () =>
        runFixtureDefinition(
          {
            id: 'escape',
            path: definition,
            execution: 'fixture',
            configurations: ['../../x'],
            procedure: 'scenario',
            robot: { repository: THIS_REPOSITORY, ref: SHA },
            callerRepository: THIS_REPOSITORY,
          },
          { root: workspace, outputDir, env: callerEnv(), version: '1.2.3' }
        ),
      /configuration "\.\.\/\.\.\/x"/
    );
    assert.equal(existsSync(escaped), false);

    const collectOut = path.join(tempRoot, 'collect-out');
    assert.throws(
      () =>
        assembleVerifierDefinition(
          {
            id: 'escape',
            path: definition,
            execution: 'capability-verifier',
            configurations: ['../../x'],
            artifactPrefix: 'vra-escape-',
            procedure: 'scenario',
            robot: { repository: 'Example/robot', ref: SHA },
            callerRepository: THIS_REPOSITORY,
          },
          path.join(tempRoot, 'downloaded'),
          collectOut,
          callerEnv(),
          '1.2.3'
        ),
      /configuration "\.\.\/\.\.\/x"/
    );
    const collectEscaped = path.resolve(collectOut, 'escape', '../../x');
    assert.equal(existsSync(collectEscaped), false);
    assert.equal(existsSync(path.join(collectOut, 'escape', 'report.json')), false);
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('a caller from another repository named verification-reports-action is not a fixture', () => {
  const repo = fileURLToPath(new URL('..', import.meta.url));
  assert.equal(isFixtureCaller('evil/verification-reports-action', THIS_REPOSITORY), false);
  assert.equal(isFixtureCaller('evil/verification-reports-action', 'evil/verification-reports-action'), false);
  assert.equal(isFixtureCaller(THIS_REPOSITORY, 'evil/verification-reports-action'), false);
  assert.equal(isFixtureCaller(THIS_REPOSITORY, ''), false);
  assert.equal(isFixtureCaller(THIS_REPOSITORY, THIS_REPOSITORY), true);

  for (const workflowRepository of [THIS_REPOSITORY, 'evil/verification-reports-action', '']) {
    const plan = planDefinitions(
      { definitions: 'tests/fixtures/robot/verifications/*.toml' },
      callerEnv({
        GITHUB_REPOSITORY: 'evil/verification-reports-action',
        WORKFLOW_REPOSITORY: workflowRepository,
      }),
      repo
    );
    assert.ok(plan.definitions.length >= 1);
    for (const item of plan.definitions) {
      assert.equal(item.execution, 'capability-verifier');
    }
  }
});

test('fixture runs record fixture true in run.json', () => {
  const tempRoot = mkdtempSync(path.join(tmpdir(), 'vra-fixture-flag-'));
  try {
    const outputDir = path.join(tempRoot, 'out');
    const repo = fileURLToPath(new URL('..', import.meta.url));
    runAction(
      callerEnv({
        INPUT_DEFINITIONS: 'tests/fixtures/robot/verifications/*.toml',
        INPUT_OUTPUT_DIR: outputDir,
      }),
      repo
    );
    for (const id of ['fixture-hallway', 'fixture-scenario']) {
      const run = JSON.parse(readFileSync(path.join(outputDir, id, 'stock', 'run.json'), 'utf8'));
      assert.equal(run.fixture, true);
    }
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('parseToml rejects __proto__, constructor, and prototype without polluting Object.prototype', () => {
  assert.equal(Object.prototype.polluted, undefined);
  try {
    for (const key of ['__proto__', 'constructor', 'prototype']) {
      assert.throws(() => parseToml(`[${key}]\npolluted = "yes"\n`, 'evil.toml'), /not allowed/);
      assert.equal(Object.prototype.polluted, undefined);
      assert.throws(() => parseToml(`${key} = "yes"\n`, 'evil.toml'), /not allowed/);
      assert.equal(Object.prototype.polluted, undefined);
      assert.throws(() => parseToml(`[robot.${key}]\npolluted = "yes"\n`, 'evil.toml'), /not allowed/);
      assert.equal(Object.prototype.polluted, undefined);
    }
    const parsed = parseToml('format = "ok"\n[robot]\nname = "g1"\n', 'ok.toml');
    assert.equal(parsed.format, 'ok');
    assert.equal(parsed.robot.name, 'g1');
    assert.equal(Object.getPrototypeOf(parsed), null);
    assert.equal(Object.getPrototypeOf(parsed.robot), null);
  } finally {
    delete Object.prototype.polluted;
  }
});

test('an escaping definitions glob or symlink fails', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'vra-glob-'));
  const outside = mkdtempSync(path.join(tmpdir(), 'vra-outside-'));
  try {
    writeFileSync(path.join(outside, 'evil.toml'), 'format = "livingproof-verification-definition/0.1.0"\n');
    const relativeOutside = path.relative(root, outside).split(path.sep).join('/');
    assert.match(relativeOutside, /^\.\.\//);
    assert.throws(() => expandDefinitions(`${relativeOutside}/*.toml`, root), /escapes the checkout/);
    assert.throws(() => expandDefinitions('/tmp/*.toml', root), /escapes the checkout/);

    mkdirSync(path.join(root, 'verifications'));
    writeFileSync(path.join(root, 'verifications', 'ok.toml'), 'format = "ok"\n');
    assert.deepEqual(expandDefinitions('verifications/*.toml', root), ['verifications/ok.toml']);

    symlinkSync(outside, path.join(root, 'linked'));
    assert.throws(() => expandDefinitions('linked/*.toml', root), /symlink/);

    symlinkSync(path.join(outside, 'evil.toml'), path.join(root, 'verifications', 'evil.toml'));
    assert.throws(() => expandDefinitions('verifications/*.toml', root), /symlink/);
    assert.equal(existsSync(path.join(root, 'verifications', 'evil.toml')), true);
  } finally {
    rmSync(root, { recursive: true, force: true });
    rmSync(outside, { recursive: true, force: true });
  }
});

test('README states the caller token must be able to read the workflow repository', () => {
  const readme = readFileSync(new URL('../README.md', import.meta.url), 'utf8');
  assert.match(readme, /caller's token must be able to read the workflow repository/);
  assert.match(readme, /Workflow source checkout fails before any definition is planned/);
  assert.match(readme, /no verification report is written/);
  assert.match(readme, /repository was not found, or the token does not have permission to read it/);
});
