// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { runAction } from '../src/run.mjs';

test('action.yml defines the composite action contract', () => {
  const actionYml = readFileSync(new URL('../action.yml', import.meta.url), 'utf8');
  assert.match(actionYml, /^name:\s+Living Proof Verification Reports/m);
  assert.match(actionYml, /^  using:\s+composite/m);
  assert.match(actionYml, /^\s+pins-path:/m);
  assert.match(actionYml, /^\s+robot-manifest:/m);
  assert.match(actionYml, /^\s+runner-config:/m);
  assert.match(actionYml, /^\s+output-dir:/m);
  assert.match(actionYml, /^\s+report-path:/m);
  assert.match(actionYml, /^\s+status:/m);
});

test('runAction writes a placeholder report and GitHub Action outputs', () => {
  const tempRoot = mkdtempSync(path.join(tmpdir(), 'vra-test-'));
  try {
    const outputDir = path.join(tempRoot, 'report-out');
    const githubOutput = path.join(tempRoot, 'github-output.txt');

    const result = runAction({
      INPUT_PINS_PATH: 'custom/pins.json',
      INPUT_ROBOT_MANIFEST: 'custom/robot.json',
      INPUT_RUNNER_CONFIG: 'custom/runner.json',
      INPUT_OUTPUT_DIR: outputDir,
      GITHUB_OUTPUT: githubOutput,
    });

    assert.equal(result.status, 'placeholder');
    assert.equal(result.reportPath, path.join(outputDir, 'report.json'));

    const writtenReport = JSON.parse(readFileSync(result.reportPath, 'utf8'));
    assert.equal(writtenReport.status, 'placeholder');
    assert.deepEqual(writtenReport.inputs, {
      pins_path: 'custom/pins.json',
      robot_manifest: 'custom/robot.json',
      runner_config: 'custom/runner.json',
    });

    const outputsText = readFileSync(githubOutput, 'utf8');
    assert.match(outputsText, new RegExp(`report-path=${result.reportPath.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`));
    assert.match(outputsText, /^status=placeholder$/m);
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('src/run.mjs executes as a standalone process', () => {
  const tempRoot = mkdtempSync(path.join(tmpdir(), 'vra-cli-'));
  try {
    const outputDir = path.join(tempRoot, 'out');
    const githubOutput = path.join(tempRoot, 'github-output.txt');
    const stdout = execFileSync(process.execPath, ['src/run.mjs'], {
      cwd: new URL('..', import.meta.url),
      env: {
        ...process.env,
        INPUT_OUTPUT_DIR: outputDir,
        GITHUB_OUTPUT: githubOutput,
      },
      encoding: 'utf8',
    });
    assert.match(stdout, /Living Proof Verification Reports \(placeholder\): wrote /);
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('runAction rejects an empty output-dir with a helpful error', () => {
  assert.throws(
    () => runAction({ INPUT_OUTPUT_DIR: '   ' }),
    /output-dir must be a non-empty directory path/
  );
});
