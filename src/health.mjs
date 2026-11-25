// SPDX-License-Identifier: Apache-2.0
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { ReportError, sha256 } from './definitions.mjs';
import { checkPin } from './provenance.mjs';
import { assertChecksums } from './report.mjs';

export const HEALTH = Object.freeze({
  name: 'livingproof-evaluator-health',
  repository: 'Living-Proof-Labs/livingproof-evaluator-health',
  tag: 'v1.1.2',
  commit: '7441d6d7c7dc529d12cf0be0a4dc6225faa85cad',
});

function require(value, message) {
  if (!value) throw new ReportError(`Health: ${message}`);
}

export function healthEnabled(value) {
  require(value === undefined || value === '' || value === 'false' || value === 'true', 'health must be true or false');
  return value === 'true';
}

export async function resolveHealth(client, trial = '1') {
  require(/^[1-9][0-9]*$/.test(String(trial)) && Number.isSafeInteger(Number(trial)), 'health-trial must be a positive integer');
  const pin = { ...HEALTH, kind: 'source', paths: [], lockField: 'health' };
  const source = checkPin(pin, await client.resolve(pin));
  require(source.commit === HEALTH.commit, 'release tag does not match the reviewed immutable commit');
  return { source, trial: Number(trial) };
}

function json(file) { return JSON.parse(readFileSync(file, 'utf8')); }

export function healthInputs(item, configuration, downloadRoot, trial) {
  const prefix = item.artifactPrefix;
  const evidence = path.join(downloadRoot, 'evidence', `${prefix}evidence-${configuration}`);
  const media = path.join(downloadRoot, 'mcap', `${prefix}mcap-${configuration}`);
  const reportDir = path.join(downloadRoot, 'reports', `${prefix}capability-report-${configuration}`);
  assertChecksums(reportDir);
  const manifest = json(path.join(evidence, 'manifest.json'));
  const run = json(path.join(evidence, 'run-manifest.json'));
  const mediaDoc = json(path.join(media, 'media.json'));
  const report = json(path.join(reportDir, 'report.json'));
  require(manifest.format === 'livingproof-hallway-evidence/2.0.0', 'unsupported evidence manifest');
  require(typeof manifest.run_id === 'string' && manifest.run_id.length > 0, 'missing run identity');
  require(manifest.configuration?.name === configuration && run.configuration?.name === configuration && mediaDoc.configuration === configuration, 'configuration mismatch');
  require(run.run_id === manifest.run_id && mediaDoc.run_id === manifest.run_id, 'run mismatch');
  require(run.robot?.commit === item.robot.ref && [`https://github.com/${item.robot.repository}`, `https://github.com/${item.robot.repository}.git`].includes(run.robot?.repo), 'robot source mismatch');
  require(run.configuration.sha256 === manifest.configuration.sha256 && /^[a-f0-9]{64}$/.test(manifest.configuration.sha256), 'configuration digest mismatch');
  const trials = manifest.trials?.results;
  require(Array.isArray(trials) && new Set(trials.map(t => t.index)).size === trials.length, 'duplicate or missing evidence trials');
  const selected = trials.filter(t => t.index === trial);
  require(selected.length === 1 && /^[a-f0-9]{64}$/.test(selected[0].sha256), 'requested trial is not uniquely bound');
  const rows = report.source_bundle?.results;
  require(Array.isArray(rows), 'report has no source bundle');
  const matched = rows.filter(r => r.original_result?.index === trial && r.provenance?.run_id === manifest.run_id && r.system?.id === `${manifest.robot?.id}/${configuration}`);
  require(matched.length === 1, 'report trial/run/configuration is not uniquely matched');
  require(Array.isArray(matched[0].evidence) && matched[0].evidence.some(e => e.sha256 === selected[0].sha256), 'report observation digest mismatch');
  require(Array.isArray(mediaDoc.trials) && mediaDoc.trials.filter(t => t.index === trial && t.observation_sha256 === selected[0].sha256).length === 1, 'media trial digest mismatch');
  return { evidence, media, manifest, selected: selected[0], reportTrial: matched[0].id };
}

export function prepareHealth(plan, downloadRoot, env = process.env) {
  if (!healthEnabled(env.INPUT_HEALTH)) return new Map();
  const health = plan.health;
  require(health?.source?.commit === HEALTH.commit && health.source.tag === HEALTH.tag && health.source.repository === HEALTH.repository && ['resolved', 'not-published', 'verified'].includes(health.source.status) && /^sha256:[a-f0-9]{64}$/.test(health.source.digest), 'verified release provenance missing');
  require(Number.isSafeInteger(health.trial) && health.trial > 0, 'invalid trial');
  const inputs = [];
  for (const item of plan.definitions) {
    require(item.execution === 'capability-verifier', 'Health requires real recorded evidence, not fixture execution');
    for (const configuration of item.configurations) inputs.push({ key: `${item.id}/${configuration}`, ...healthInputs(item, configuration, downloadRoot, health.trial) });
  }
  const temporary = mkdtempSync(path.join(tmpdir(), 'vra-health-'));
  const results = new Map();
  const run = (command, args) => execFileSync(command, args, { cwd: temporary, env, encoding: 'utf8', timeout: 180000, maxBuffer: 16 << 20 });
  try {
    const python = path.join(temporary, 'env/bin/python');
    run('uv', ['venv', '--python', '3.12.14', path.join(temporary, 'env')]);
    run('uv', ['pip', 'install', '--python', python, `${HEALTH.name} @ git+https://github.com/${HEALTH.repository}.git@${HEALTH.commit}`]);
    const version = run(python, ['-m', 'livingproof_evaluator_health', '--version']).trim();
    require(version === `${HEALTH.name} ${HEALTH.tag.slice(1)}`, 'installed version mismatch');
    for (const [index, input] of inputs.entries()) {
      const output = path.join(temporary, `${index}.json`);
      run(python, ['-m', 'livingproof_evaluator_health', '--evidence', path.resolve(input.evidence), '--media', path.resolve(input.media), '--trial', String(health.trial), '--output', output]);
      const bytes = readFileSync(output);
      const result = JSON.parse(bytes);
      require(result.evaluator_version === HEALTH.tag.slice(1) && result.trial?.run_id === input.manifest.run_id && result.trial.configuration === input.manifest.configuration.name && result.trial.index === health.trial && result.trial.observation_sha256 === input.selected.sha256, 'evaluator output trial/version mismatch');
      require(result.evidence_manifest_sha256 === sha256(readFileSync(path.join(input.evidence, 'manifest.json'))) && result.media_manifest_sha256 === sha256(readFileSync(path.join(input.media, 'media.json'))) && result.run_manifest_sha256 === sha256(readFileSync(path.join(input.evidence, 'run-manifest.json'))), 'evaluator manifest digest mismatch');
      require(![result.simulator?.status, result.robot?.status].includes('declared media file missing'), 'declared MCAP file missing');
      results.set(input.key, { bytes, record: { advisory: true, path: 'health.json', sha256: sha256(bytes), source: health.source, trial: result.trial, report_trial: input.reportTrial } });
    }
    return results;
  } finally { rmSync(temporary, { recursive: true, force: true }); }
}
