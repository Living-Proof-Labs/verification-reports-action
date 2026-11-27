// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { createHash } from 'node:crypto';
import { VERIFIER_WORKFLOW_SHA, planDefinitions } from '../src/definitions.mjs';
import { runAction } from '../src/run.mjs';

const CALLER = 'Living-Proof-Labs/verification-reports-action';
const SHA = '0123456789abcdef0123456789abcdef01234567';
const DECLARED = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';

function callerEnv(overrides = {}) {
  return {
    ...process.env,
    GITHUB_REPOSITORY: CALLER,
    GITHUB_SHA: SHA,
    GITHUB_RUN_ID: '99',
    GITHUB_SERVER_URL: 'https://github.com',
    VERIFICATION_CREATED: '2026-11-23T00:00:00Z',
    INPUT_DEFINITIONS: 'tests/fixtures/robot/verifications/*.toml',
    INPUT_OUTPUT_DIR: '',
    INPUT_VERSION: '1.2.3',
    INPUT_REPOSITORY: '',
    INPUT_REF: '',
    WORKFLOW_REPOSITORY: CALLER,
    ...overrides,
  };
}

test('action.yml keeps the composite contract aligned with the workflow', () => {
  const actionYml = readFileSync(new URL('../action.yml', import.meta.url), 'utf8');
  const workflow = readFileSync(new URL('../.github/workflows/report.yml', import.meta.url), 'utf8');
  const readme = readFileSync(new URL('../README.md', import.meta.url), 'utf8');
  assert.match(actionYml, /^name:\s+Living Proof Verification Reports/m);
  assert.match(actionYml, /^  using:\s+composite/m);
  for (const key of ['definitions', 'output-dir', 'version', 'repository', 'ref', 'media', 'comment', 'evaluators']) {
    assert.match(actionYml, new RegExp(`^\\s+${key}:`, 'm'));
    assert.match(workflow, new RegExp(`^\\s+${key}:`, 'm'));
    assert.match(readme, new RegExp(`\\| \`${key}\` \\|`));
  }
  for (const key of ['report-path', 'status', 'grade', 'grades']) {
    assert.match(actionYml, new RegExp(`^\\s+${key}:`, 'm'));
    assert.match(workflow, new RegExp(`^\\s+${key}:`, 'm'));
    assert.match(readme, new RegExp(`\\| \`${key}\` \\|`));
  }
  assert.match(readme, /reusable workflow/i);
  assert.match(readme, /composite action/i);
  assert.match(readme, /matrices/i);
});

test('runAction writes both fixture procedures and keeps a FAIL grade successful', () => {
  const tempRoot = mkdtempSync(path.join(tmpdir(), 'vra-run-'));
  try {
    const outputDir = path.join(tempRoot, 'out');
    const githubOutput = path.join(tempRoot, 'github-output.txt');
    const result = runAction(
      callerEnv({ INPUT_OUTPUT_DIR: outputDir, GITHUB_OUTPUT: githubOutput })
    );
    assert.equal(result.status, 'ok');
    assert.equal(result.grades['fixture-hallway/stock'], 'PASS');
    assert.equal(result.grades['fixture-scenario/stock'], 'FAIL');
    const outputs = readFileSync(githubOutput, 'utf8');
    assert.match(outputs, /^status=ok$/m);
    assert.match(outputs, /"fixture-scenario\/stock":"FAIL"/);

    for (const [id, grade, procedure] of [
      ['fixture-hallway', 'PASS', 'walk-hallway'],
      ['fixture-scenario', 'FAIL', 'scenario'],
    ]) {
      const directory = path.join(outputDir, id, 'stock');
      for (const name of ['report.html', 'report.md', 'report.json', 'report.txt', 'SHA256SUMS', 'run.json']) {
        assert.equal(readFileSync(path.join(directory, name), 'utf8').length > 0, true, name);
      }
      execFileSync('sha256sum', ['-c', 'SHA256SUMS'], { cwd: directory });
      const report = JSON.parse(readFileSync(path.join(directory, 'report.json'), 'utf8'));
      assert.equal(report.summaries[0].grade, grade);
      const run = JSON.parse(readFileSync(path.join(directory, 'run.json'), 'utf8'));
      assert.equal(run.outcome[0].grade, grade);
      assert.equal(run.procedure, procedure);
      assert.equal(run.robot.commit, SHA);
      assert.equal(run.declared_robot.commit, DECLARED);
      assert.notEqual(run.robot.commit, run.declared_robot.commit);
      assert.equal(run.version, '1.2.3');
      assert.equal(run.fixture, true);
      assert.equal(readFileSync(path.join(directory, 'evidence', 'observation.json'), 'utf8').includes(procedure), true);
    }
    assert.equal(
      readFileSync(path.join(outputDir, 'fixture-hallway', 'stock', 'evidence', 'trial.mcap'), 'utf8'),
      'fixture-mcap\n'
    );
    assert.equal(exists(path.join(outputDir, 'fixture-scenario', 'stock', 'evidence', 'trial.mcap')), false);
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('src/run.mjs executes the fixture as a process', () => {
  const tempRoot = mkdtempSync(path.join(tmpdir(), 'vra-cli-'));
  try {
    const outputDir = path.join(tempRoot, 'out');
    const stdout = execFileSync(process.execPath, ['src/run.mjs'], {
      cwd: new URL('..', import.meta.url),
      env: callerEnv({ INPUT_OUTPUT_DIR: outputDir }),
      encoding: 'utf8',
    });
    assert.match(stdout, /Living Proof Verification Reports: wrote /);
    assert.match(stdout, /\(ok\)/);
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('runAction rejects an empty output-dir', () => {
  assert.throws(
    () => runAction(callerEnv({ INPUT_OUTPUT_DIR: '   ' })),
    /output-dir must be a non-empty directory path/
  );
});

test('the caller is tested at its checked-out commit', () => {
  assert.throws(
    () =>
      runAction(
        callerEnv({
          INPUT_OUTPUT_DIR: 'unused',
          INPUT_REF: 'ffffffffffffffffffffffffffffffffffffffff',
        })
      ),
    /checked-out commit/
  );
  assert.throws(
    () =>
      planDefinitions(
        { definitions: 'tests/fixtures/robot/verifications/*.toml', repository: 'Example/robot', ref: 'v1.2.3' },
        callerEnv(),
        new URL('..', import.meta.url)
      ),
    /full commit SHA/
  );
});

test('a definition outside the fixture is sent to capability-verifier', () => {
  const tempRoot = mkdtempSync(path.join(tmpdir(), 'vra-plan-'));
  try {
    const toml = [
      'format = "livingproof-verification-definition/0.1.0"',
      'id = "corner"',
      'procedure = "scenario"',
      'scenario = "walk-corner-left@0.2.0"',
      '',
      '[robot]',
      'repository = "Example/robot"',
      'ref = "v9.9.9"',
      'configurations = ["stock"]',
      '',
    ].join('\n');
    writeFileSync(path.join(tempRoot, 'corner.toml'), toml);
    const digest = createHash('sha256').update(toml).digest('hex');
    const pinned = 'cccccccccccccccccccccccccccccccccccccccc';
    writeFileSync(
      path.join(tempRoot, 'corner.lock.json'),
      `${JSON.stringify({
        format: 'livingproof-verification-lock/0.2.0',
        definition: { path: 'corner.toml', sha256: digest },
        procedure: 'scenario',
        robot: { repository: 'Example/robot', ref: 'v9.9.9', commit: DECLARED, configurations: ['stock'] },
        configurations: [{ name: 'stock' }],
        releases: { verifier: { repository: 'Living-Proof-Labs/capability-verifier', tag: 'v9.9.9', commit: 'dddddddddddddddddddddddddddddddddddddddd' } },
      })}\n`
    );
    const plan = planDefinitions(
      { definitions: '*.toml', repository: 'Example/robot', ref: pinned },
      callerEnv(),
      tempRoot
    );
    assert.equal(plan.definitions.length, 1);
    const item = plan.definitions[0];
    assert.equal(item.execution, 'capability-verifier');
    assert.equal(item.procedure, 'scenario');
    assert.equal(item.robot.repository, 'Example/robot');
    assert.equal(item.robot.ref, pinned);
    assert.notEqual(item.robot.ref, DECLARED);
    assert.match(item.workflow, new RegExp(`${VERIFIER_WORKFLOW_SHA}$`));
    assert.match(item.workflow, /scenario\.yml@/);
    assert.throws(
      () => runAction(callerEnv({ INPUT_DEFINITIONS: '*.toml', INPUT_OUTPUT_DIR: path.join(tempRoot, 'out') }), tempRoot),
      /Call \.github\/workflows\/report\.yml/
    );
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('a changed definition and a missing fixture both refuse a report', () => {
  const tempRoot = mkdtempSync(path.join(tmpdir(), 'vra-bad-'));
  const root = path.join(tempRoot, 'repo');
  const definitionDir = path.join(root, 'tests/fixtures/robot/verifications');
  mkdirSync(definitionDir, { recursive: true });
  const toml = [
    'format = "livingproof-verification-definition/0.1.0"',
    'id = "broken"',
    'procedure = "walk-hallway"',
    '',
    '[robot]',
    'configurations = ["stock"]',
    '',
  ].join('\n');
  writeFileSync(path.join(definitionDir, 'broken.toml'), toml);
  writeFileSync(
    path.join(definitionDir, 'broken.lock.json'),
    `${JSON.stringify({
      format: 'livingproof-verification-lock/0.2.0',
      definition: { sha256: '0'.repeat(64) },
      procedure: 'walk-hallway',
      configurations: [{ name: 'stock' }],
    })}\n`
  );
  const outputDir = path.join(tempRoot, 'out');
  assert.throws(
    () =>
      runAction(
        callerEnv({ INPUT_DEFINITIONS: 'tests/fixtures/robot/verifications/*.toml', INPUT_OUTPUT_DIR: outputDir }),
        root
      ),
    /sha256/
  );
  assert.equal(exists(outputDir), false);
});

function exists(file) {
  try {
    readFileSync(file);
    return true;
  } catch (error) {
    if (error.code === 'ENOENT') return false;
    throw error;
  }
}
