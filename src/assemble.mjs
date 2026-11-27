// SPDX-License-Identifier: Apache-2.0
import { existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { ReportError, assertConfigurationSlug, sha256 } from './definitions.mjs';
import {
  assertChecksums,
  buildRunRecord,
  copyTreeContents,
  readSummaries,
  stampReportInputs,
  writeChecksums,
} from './report.mjs';

const REPORT_FILES = ['report.html', 'report.md', 'report.json', 'report.txt', 'SHA256SUMS'];

function artifactDir(root, artifactName) {
  if (!root || !existsSync(root)) return null;
  const direct = path.join(root, artifactName);
  if (existsSync(direct)) return direct;
  return null;
}

function copyOptional(source, destination) {
  if (!source || !existsSync(source)) return false;
  copyTreeContents(source, destination);
  return true;
}

function resultsOf(stored) {
  if (stored == null) return [];
  if (Array.isArray(stored)) return stored.filter((item) => item && typeof item === 'object');
  if (typeof stored !== 'object') return [];
  if (stored.advisory && typeof stored.advisory === 'object') return [stored];
  if (stored.evaluator && typeof stored.evaluator === 'object' && stored.format) return [stored];
  const documents = [];
  for (const item of Object.values(stored)) {
    if (Array.isArray(item)) documents.push(...item.filter((entry) => entry && typeof entry === 'object'));
    else if (item && typeof item === 'object') documents.push(item);
  }
  return documents;
}

export function citedDetailsUris(report) {
  const bundle = report?.source_bundle && typeof report.source_bundle === 'object' ? report.source_bundle : report;
  const stored = bundle?.assessment?.advisory_results;
  const uris = [];
  for (const document of resultsOf(stored)) {
    const details = document?.advisory?.details_file;
    if (details && typeof details.uri === 'string' && details.uri) uris.push(details.uri);
  }
  return uris;
}

export function safeDetailsParts(uri) {
  if (typeof uri !== 'string' || uri === '' || uri.includes('\\') || uri.includes('\u0000')) {
    throw new ReportError(`cited details path ${JSON.stringify(uri)} is not a relative path`);
  }
  if (uri.startsWith('/')) {
    throw new ReportError(`cited details path ${JSON.stringify(uri)} is absolute`);
  }
  const parts = uri.split('/');
  if (parts.some((part) => part === '' || part === '.' || part === '..')) {
    throw new ReportError(`cited details path ${JSON.stringify(uri)} is not a relative path`);
  }
  return parts;
}

function checksumMap(directory) {
  const text = readFileSync(path.join(directory, 'SHA256SUMS'), 'utf8');
  const listed = {};
  for (const line of text.split('\n')) {
    if (!line) continue;
    const match = line.match(/^([0-9a-f]{64})  (.+)$/);
    if (!match) throw new ReportError(`${directory}: SHA256SUMS has an unreadable line`);
    listed[match[2]] = match[1];
  }
  return listed;
}

export function copyCitedDetails(reportArtifact, directory, report) {
  const listed = checksumMap(reportArtifact);
  for (const uri of citedDetailsUris(report)) {
    const parts = safeDetailsParts(uri);
    const relative = parts.join('/');
    if (!Object.hasOwn(listed, relative)) {
      throw new ReportError(`cited details file ${relative} is not listed in SHA256SUMS`);
    }
    let current = reportArtifact;
    for (const part of parts) {
      current = path.join(current, part);
      if (!existsSync(current) && !lstatSync(current, { throwIfNoEntry: false })?.isSymbolicLink()) {
        throw new ReportError(`cited details file ${relative} is missing`);
      }
      if (lstatSync(current).isSymbolicLink()) {
        throw new ReportError(`cited details file ${relative} is a symlink`);
      }
    }
    const bytes = readFileSync(current);
    const digest = sha256(bytes);
    if (digest !== listed[relative]) {
      throw new ReportError(
        `cited details file ${relative} does not match SHA256SUMS: sha256 is ${digest}, not ${listed[relative]}`
      );
    }
    const destination = path.join(directory, ...parts);
    mkdirSync(path.dirname(destination), { recursive: true });
    writeFileSync(destination, bytes);
  }
}

export function assembleVerifierDefinition(item, downloadRoot, outputDir, env, version) {
  for (const configuration of item.configurations ?? []) {
    assertConfigurationSlug(configuration, item.path ?? 'collect');
  }
  const destination = path.join(outputDir, item.id);
  rmSync(destination, { recursive: true, force: true });
  mkdirSync(destination, { recursive: true });
  for (const configuration of item.configurations) {
    const prefix = `${item.artifactPrefix}`;
    const reportArtifact = artifactDir(
      path.join(downloadRoot, 'reports'),
      `${prefix}capability-report-${configuration}`
    );
    if (!reportArtifact) {
      throw new ReportError(
        `${item.path}: capability-report artifact ${prefix}capability-report-${configuration} is missing, so no report can be written`
      );
    }
    for (const name of REPORT_FILES) {
      if (!existsSync(path.join(reportArtifact, name))) {
        throw new ReportError(`${item.path}: ${name} is missing from the capability report`);
      }
    }
    assertChecksums(reportArtifact);
    const directory = path.join(destination, configuration);
    mkdirSync(directory, { recursive: true });
    for (const name of REPORT_FILES) {
      writeFileSync(path.join(directory, name), readFileSync(path.join(reportArtifact, name)));
    }
    // The evaluator cites thresholds.json from the run folder. The reporter
    // uploads it beside the report; without this copy ./site refuses the run.
    const thresholds = path.join(reportArtifact, 'thresholds.json');
    if (existsSync(thresholds)) {
      writeFileSync(path.join(directory, 'thresholds.json'), readFileSync(thresholds));
    }
    const evidenceArtifact = artifactDir(
      path.join(downloadRoot, 'evidence'),
      `${prefix}evidence-${configuration}`
    );
    if (evidenceArtifact) copyOptional(evidenceArtifact, path.join(directory, 'evidence'));
    const mcapArtifact = artifactDir(path.join(downloadRoot, 'mcap'), `${prefix}mcap-${configuration}`);
    if (mcapArtifact) copyOptional(mcapArtifact, path.join(directory, 'evidence', 'mcap'));
    const videoRoot = path.join(downloadRoot, 'video');
    if (existsSync(videoRoot)) {
      for (const name of readdirSync(videoRoot)) {
        if (name.startsWith(`${prefix}video-${configuration}-`)) {
          copyOptional(path.join(videoRoot, name), path.join(directory, 'evidence', 'video'));
        }
      }
    }
    const report = JSON.parse(readFileSync(path.join(directory, 'report.json'), 'utf8'));
    copyCitedDetails(reportArtifact, directory, report);
    const summaries = readSummaries(report, path.join(directory, 'report.json'));
    const record = buildRunRecord({
      item,
      configuration,
      summary: summaries[0],
      env,
      version,
    });
    stampReportInputs(directory, record.inputs);
    writeFileSync(path.join(directory, 'run.json'), `${JSON.stringify(record, null, 2)}\n`, 'utf8');
    writeChecksums(directory);
    assertChecksums(directory);
  }
  return destination;
}

export function mergeFixtureArtifacts(fixtureRoot, outputDir, plan) {
  if (!fixtureRoot || !existsSync(fixtureRoot)) return;
  const allowed = new Set(
    (plan?.definitions ?? []).filter((item) => item.execution === 'fixture').map((item) => item.id)
  );
  for (const artifactName of readdirSync(fixtureRoot)) {
    const artifact = path.join(fixtureRoot, artifactName);
    if (!existsSync(artifact)) continue;
    let names;
    try {
      names = readdirSync(artifact);
    } catch {
      continue;
    }
    for (const id of names) {
      if (id.startsWith('.') || !allowed.has(id)) continue;
      const from = path.join(artifact, id);
      const to = path.join(outputDir, id);
      rmSync(to, { recursive: true, force: true });
      copyTreeContents(from, to);
    }
  }
}
