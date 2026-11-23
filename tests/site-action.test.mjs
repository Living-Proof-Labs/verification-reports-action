// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { collectReports } from '../src/collect.mjs';
import { runFixtureCli } from '../src/fixture-run.mjs';
import { writePlan } from '../src/plan.mjs';

const repoRoot = path.resolve(import.meta.dirname, '..');

test('site/action.yml defines the composite site step contract without Pages deployment', () => {
  const siteActionYml = readFileSync(path.join(repoRoot, 'site/action.yml'), 'utf8');
  assert.match(siteActionYml, /^name:\s+Living Proof Verification Reports Site/m);
  assert.match(siteActionYml, /^  using:\s+composite/m);
  for (const inputName of [
    'runs-dir:',
    'output-dir:',
    'evidence-retention-days:',
    'restore-history:',
    'site-url:',
    'upload-artifact:',
  ]) {
    assert.ok(siteActionYml.includes(`  ${inputName}`), `missing input ${inputName}`);
  }
  for (const outputName of ['reports-dir:', 'index-path:', 'runs-count:', 'artifact-only:']) {
    assert.ok(siteActionYml.includes(`  ${outputName}`), `missing output ${outputName}`);
  }
  // Pull-request runs build the section as an artifact only and never deploy Pages.
  assert.doesNotMatch(siteActionYml, /actions\/deploy-pages/);
  assert.doesNotMatch(siteActionYml, /actions\/upload-pages-artifact/);
  assert.match(
    siteActionYml,
    /uses: actions\/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065/
  );
  assert.match(
    siteActionYml,
    /uses: actions\/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02/
  );
});

test('src/site_action.py executes end to end as a standalone process on fixture runs', () => {
  const tempRoot = mkdtempSync(path.join(tmpdir(), 'vra-site-'));
  try {
    const runsDir = path.join(tempRoot, 'runs');
    const reportsDir = path.join(tempRoot, 'docs', 'reports');
    const githubOutput = path.join(tempRoot, 'github-output.txt');

    execFileSync('python3', ['tools/site_fixture.py', 'write', runsDir, '--run-id', '77'], {
      cwd: repoRoot,
      encoding: 'utf8',
    });

    execFileSync('python3', ['src/site_action.py'], {
      cwd: repoRoot,
      env: {
        ...process.env,
        INPUT_RUNS_DIR: runsDir,
        INPUT_OUTPUT_DIR: reportsDir,
        INPUT_EVIDENCE_RETENTION_DAYS: '7',
        INPUT_RESTORE_HISTORY: 'auto',
        INPUT_UPLOAD_ARTIFACT: 'auto',
        GITHUB_EVENT_NAME: 'pull_request',
        GITHUB_RUN_ID: '77',
        GITHUB_RUN_ATTEMPT: '1',
        GITHUB_OUTPUT: githubOutput,
      },
      encoding: 'utf8',
    });

    const outputs = readFileSync(githubOutput, 'utf8');
    assert.match(outputs, /^artifact-only=true$/m);
    assert.match(outputs, /^should-upload-artifact=true$/m);
    assert.match(outputs, /^runs-count=3$/m);

    const indexHtml = readFileSync(path.join(reportsDir, 'index.html'), 'utf8');
    assert.ok(
      indexHtml.includes(
        'https://docs.livingprooflabs.com/livingproof-verification/reports/index.html'
      )
    );
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('src/site_action.py consumes collectReports output from .github/workflows/report.yml', () => {
  const tempRoot = mkdtempSync(path.join(tmpdir(), 'vra-report-to-site-'));
  try {
    const baseEnv = {
      ...process.env,
      GITHUB_REPOSITORY: 'Living-Proof-Labs/verification-reports-action',
      GITHUB_SHA: '0123456789abcdef0123456789abcdef01234567',
      GITHUB_RUN_ID: '88',
      VERIFICATION_CREATED: '2026-11-23T03:00:00Z',
      INPUT_DEFINITIONS: 'tests/fixtures/robot/verifications/*.toml',
      INPUT_VERSION: '2.1.0',
      INPUT_REPOSITORY: '',
      INPUT_REF: '',
    };
    const planPath = path.join(tempRoot, 'plan.json');
    const planned = writePlan(
      { ...baseEnv, INPUT_PLAN_PATH: planPath, GITHUB_OUTPUT: path.join(tempRoot, 'plan.out') },
      repoRoot
    );
    const stage = path.join(tempRoot, 'stage');
    for (const item of planned.plan.definitions) {
      runFixtureCli(
        {
          ...baseEnv,
          INPUT_PLAN_PATH: planPath,
          INPUT_DEFINITION: item.path,
          INPUT_OUTPUT_DIR: path.join(stage, `vra-fixture-${item.id}`),
        },
        repoRoot
      );
    }
    const collectedDir = path.join(tempRoot, 'verification-report');
    collectReports(
      {
        ...baseEnv,
        INPUT_PLAN_PATH: planPath,
        INPUT_OUTPUT_DIR: collectedDir,
        INPUT_FIXTURE_ROOT: stage,
        INPUT_VERIFIER_ROOT: path.join(tempRoot, 'no-verifier'),
        GITHUB_OUTPUT: path.join(tempRoot, 'collect.out'),
      },
      tempRoot
    );

    const reportsDir = path.join(tempRoot, 'docs', 'reports');
    const siteOut = path.join(tempRoot, 'site.out');
    execFileSync('python3', ['src/site_action.py'], {
      cwd: repoRoot,
      env: {
        ...baseEnv,
        INPUT_RUNS_DIR: collectedDir,
        INPUT_OUTPUT_DIR: reportsDir,
        INPUT_RESTORE_HISTORY: 'false',
        INPUT_UPLOAD_ARTIFACT: 'false',
        GITHUB_EVENT_NAME: 'push',
        GITHUB_OUTPUT: siteOut,
      },
      encoding: 'utf8',
    });

    const outputs = readFileSync(siteOut, 'utf8');
    assert.match(outputs, /^runs-count=2$/m);
    assert.match(outputs, /^artifact-only=false$/m);

    const indexHtml = readFileSync(path.join(reportsDir, 'index.html'), 'utf8');
    const failPos = indexHtml.indexOf('fixture-scenario-stock/latest/');
    const passPos = indexHtml.indexOf('fixture-hallway-stock/latest/');
    assert.ok(failPos !== -1 && passPos !== -1 && failPos < passPos);

    execFileSync('python3', ['tools/site_fixture.py', 'check', reportsDir], {
      cwd: repoRoot,
      encoding: 'utf8',
    });
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});
