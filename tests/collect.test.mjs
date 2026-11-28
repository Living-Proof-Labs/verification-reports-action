// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { collectReports } from '../src/collect.mjs';
import { writePlan } from '../src/plan.mjs';
import { runFixtureCli } from '../src/fixture-run.mjs';
import { REQUIRED_INPUTS } from '../src/provenance.mjs';
import { writeChecksums } from '../src/report.mjs';
import { reportFiles } from './report-files.mjs';

const CALLER = 'Living-Proof-Labs/verification-reports-action';
const SHA = '0123456789abcdef0123456789abcdef01234567';

function env(overrides) {
  return {
    ...process.env,
    GITHUB_REPOSITORY: CALLER,
    GITHUB_SHA: SHA,
    GITHUB_RUN_ID: '42',
    VERIFICATION_CREATED: '2026-11-23T00:00:00Z',
    INPUT_DEFINITIONS: 'tests/fixtures/robot/verifications/*.toml',
    INPUT_VERSION: '9.9.9',
    INPUT_REPOSITORY: '',
    INPUT_REF: '',
    WORKFLOW_REPOSITORY: CALLER,
    ...overrides,
  };
}

test('plan, fixture-run and collect reproduce the full fixture report', async () => {
  const tempRoot = mkdtempSync(path.join(tmpdir(), 'vra-collect-'));
  const repo = fileURLToPath(new URL('..', import.meta.url));
  try {
    const planPath = path.join(tempRoot, 'plan.json');
    const planned = await writePlan(
      env({ INPUT_PLAN_PATH: planPath, GITHUB_OUTPUT: path.join(tempRoot, 'plan-out.txt') }),
      repo
    );
    assert.equal(planned.outputs.fixture_count, '2');
    assert.equal(planned.outputs.hallway_count, '0');
    assert.equal(planned.outputs.scenario_count, '0');
    const procedures = planned.plan.definitions.map((item) => item.procedure).sort();
    assert.deepEqual(procedures, ['scenario', 'walk-hallway']);

    const stage = path.join(tempRoot, 'artifacts');
    for (const item of planned.plan.definitions) {
      const outputDir = path.join(stage, `vra-fixture-${item.id}`);
      runFixtureCli(
        env({
          INPUT_PLAN_PATH: planPath,
          INPUT_DEFINITION: item.path,
          INPUT_OUTPUT_DIR: outputDir,
        }),
        repo
      );
    }
    const outputDir = path.join(tempRoot, 'final');
    const collected = collectReports(
      env({
        INPUT_PLAN_PATH: planPath,
        INPUT_OUTPUT_DIR: outputDir,
        INPUT_FIXTURE_ROOT: stage,
        INPUT_VERIFIER_ROOT: path.join(tempRoot, 'no-verifier'),
        GITHUB_OUTPUT: path.join(tempRoot, 'collect-out.txt'),
      }),
      tempRoot
    );
    assert.equal(collected.outputs.status, 'ok');
    assert.equal(collected.grades['fixture-hallway/stock'], 'PASS');
    assert.equal(collected.grades['fixture-scenario/stock'], 'FAIL');
    execFileSync('sha256sum', ['-c', 'SHA256SUMS'], {
      cwd: path.join(outputDir, 'fixture-hallway', 'stock'),
    });
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('scripts load from this repo when the caller directory has no src', () => {
  const tempRoot = mkdtempSync(path.join(tmpdir(), 'vra-caller-'));
  const repo = fileURLToPath(new URL('..', import.meta.url));
  const caller = path.join(tempRoot, 'caller');
  try {
    cpSync(path.join(repo, 'tests/fixtures'), path.join(caller, 'tests/fixtures'), { recursive: true });
    assert.equal(existsSync(path.join(caller, 'src', 'plan.mjs')), false);
    const planPath = path.join(tempRoot, 'plan.json');
    const outputFile = path.join(tempRoot, 'github-output.txt');
    execFileSync(process.execPath, [path.join(repo, 'src/plan.mjs')], {
      cwd: caller,
      env: env({
        INPUT_PLAN_PATH: planPath,
        GITHUB_OUTPUT: outputFile,
      }),
    });
    const plan = JSON.parse(readFileSync(planPath, 'utf8'));
    assert.equal(plan.definitions.length, 2);
    assert.equal(existsSync(path.join(caller, 'src')), false);
    const stage = path.join(tempRoot, 'artifacts');
    for (const item of plan.definitions) {
      execFileSync(process.execPath, [path.join(repo, 'src/fixture-run.mjs')], {
        cwd: caller,
        env: env({
          INPUT_PLAN_PATH: planPath,
          INPUT_DEFINITION: item.path,
          INPUT_OUTPUT_DIR: path.join(stage, `vra-fixture-${item.id}`),
        }),
      });
    }
    const outputDir = path.join(tempRoot, 'final');
    execFileSync(process.execPath, [path.join(repo, 'src/collect.mjs')], {
      cwd: caller,
      env: env({
        INPUT_PLAN_PATH: planPath,
        INPUT_OUTPUT_DIR: outputDir,
        INPUT_FIXTURE_ROOT: stage,
        INPUT_VERIFIER_ROOT: path.join(tempRoot, 'no-verifier'),
        GITHUB_OUTPUT: path.join(tempRoot, 'collect-out.txt'),
      }),
    });
    const grades = readFileSync(path.join(tempRoot, 'collect-out.txt'), 'utf8');
    assert.match(grades, /^status=ok$/m);
    assert.match(grades, /"fixture-scenario\/stock":"FAIL"/);
    assert.equal(existsSync(path.join(outputDir, 'fixture-hallway', 'stock', 'report.json')), true);
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('a verifier plan does not merge another job\'s fixture reports', async () => {
  const tempRoot = mkdtempSync(path.join(tmpdir(), 'vra-foreign-fixture-'));
  const repo = fileURLToPath(new URL('..', import.meta.url));
  try {
    const planPath = path.join(tempRoot, 'plan.json');
    const planned = await writePlan(
      env({ INPUT_PLAN_PATH: planPath, GITHUB_OUTPUT: path.join(tempRoot, 'plan-out.txt') }),
      repo
    );
    const stage = path.join(tempRoot, 'artifacts');
    for (const item of planned.plan.definitions) {
      runFixtureCli(
        env({
          INPUT_PLAN_PATH: planPath,
          INPUT_DEFINITION: item.path,
          INPUT_OUTPUT_DIR: path.join(stage, `vra-fixture-${item.id}`),
        }),
        repo
      );
    }
    const verifier = {
      id: 'unitree-g1-stock-mujoco-hallway',
      path: 'tests/smoke/g1/unitree-g1-stock-mujoco-hallway.toml',
      procedure: 'walk-hallway',
      configurations: ['stock'],
      execution: 'capability-verifier',
      robot: { repository: 'Living-Proof-Labs/livingproof-unitree-g1', ref: SHA },
      callerRepository: CALLER,
      artifactPrefix: 'vra-unitree-g1-stock-mujoco-hallway-',
      provenance: REQUIRED_INPUTS.map((name) => ({
        name,
        repository: 'Living-Proof-Labs/capability-verifier',
        tag: 'v2.12.5',
        commit: SHA,
        digest: `sha256:${'ab'.repeat(32)}`,
        status: 'resolved',
        result: 'resolved',
        attestation: 'not-published',
        checks: { release: 'passed', commit: 'resolved', digest: 'resolved', attestation: 'not-published' },
      })),
      lock: { robot: { commit: SHA } },
    };
    const reportDir = path.join(
      tempRoot,
      'downloaded',
      'reports',
      'vra-unitree-g1-stock-mujoco-hallway-capability-report-stock'
    );
    mkdirSync(reportDir, { recursive: true });
    for (const [name, contents] of Object.entries(reportFiles({
      capability: 'walk-hallway',
      system: { id: 'unitree-g1/stock' },
      grade: 'PASS',
      k: 1,
      n: 1,
      required_passes: 1,
    }))) {
      writeFileSync(path.join(reportDir, name), contents);
    }
    writeChecksums(reportDir);
    const g1Plan = path.join(tempRoot, 'g1-plan.json');
    writeFileSync(g1Plan, `${JSON.stringify({ definitions: [verifier] })}\n`);
    const outputDir = path.join(tempRoot, 'final');
    const collected = collectReports(
      env({
        INPUT_PLAN_PATH: g1Plan,
        INPUT_OUTPUT_DIR: outputDir,
        INPUT_FIXTURE_ROOT: stage,
        INPUT_VERIFIER_ROOT: path.join(tempRoot, 'downloaded'),
        GITHUB_OUTPUT: path.join(tempRoot, 'collect-out.txt'),
      }),
      tempRoot
    );
    assert.equal(collected.grades['unitree-g1-stock-mujoco-hallway/stock'], 'PASS');
    assert.equal(existsSync(path.join(outputDir, 'fixture-hallway')), false);
    assert.equal(existsSync(path.join(outputDir, 'fixture-scenario')), false);
    assert.deepEqual(
      planned.plan.definitions.map((item) => item.id).sort(),
      ['fixture-hallway', 'fixture-scenario']
    );
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('verifier artifacts are assembled and a missing report fails', () => {
  const tempRoot = mkdtempSync(path.join(tmpdir(), 'vra-verifier-'));
  try {
    const configuration = 'stock';
    const item = {
      id: 'corner',
      path: 'verifications/corner.toml',
      lockPath: 'verifications/corner.lock.json',
      procedure: 'scenario',
      configurations: [configuration],
      execution: 'capability-verifier',
      robot: { repository: 'Example/robot', ref: SHA },
      callerRepository: CALLER,
      artifactPrefix: 'vra-corner-',
      provenance: REQUIRED_INPUTS.map((name) => ({
        name,
        repository: 'Living-Proof-Labs/capability-verifier',
        tag: 'v2.11.4',
        commit: '0c00c388c693012392da0c1cd391046c752e9b3c',
        digest: `sha256:${'ab'.repeat(32)}`,
        status: 'verified',
        result: 'verified',
        attestation: 'verified',
        checks: { release: 'passed', commit: 'passed', digest: 'passed', attestation: 'verified' },
      })),
      lock: {
        robot: { commit: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' },
        releases: {
          verifier: {
            repository: 'Living-Proof-Labs/capability-verifier',
            tag: 'v2.11.4',
            commit: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
          },
        },
      },
    };
    const summary = {
      capability: 'walk-corner-left',
      system: { id: 'example/stock' },
      grade: 'INSUFFICIENT_EVIDENCE',
      k: null,
      n: 1,
      required_passes: 1,
    };
    const reportDir = path.join(tempRoot, 'downloaded', 'reports', `vra-corner-capability-report-${configuration}`);
    mkdirSync(reportDir, { recursive: true });
    const rendered = reportFiles(summary);
    for (const [name, contents] of Object.entries(rendered)) {
      writeFileSync(path.join(reportDir, name), contents);
    }
    writeFileSync(path.join(reportDir, 'thresholds.json'), '{"max_lateral_drift_m":0.5}\n');
    writeChecksums(reportDir);
    const evidenceDir = path.join(tempRoot, 'downloaded', 'evidence', `vra-corner-evidence-${configuration}`);
    mkdirSync(evidenceDir, { recursive: true });
    writeFileSync(path.join(evidenceDir, 'observation.json'), '{"trial":0}\n');
    const mcapDir = path.join(tempRoot, 'downloaded', 'mcap', `vra-corner-mcap-${configuration}`);
    mkdirSync(mcapDir, { recursive: true });
    writeFileSync(path.join(mcapDir, 'trial.mcap'), 'mcap');
    const plan = { definitions: [item] };
    const planPath = path.join(tempRoot, 'plan.json');
    writeFileSync(planPath, `${JSON.stringify(plan)}\n`);
    const outputDir = path.join(tempRoot, 'out');
    const collected = collectReports(
      env({
        INPUT_PLAN_PATH: planPath,
        INPUT_OUTPUT_DIR: outputDir,
        INPUT_FIXTURE_ROOT: path.join(tempRoot, 'no-fixture'),
        INPUT_VERIFIER_ROOT: path.join(tempRoot, 'downloaded'),
        INPUT_VERSION: '4.5.6',
      }),
      tempRoot
    );
    assert.equal(collected.outputs.status, 'ok');
    assert.equal(collected.outputs.grade, 'INSUFFICIENT_EVIDENCE');
    const directory = path.join(outputDir, 'corner', 'stock');
    execFileSync('sha256sum', ['-c', 'SHA256SUMS'], { cwd: directory });
    assert.equal(readFileSync(path.join(directory, 'evidence', 'observation.json'), 'utf8'), '{"trial":0}\n');
    assert.equal(
      readFileSync(path.join(directory, 'thresholds.json'), 'utf8'),
      '{"max_lateral_drift_m":0.5}\n'
    );
    assert.equal(readFileSync(path.join(directory, 'evidence', 'mcap', 'trial.mcap'), 'utf8'), 'mcap');
    const run = JSON.parse(readFileSync(path.join(directory, 'run.json'), 'utf8'));
    const report = JSON.parse(readFileSync(path.join(directory, 'report.json'), 'utf8'));
    assert.equal(run.outcome[0].grade, 'INSUFFICIENT_EVIDENCE');
    assert.equal(run.robot.commit, SHA);
    assert.equal(run.version, '4.5.6');
    assert.equal(run.inputs.length, REQUIRED_INPUTS.length);
    assert.deepEqual(report.inputs, run.inputs);
    assert.equal(report.inputs[0].status, 'verified');
    assert.equal(run.fixture, false);

    rmSync(path.join(reportDir, 'report.html'));
    assert.throws(
      () =>
        collectReports(
          env({
            INPUT_PLAN_PATH: planPath,
            INPUT_OUTPUT_DIR: path.join(tempRoot, 'again'),
            INPUT_FIXTURE_ROOT: path.join(tempRoot, 'no-fixture'),
            INPUT_VERIFIER_ROOT: path.join(tempRoot, 'downloaded'),
          }),
          tempRoot
        ),
      /report\.html is missing/
    );
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});
