// SPDX-License-Identifier: Apache-2.0
import { existsSync, readFileSync, renameSync, rmSync } from 'node:fs';
import path from 'node:path';
import { ReportError, assertConfigurationSlug } from './definitions.mjs';
import { REPORT_FILES, buildRunRecord, readSummaries, writeConfiguration } from './report.mjs';

export function expectedConfigurationDir(root, id, configuration) {
  return path.join(root, 'tests/fixtures/robot/expected', id, configuration);
}

/**
 * A fixture's report files are committed beside its expected.json. The action renders no
 * report: capability-report draws every page (#75).
 */
export function fixtureReportFiles(expected, summary, where) {
  const files = {};
  for (const name of REPORT_FILES) {
    const file = path.join(expected, name);
    if (!existsSync(file)) {
      throw new ReportError(`${where}: fixture has no ${name}, so no report can be written`);
    }
    files[name] = readFileSync(file, 'utf8');
  }
  const [reported] = readSummaries(JSON.parse(files['report.json']), `${where} report.json`);
  if (reported.grade !== summary.grade || reported.capability !== summary.capability) {
    throw new ReportError(`${where}: report.json does not match expected.json`);
  }
  return files;
}

export function runFixtureDefinition(item, { root, outputDir, env, version }) {
  for (const configuration of item.configurations ?? []) {
    assertConfigurationSlug(configuration, item.path ?? 'fixture');
  }
  const destination = path.join(outputDir, item.id);
  const staging = path.join(outputDir, `.partial-${item.id}`);
  rmSync(staging, { recursive: true, force: true });
  try {
    for (const configuration of item.configurations) {
      const expected = expectedConfigurationDir(root, item.id, configuration);
      const manifestPath = path.join(expected, 'expected.json');
      if (!existsSync(manifestPath)) {
        throw new ReportError(
          `${item.path}: fixture ${item.id}/${configuration} has no expected.json, so no report can be written`
        );
      }
      const summary = JSON.parse(readFileSync(manifestPath, 'utf8'));
      if (typeof summary.grade !== 'string' || !summary.grade) {
        throw new ReportError(`${manifestPath}: grade is missing`);
      }
      const evidence = path.join(expected, 'evidence');
      const record = buildRunRecord({ item, configuration, summary, env, version });
      writeConfiguration(path.join(staging, configuration), {
        files: fixtureReportFiles(expected, summary, `${item.path}: ${item.id}/${configuration}`),
        evidence: existsSync(evidence) ? evidence : null,
        runRecord: record,
      });
    }
    rmSync(destination, { recursive: true, force: true });
    renameSync(staging, destination);
    return destination;
  } catch (error) {
    rmSync(staging, { recursive: true, force: true });
    rmSync(destination, { recursive: true, force: true });
    throw error;
  }
}
