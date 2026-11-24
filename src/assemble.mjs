// SPDX-License-Identifier: Apache-2.0
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { ReportError, assertConfigurationSlug } from './definitions.mjs';
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

export function mergeFixtureArtifacts(fixtureRoot, outputDir) {
  if (!fixtureRoot || !existsSync(fixtureRoot)) return;
  for (const artifactName of readdirSync(fixtureRoot)) {
    const artifact = path.join(fixtureRoot, artifactName);
    for (const id of readdirSync(artifact)) {
      if (id.startsWith('.')) continue;
      const from = path.join(artifact, id);
      const to = path.join(outputDir, id);
      rmSync(to, { recursive: true, force: true });
      copyTreeContents(from, to);
    }
  }
}
