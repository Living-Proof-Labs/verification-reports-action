// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { HEALTH, healthEnabled, healthInputs, prepareHealth, resolveHealth } from '../src/health.mjs';
import { collectReports } from '../src/collect.mjs';
import { REQUIRED_INPUTS } from '../src/provenance.mjs';
import { writeChecksums } from '../src/report.mjs';

const hash = 'a'.repeat(64);
const commit = 'b'.repeat(40);
const evidence = { releaseFound: true, tagCommit: HEALTH.commit, publishedCommit: HEALTH.commit, sourceDigest: hash, attestation: { published: false, status: 404 } };

test('Health is opt-in and uses the existing source provenance checks', async () => {
  assert.equal(healthEnabled(undefined), false);
  assert.equal(healthEnabled('false'), false);
  assert.equal(healthEnabled('true'), true);
  assert.throws(() => healthEnabled('yes'));
  assert.equal(prepareHealth({}, '', { INPUT_HEALTH: 'false' }).size, 0);
  const health = await resolveHealth({ resolve: async pin => {
    assert.equal(pin.kind, 'source');
    assert.equal(pin.tag, 'v1.1.2');
    return evidence;
  } });
  assert.equal(health.source.commit, HEALTH.commit);
  assert.equal(health.trial, 1);
  for (const trial of [0, -1, 1.5, '1;echo bad', '9007199254740992']) {
    await assert.rejects(resolveHealth({ resolve: async () => evidence }, trial), /positive integer/);
  }
  for (const change of [ { tagCommit: commit }, { releaseFound: false }, { sourceDigest: null }, { attestation: { published: false, status: 403 } } ]) {
    await assert.rejects(resolveHealth({ resolve: async () => ({ ...evidence, ...change }) }));
  }
  assert.throws(() => prepareHealth({ health: { source: { ...health.source, commit } } }, '', { INPUT_HEALTH: 'true' }), /provenance missing/);
});

function fixture(t) {
  const root = mkdtempSync(path.join(tmpdir(), 'vra-health-binding-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const item = { artifactPrefix: 'vra-example-', robot: { repository: 'Example/robot', ref: commit } };
  const dirs = { evidence: 'evidence/vra-example-evidence-stock', media: 'mcap/vra-example-mcap-stock', report: 'reports/vra-example-capability-report-stock' };
  const documents = {
    evidence: { format: 'livingproof-hallway-evidence/2.0.0', run_id: 'recorded-run', robot: { id: 'robot' }, configuration: { name: 'stock', sha256: hash }, trials: { results: [{ index: 1, sha256: hash }] } },
    run: { run_id: 'recorded-run', robot: { repo: 'https://github.com/Example/robot.git', commit }, configuration: { name: 'stock', sha256: hash } },
    media: { run_id: 'recorded-run', configuration: 'stock', trials: [{ index: 1, observation_sha256: hash }] },
    report: { source_bundle: { results: [{ id: 'stock-trial-01', original_result: { index: 1 }, provenance: { run_id: 'recorded-run' }, system: { id: 'robot/stock' }, evidence: [{ sha256: hash }] }] } },
  };
  const files = { evidence: [dirs.evidence, 'manifest.json'], run: [dirs.evidence, 'run-manifest.json'], media: [dirs.media, 'media.json'], report: [dirs.report, 'report.json'] };
  function save() {
    for (const [key, [dir, file]] of Object.entries(files)) {
      mkdirSync(path.join(root, dir), { recursive: true });
      writeFileSync(path.join(root, dir, file), JSON.stringify(documents[key]));
    }
    writeChecksums(path.join(root, dirs.report));
  }
  save();
  return { root, item, documents, save };
}

test('matched report, robot, configuration, run and trial pass before invocation', t => {
  const f = fixture(t);
  assert.equal(healthInputs(f.item, 'stock', f.root, 1).reportTrial, 'stock-trial-01');
});

const mutations = {
  'foreign robot commit': d => { d.run.robot.commit = 'c'.repeat(40); },
  'foreign robot repo': d => { d.run.robot.repo = 'https://github.com/Other/robot.git'; },
  'configuration name': d => { d.evidence.configuration.name = 'playground'; },
  'configuration digest': d => { d.run.configuration.sha256 = 'c'.repeat(64); },
  'media run': d => { d.media.run_id = 'foreign'; },
  'run manifest': d => { d.run.run_id = 'foreign'; },
  'report run': d => { d.report.source_bundle.results[0].provenance.run_id = 'foreign'; },
  'report configuration': d => { d.report.source_bundle.results[0].system.id = 'robot/playground'; },
  'report trial': d => { d.report.source_bundle.results[0].original_result.index = 2; },
  'report digest': d => { d.report.source_bundle.results[0].evidence[0].sha256 = 'c'.repeat(64); },
  'media digest': d => { d.media.trials[0].observation_sha256 = 'c'.repeat(64); },
  'duplicate evidence trial': d => { d.evidence.trials.results.push(d.evidence.trials.results[0]); },
  'ambiguous report trial': d => { d.report.source_bundle.results.push(d.report.source_bundle.results[0]); },
};
for (const [name, mutate] of Object.entries(mutations)) test(`Health refuses ${name}`, t => {
  const f = fixture(t);
  mutate(f.documents);
  f.save();
  assert.throws(() => healthInputs(f.item, 'stock', f.root, 1));
});

test('workflow keeps a single simulator job and compares off/on from its artifacts', () => {
  const workflow = readFileSync(new URL('../.github/workflows/verify.yml', import.meta.url), 'utf8');
  assert.equal((workflow.match(/definitions: tests\/smoke\/g1\/\*\.toml/g) ?? []).length, 1);
  assert.match(workflow, /health: true/);
  assert.match(workflow, /run: node tools\/health-smoke.mjs/);
  const report = readFileSync(new URL('../.github/workflows/report.yml', import.meta.url), 'utf8');
  assert.match(report, /INPUT_HEALTH: \$\{\{ inputs.health \}\}/);
  assert.match(report, /if: inputs.health/);
});

// Refusal must precede final assembly, preserving an existing output directory.
test('collector leaves output untouched when a Health join fails', async t => {
  const f = fixture(t);
  f.documents.media.run_id = 'foreign';
  f.save();
  const source = await resolveHealth({ resolve: async () => evidence });
  const item = { ...f.item, id: 'example', execution: 'capability-verifier', configurations: ['stock'], provenance: REQUIRED_INPUTS.map(name => ({ name, status: 'resolved' })) };
  const plan = path.join(f.root, 'plan.json');
  writeFileSync(plan, JSON.stringify({ health: source, definitions: [item] }));
  const output = path.join(f.root, 'output');
  mkdirSync(output);
  writeFileSync(path.join(output, 'keep'), 'untouched');
  assert.throws(() => collectReports({ INPUT_PLAN_PATH: plan, INPUT_OUTPUT_DIR: output, INPUT_VERIFIER_ROOT: f.root, INPUT_HEALTH: 'true' }), /run mismatch/);
  assert.equal(readFileSync(path.join(output, 'keep'), 'utf8'), 'untouched');
});
