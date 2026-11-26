// SPDX-License-Identifier: Apache-2.0
// Reassemble Health-off from the SAME downloaded artifacts, never run a simulator.
import assert from 'node:assert/strict';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { collectReports } from '../src/collect.mjs';
import { HEALTH } from '../src/health.mjs';
import { sha256 } from '../src/definitions.mjs';
import { assertChecksums, listFiles } from '../src/report.mjs';

const off = collectReports();
assert.equal(off.outputs.grade, process.env.HEALTH_ON_GRADE);
assert.deepEqual(JSON.parse(off.outputs.grades), JSON.parse(process.env.HEALTH_ON_GRADES));
const plan = JSON.parse(readFileSync(process.env.INPUT_PLAN_PATH));
const bindings = [];
for (const item of plan.definitions) {
  for (const configuration of item.configurations) {
    const key = `${item.id}/${configuration}`;
    const onDir = path.join(process.env.HEALTH_ON_DIR, key);
    const offDir = path.join(off.outputDir, key);
    assertChecksums(onDir);
    assertChecksums(offDir);
    assert.equal(existsSync(path.join(offDir, 'health.json')), false);
    for (const name of ['report.json', 'report.html', 'report.md', 'report.txt']) {
      assert.deepEqual(readFileSync(path.join(onDir, name)), readFileSync(path.join(offDir, name)), `${key}/${name} changed`);
    }
    for (const file of listFiles(path.join(offDir, 'evidence'))) {
      assert.deepEqual(readFileSync(file), readFileSync(path.join(onDir, path.relative(offDir, file))));
    }
    const healthBytes = readFileSync(path.join(onDir, 'health.json'));
    const health = JSON.parse(healthBytes);
    const record = JSON.parse(readFileSync(path.join(onDir, 'run.json')));
    assert.equal(record.health.source.commit, HEALTH.commit);
    assert.equal(record.health.sha256, sha256(healthBytes));
    assert.equal(health.evaluator_version, HEALTH.tag.slice(1));
    assert.equal(health.trial.index, 1);
    assert.equal(health.trial.configuration, 'stock');
    assert.equal(health.simulator.status, 'recorded simulator observations');
    assert.ok(health.simulator.sample_count > 0);
    assert.equal(health.robot.status, 'no robot health telemetry');
    assert.deepEqual(health.robot.message_counts, {});
    const offRecord = JSON.parse(readFileSync(path.join(offDir, 'run.json')));
    // Health-on additionally retains exact private replay inputs. Validate them
    // against the SAME downloaded producer artifact before excluding this optional
    // descriptor from metadata parity; no verdict/provenance field is excluded.
    const originalDir = path.join(process.env.INPUT_VERIFIER_ROOT, 'reports', `${item.artifactPrefix}capability-report-${configuration}`);
    assert.equal(record.reporter_input.format, 'livingproof-reporter-input/1');
    assert.deepEqual(Object.keys(record.reporter_input).sort(), ['format', 'integrity_lines', 'report']);
    for (const [field, retained, original] of [
      ['report', 'report-render-input.json', 'report.json'],
      ['integrity_lines', 'report-integrity-lines.txt', 'integrity-lines.txt'],
    ]) {
      const bytes = readFileSync(path.join(onDir, retained));
      assert.deepEqual(bytes, readFileSync(path.join(originalDir, original)));
      assert.deepEqual(record.reporter_input[field], { path: retained, sha256: sha256(bytes) });
      assert.equal(existsSync(path.join(offDir, retained)), false);
    }
    assert.equal(offRecord.reporter_input, undefined);
    delete record.reporter_input;
    delete record.health;
    // Each collection has its own timestamp; all outcomes/provenance are equal.
    delete record.run.created;
    delete offRecord.run.created;
    assert.deepEqual(record, offRecord);
    bindings.push({ key, ...health, simulator: { ...health.simulator, snapshots: undefined }, health_sha256: sha256(healthBytes) });
  }
}
writeFileSync(path.join(off.outputDir, 'health-comparison.json'), JSON.stringify({ grade: off.outputs.grade, grades: off.grades, identical_reports_and_evidence: true, bindings }, null, 2) + '\n');
console.log(JSON.stringify({ grade: off.outputs.grade, identical_reports_and_evidence: true, bindings }));
