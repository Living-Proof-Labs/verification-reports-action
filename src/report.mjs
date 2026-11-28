// SPDX-License-Identifier: Apache-2.0
import { mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { assertConfigurationSlug, sha256 } from './definitions.mjs';
import { fixtureProvenance } from './provenance.mjs';

const REPORT_FILES = ['report.html', 'report.md', 'report.json', 'report.txt'];

function createdStamp(env) {
  const supplied = (env.VERIFICATION_CREATED ?? '').trim();
  if (supplied) return supplied;
  return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
}

export function renderReport(summary) {
  const line = `${summary.capability}: overall ${summary.grade}.`;
  const document = {
    report_version: '1.0.0',
    summaries: [summary],
  };
  return {
    'report.json': `${JSON.stringify(document, null, 2)}\n`,
    'report.md': `# Verification report\n\n${line}\n`,
    'report.txt': `${line}\n`,
    'report.html': `<!DOCTYPE html>\n<html><body><p>${line}</p></body></html>\n`,
  };
}

export function readSummaries(report, where) {
  if (!report || typeof report !== 'object' || Array.isArray(report)) {
    throw new Error(`${where}: report.json must be a JSON object`);
  }
  if (report.report_version !== '1.0.0') {
    throw new Error(
      `${where}: report.json report_version ${JSON.stringify(report.report_version)} is not 1.0.0`
    );
  }
  if (!Array.isArray(report.summaries) || report.summaries.length === 0) {
    throw new Error(`${where}: report.json has no summaries`);
  }
  return report.summaries.map((summary, index) => {
    if (!summary || typeof summary !== 'object') {
      throw new Error(`${where}: summaries[${index}] is not an object`);
    }
    if (typeof summary.grade !== 'string' || !/^[A-Z][A-Z0-9_]*$/.test(summary.grade)) {
      throw new Error(`${where}: summaries[${index}].grade is missing`);
    }
    if (!summary.system || typeof summary.system.id !== 'string') {
      throw new Error(`${where}: summaries[${index}].system.id is missing`);
    }
    if (typeof summary.capability !== 'string' || !summary.capability) {
      throw new Error(`${where}: summaries[${index}].capability is missing`);
    }
    return summary;
  });
}

export function buildRunRecord({ item, configuration, summary, env, version }) {
  const server = (env.GITHUB_SERVER_URL ?? 'https://github.com').replace(/\/$/, '');
  const tested = item.robot;
  const caller = item.callerRepository || tested.repository;
  const runId = String(env.GITHUB_RUN_ID ?? 'local');
  const releases = item.lock?.releases && typeof item.lock.releases === 'object' ? item.lock.releases : {};
  const withUrls = {};
  for (const [name, release] of Object.entries(releases)) {
    if (!release || typeof release !== 'object') continue;
    const entry = { ...release };
    if (typeof release.repository === 'string' && typeof release.tag === 'string') {
      entry.url = `${server}/${release.repository}/releases/tag/${release.tag}`;
    }
    withUrls[name] = entry;
  }
  return {
    fixture: item.execution === 'fixture',
    robot: {
      repository: tested.repository,
      commit: tested.ref,
      configuration,
      url: `${server}/${tested.repository}/tree/${tested.ref}`,
    },
    variant: item.id,
    verification: item.id,
    procedure: item.procedure,
    outcome: [
      {
        capability: summary.capability,
        system: summary.system.id,
        grade: summary.grade,
        passed: summary.k ?? null,
        trials: summary.n ?? null,
        required: summary.required_passes ?? null,
      },
    ],
    run: {
      id: runId,
      url: `${server}/${caller}/actions/runs/${runId}`,
      created: createdStamp(env),
    },
    commit: {
      sha: tested.ref,
      url: `${server}/${tested.repository}/commit/${tested.ref}`,
    },
    releases: withUrls,
    definition: item.path,
    version: version ?? '',
    declared_robot: item.lock?.robot ?? {},
    inputs: Array.isArray(item.provenance)
      ? item.provenance
      : item.execution === 'fixture'
        ? fixtureProvenance(item)
        : [],
  };
}

const STATUS_PHRASE = {
  verified: 'verified (attestation matched)',
  'not-published': 'not-published (no attestation published; commit checked)',
  resolved: 'resolved (no published commit; tag peeled)',
  fixture: 'fixture (this repository\'s fixture; release not checked)',
};

/** One glanceable line. The status word is the provenance enum. */
export function pinnedInputLine(input) {
  const phrase = STATUS_PHRASE[input?.status] ?? String(input?.status ?? '');
  return `${input?.name}: ${phrase}`;
}

export function pinnedInputsText(inputs) {
  const lines = (inputs ?? []).map((input) => pinnedInputLine(input));
  return ['## Pinned inputs', '', ...lines, ''].join('\n');
}

export function pinnedInputsHtml(inputs) {
  const lines = (inputs ?? []).map((input) => `<p>${pinnedInputLine(input)}</p>`).join('\n');
  return `<section id="pinned-inputs">\n<h2>Pinned inputs</h2>\n${lines}\n</section>\n`;
}

function spliceMarked(text, section) {
  const start = '<!-- pinned-inputs -->';
  const end = '<!-- /pinned-inputs -->';
  const block = `${start}\n${section}${end}\n`;
  const pattern = new RegExp(`${start}[\\s\\S]*?${end}\\n?`);
  if (pattern.test(text)) return text.replace(pattern, block);
  return `${text.replace(/\s*$/, '\n')}${block}`;
}

function splicePlain(text, section) {
  const heading = '## Pinned inputs\n';
  const start = text.indexOf(heading);
  const base = start === -1 ? text : text.slice(0, start);
  return `${base.replace(/\s*$/, '\n')}${section}`;
}

function spliceHtml(text, section) {
  const pattern = /<section id="pinned-inputs">[\s\S]*?<\/section>\n?/;
  if (pattern.test(text)) return text.replace(pattern, section);
  if (text.includes('</body>')) return text.replace('</body>', `${section}</body>`);
  return `${text.replace(/\s*$/, '\n')}${section}`;
}

export function stampReportInputs(directory, inputs) {
  if (!Array.isArray(inputs)) return;
  const reportPath = path.join(directory, 'report.json');
  const report = JSON.parse(readFileSync(reportPath, 'utf8'));
  report.inputs = inputs;
  writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  const markdown = pinnedInputsText(inputs);
  const markdownFile = path.join(directory, 'report.md');
  writeFileSync(markdownFile, spliceMarked(readFileSync(markdownFile, 'utf8'), markdown), 'utf8');
  const textFile = path.join(directory, 'report.txt');
  writeFileSync(textFile, splicePlain(readFileSync(textFile, 'utf8'), markdown), 'utf8');
  const htmlFile = path.join(directory, 'report.html');
  writeFileSync(htmlFile, spliceHtml(readFileSync(htmlFile, 'utf8'), pinnedInputsHtml(inputs)), 'utf8');
}

export function listFiles(directory) {
  const found = [];
  const walk = (current) => {
    for (const name of readdirSync(current, { withFileTypes: true })) {
      const absolute = path.join(current, name.name);
      if (name.isDirectory()) walk(absolute);
      else if (name.isFile() && name.name !== 'SHA256SUMS') found.push(absolute);
    }
  };
  walk(directory);
  return found.sort();
}

export function writeChecksums(directory) {
  const lines = listFiles(directory).map((absolute) => {
    const relative = path.relative(directory, absolute).split(path.sep).join('/');
    return `${sha256(readFileSync(absolute))}  ${relative}`;
  });
  writeFileSync(path.join(directory, 'SHA256SUMS'), `${lines.join('\n')}\n`, 'utf8');
}

export function assertChecksums(directory) {
  const sums = path.join(directory, 'SHA256SUMS');
  const text = readFileSync(sums, 'utf8');
  const lines = text.split('\n').filter((line) => line.length > 0);
  if (lines.length === 0) throw new Error(`${directory}: SHA256SUMS is empty`);
  for (const line of lines) {
    const match = line.match(/^([0-9a-f]{64})  (.+)$/);
    if (!match) throw new Error(`${directory}: SHA256SUMS has an unreadable line`);
    const absolute = path.join(directory, match[2]);
    const digest = sha256(readFileSync(absolute));
    if (digest !== match[1]) {
      throw new Error(`${absolute}: sha256 is ${digest}, not ${match[1]}`);
    }
  }
}

export function writeConfiguration(directory, { files, evidence, runRecord }) {
  rmSync(directory, { recursive: true, force: true });
  mkdirSync(directory, { recursive: true });
  for (const [name, contents] of Object.entries(files)) {
    writeFileSync(path.join(directory, name), contents, 'utf8');
  }
  stampReportInputs(directory, runRecord.inputs);
  if (evidence) copyTree(evidence, path.join(directory, 'evidence'));
  writeFileSync(path.join(directory, 'run.json'), `${JSON.stringify(runRecord, null, 2)}\n`, 'utf8');
  writeChecksums(directory);
  assertChecksums(directory);
  for (const name of REPORT_FILES) {
    if (!readFileSync(path.join(directory, name), 'utf8')) {
      throw new Error(`${directory}: ${name} is empty`);
    }
  }
}

function copyTree(source, destination) {
  mkdirSync(destination, { recursive: true });
  for (const name of readdirSync(source, { withFileTypes: true })) {
    const from = path.join(source, name.name);
    const to = path.join(destination, name.name);
    if (name.isDirectory()) copyTree(from, to);
    else if (name.isFile()) writeFileSync(to, readFileSync(from));
  }
}

export function copyTreeContents(source, destination) {
  copyTree(source, destination);
}

export function gradeMap(outputDir, plan) {
  const grades = {};
  for (const item of plan.definitions) {
    for (const configuration of item.configurations) {
      assertConfigurationSlug(configuration, item.path ?? 'collect');
      const directory = path.join(outputDir, item.id, configuration);
      const reportPath = path.join(directory, 'report.json');
      const report = JSON.parse(readFileSync(reportPath, 'utf8'));
      const summaries = readSummaries(report, reportPath);
      assertChecksums(directory);
      grades[`${item.id}/${configuration}`] = summaries.map((summary) => summary.grade).join(',');
    }
  }
  return grades;
}

export function outputsFromGrades(grades, reportPath) {
  const values = Object.values(grades);
  const grade = values.length === 1 ? values[0] : JSON.stringify(grades);
  return {
    'report-path': reportPath,
    status: 'ok',
    grade,
    grades: JSON.stringify(grades),
  };
}
