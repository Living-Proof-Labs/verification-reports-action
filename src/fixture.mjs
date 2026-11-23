// SPDX-License-Identifier: Apache-2.0
import { existsSync, readFileSync, renameSync, rmSync } from 'node:fs';
import path from 'node:path';
import { ReportError } from './definitions.mjs';
import { buildRunRecord, renderReport, writeConfiguration } from './report.mjs';

export function expectedConfigurationDir(root, id, configuration) {
  return path.join(root, 'tests/fixtures/robot/expected', id, configuration);
}

export function runFixtureDefinition(item, { root, outputDir, env, version }) {
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
        files: renderReport(summary),
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
