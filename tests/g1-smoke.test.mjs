// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { VERIFIER_WORKFLOW_SHA, VERIFIER_WORKFLOW_TAG, isFixtureDefinition, loadDefinition, planDefinitions } from '../src/definitions.mjs';
import { collectPins } from '../src/provenance.mjs';
import { verificationJobs, verifyResults } from '../tools/verification-gate.mjs';

const root = fileURLToPath(new URL('..', import.meta.url));
const definitionPath = 'tests/smoke/g1/unitree-g1-stock-mujoco-hallway.toml';
const G1_COMMIT = 'd554c8243a12849dc7fe8acc1921bb3d4e5239a7';
const RUNNER_COMMIT = '727d8706598e8ac706324ca87edd0973fdbb3485';

function readToml(relativePath) {
  const script = [
    'import json, pathlib, sys, tomllib',
    'print(json.dumps(tomllib.loads(pathlib.Path(sys.argv[1]).read_text())))',
  ].join('\n');
  const result = spawnSync('python3', ['-c', script, fileURLToPath(new URL(`../${relativePath}`, import.meta.url))], {
    encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

function indentOf(line) {
  return line.match(/^ */)[0].length;
}

function isBlank(line) {
  const trimmed = line.trim();
  return trimmed === '' || trimmed.startsWith('#');
}

function stripInlineComment(line) {
  let quote = '';
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (quote) {
      if (char === quote) quote = '';
      continue;
    }
    if (char === '"' || char === "'") {
      quote = char;
      continue;
    }
    if (char === '#' && (index === 0 || line[index - 1] === ' ')) return line.slice(0, index).trimEnd();
  }
  return line.trimEnd();
}

function parseFlowSequence(value) {
  const body = value.slice(1, -1).trim();
  if (!body) return [];
  const items = [];
  let current = '';
  let quote = '';
  for (const char of body) {
    if (quote) {
      current += char;
      if (char === quote) quote = '';
      continue;
    }
    if (char === '"' || char === "'") {
      quote = char;
      current += char;
      continue;
    }
    if (char === ',') {
      items.push(parseScalar(current.trim()));
      current = '';
      continue;
    }
    current += char;
  }
  if (current.trim()) items.push(parseScalar(current.trim()));
  return items;
}

function parseScalar(raw) {
  const value = raw.trim();
  if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
    return value.slice(1, -1);
  }
  if (value === 'true') return true;
  if (value === 'false') return false;
  if (value === 'null' || value === '~') return null;
  if (value.startsWith('[') && value.endsWith(']')) return parseFlowSequence(value);
  return value;
}

function parseWorkflowYaml(text) {
  const lines = text.split('\n');
  let index = 0;

  function skipNoise() {
    while (index < lines.length && isBlank(lines[index])) index += 1;
  }

  function readBlock(parentIndent, style) {
    const folded = style.startsWith('>');
    const strip = style.endsWith('-');
    const collected = [];
    let blockIndent = null;
    while (index < lines.length) {
      const line = lines[index];
      if (line.trim() === '') {
        collected.push('');
        index += 1;
        continue;
      }
      const indent = indentOf(line);
      if (blockIndent === null) {
        if (indent <= parentIndent) break;
        blockIndent = indent;
      }
      if (indent < blockIndent) break;
      collected.push(line.slice(blockIndent));
      index += 1;
    }
    while (collected.length > 0 && collected[collected.length - 1] === '') collected.pop();
    const body = folded
      ? collected.join('\n').replace(/\n+/g, ' ').replace(/ +/g, ' ').trim()
      : collected.join('\n');
    if (strip || body.length === 0) return body;
    return `${body}\n`;
  }

  function parseMap(minIndent) {
    const map = {};
    while (index < lines.length) {
      skipNoise();
      if (index >= lines.length) break;
      const line = stripInlineComment(lines[index]);
      const indent = indentOf(line);
      if (indent < minIndent || line.trimStart().startsWith('- ')) break;
      const trimmed = line.trim();
      const colon = trimmed.indexOf(':');
      if (colon <= 0) throw new Error(`Cannot parse workflow YAML line ${index + 1}: ${lines[index]}`);
      const key = trimmed.slice(0, colon).trim();
      const rest = trimmed.slice(colon + 1).trim();
      index += 1;
      if (rest === '|' || rest === '|-' || rest === '|+' || rest === '>' || rest === '>-' || rest === '>+') {
        map[key] = readBlock(indent, rest);
        continue;
      }
      if (rest === '') {
        skipNoise();
        if (index < lines.length && !isBlank(lines[index]) && indentOf(lines[index]) > indent) {
          map[key] = lines[index].trimStart().startsWith('- ') ? parseSeq(indentOf(lines[index])) : parseMap(indentOf(lines[index]));
        } else map[key] = null;
        continue;
      }
      map[key] = parseScalar(rest);
    }
    return map;
  }

  function parseSeq(minIndent) {
    const seq = [];
    while (index < lines.length) {
      skipNoise();
      if (index >= lines.length) break;
      const line = stripInlineComment(lines[index]);
      const indent = indentOf(line);
      if (indent < minIndent || !line.trimStart().startsWith('- ')) break;
      const rest = line.trimStart().slice(2).trim();
      index += 1;
      if (rest === '') {
        skipNoise();
        if (index < lines.length && indentOf(lines[index]) > indent) {
          seq.push(lines[index].trimStart().startsWith('- ') ? parseSeq(indentOf(lines[index])) : parseMap(indentOf(lines[index])));
        } else seq.push(null);
        continue;
      }
      const colon = rest.indexOf(':');
      if (colon > 0 && !rest.startsWith('[') && !rest.startsWith('"') && !rest.startsWith("'")) {
        const item = {};
        const key = rest.slice(0, colon).trim();
        const valueRest = rest.slice(colon + 1).trim();
        const keyIndent = indent + 2;
        if (valueRest === '|' || valueRest === '|-' || valueRest === '|+' || valueRest === '>' || valueRest === '>-' || valueRest === '>+') {
          item[key] = readBlock(keyIndent, valueRest);
        } else if (valueRest === '') {
          skipNoise();
          if (index < lines.length && indentOf(lines[index]) > keyIndent) {
            item[key] = lines[index].trimStart().startsWith('- ')
              ? parseSeq(indentOf(lines[index]))
              : parseMap(indentOf(lines[index]));
          } else item[key] = null;
        } else item[key] = parseScalar(valueRest);
        Object.assign(item, parseMap(keyIndent));
        seq.push(item);
        continue;
      }
      seq.push(parseScalar(rest));
    }
    return seq;
  }

  skipNoise();
  if (index >= lines.length) return {};
  return lines[index].trimStart().startsWith('- ') ? parseSeq(indentOf(lines[index])) : parseMap(indentOf(lines[index]));
}

function stepNamed(job, name) {
  const step = (job.steps ?? []).find((entry) => entry.name === name);
  assert.ok(step, `missing step ${name}`);
  return step;
}

const definition = readToml(definitionPath);
const lock = JSON.parse(readFileSync(new URL('../tests/smoke/g1/unitree-g1-stock-mujoco-hallway.lock.json', import.meta.url), 'utf8'));
const verifyWorkflow = parseWorkflowYaml(readFileSync(new URL('../.github/workflows/verify.yml', import.meta.url), 'utf8'));
const reportWorkflow = parseWorkflowYaml(readFileSync(new URL('../.github/workflows/report.yml', import.meta.url), 'utf8'));
const renovate = JSON.parse(readFileSync(new URL('../renovate.json', import.meta.url), 'utf8'));

test('the stock hallway definition and lock pin G1 v2.3.34 for one trial', () => {
  assert.equal(definition.id, 'unitree-g1-stock-mujoco-hallway');
  assert.equal(definition.procedure, 'walk-hallway');
  assert.equal(definition.scenario, 'walk-hallway@0.1.0');
  assert.equal(definition.robot.repository, 'Living-Proof-Labs/livingproof-unitree-g1');
  assert.equal(definition.robot.ref, 'v2.3.34');
  assert.deepEqual(definition.robot.configurations, ['stock']);
  assert.equal(definition.plan.trials.count, 1);
  assert.equal(definition.world.id, 'hallway-open');

  assert.equal(lock.robot.repository, 'Living-Proof-Labs/livingproof-unitree-g1');
  assert.equal(lock.robot.ref, 'v2.3.34');
  assert.equal(lock.robot.commit, G1_COMMIT);
  assert.equal(lock.plan.trials.count, 1);
  assert.equal(lock.procedure, 'walk-hallway');
  assert.equal(lock.releases.runner.tag, 'v3.9.7');
  assert.equal(lock.releases.runner.commit, RUNNER_COMMIT);
  assert.equal(lock.releases.verifier.tag, VERIFIER_WORKFLOW_TAG);
  assert.equal(lock.releases.verifier.commit, undefined);
  assert.equal(lock.releases.verifier.image, undefined);
  assert.equal(lock.releases.verifier.digest, undefined);
  assert.equal(lock.releases.reporter.image, undefined);
  assert.equal(lock.releases.reporter.digest, undefined);
  assert.equal(lock.evaluators['livingproof-evaluator-scenario'], undefined);
  assert.equal(lock.evaluators['livingproof-evaluator-run'].repository, 'Living-Proof-Labs/livingproof-evaluator-run');
  assert.equal(lock.evaluators['livingproof-evaluator-run'].tag, 'v1.8.6');
});

test('the G1 definition is planned through capability-verifier, not the fixture runner', () => {
  assert.equal(
    isFixtureDefinition(definitionPath, 'Living-Proof-Labs/verification-reports-action', 'Living-Proof-Labs/verification-reports-action'),
    false,
  );
  const loaded = loadDefinition(definitionPath, root);
  const pins = collectPins(loaded);
  const verifier = pins.find((pin) => pin.name === 'capability-verifier');
  assert.equal(pins.some((pin) => pin.name === 'livingproof-evaluator-run'), false);
  assert.equal(verifier.tag, VERIFIER_WORKFLOW_TAG);

  const plan = planDefinitions(
    {
      definitions: 'tests/smoke/g1/*.toml',
      repository: 'Living-Proof-Labs/livingproof-unitree-g1',
      ref: G1_COMMIT,
    },
    {
      GITHUB_REPOSITORY: 'Living-Proof-Labs/verification-reports-action',
      GITHUB_SHA: 'a'.repeat(40),
      WORKFLOW_REPOSITORY: 'Living-Proof-Labs/verification-reports-action',
    },
    root,
  );
  assert.equal(plan.definitions.length, 1);
  assert.equal(plan.definitions[0].execution, 'capability-verifier');
  assert.equal(plan.definitions[0].procedure, 'walk-hallway');
  assert.equal(plan.definitions[0].robot.repository, 'Living-Proof-Labs/livingproof-unitree-g1');
  assert.equal(plan.definitions[0].robot.ref, G1_COMMIT);
});

test('Renovate leaves the G1 smoke pin file disabled', () => {
  const rule = (renovate.packageRules ?? []).find(
    (entry) => entry.enabled === false && (entry.matchFileNames ?? []).includes('tests/smoke/g1/**'),
  );
  assert.ok(rule, 'renovate.json must disable updates under tests/smoke/g1');
});

const gateNeeds = [
  'verify',
  'site-policy',
  'version',
  'action-smoke',
  'action-smoke-check',
  'g1-smoke',
  'g1-smoke-check',
  'site-smoke',
];

test('the mock smoke and the real G1 smoke are both required, and their artifacts differ', () => {
  assert.equal(verifyWorkflow.on.pull_request, null);
  assert.ok(verifyWorkflow.on.merge_group);
  assert.deepEqual(verifyWorkflow.on.push.branches, ['main']);

  const mock = verifyWorkflow.jobs['action-smoke'];
  assert.equal(mock.name, 'Mock robot smoke');
  assert.equal(mock.needs, 'version');
  assert.equal(mock.uses, './.github/workflows/report.yml');
  assert.equal(mock.with.definitions, 'tests/fixtures/robot/verifications/*.toml');
  assert.equal(mock.with['artifact-name'], undefined);
  assert.match(String(mock.if), /needs\.version\.result == 'skipped'/);
  const mockCheck = verifyWorkflow.jobs['action-smoke-check'];
  assert.deepEqual(mockCheck.needs, ['version', 'action-smoke']);
  const mockUpload = mockCheck.steps.find((step) => step.uses && step.uses.startsWith('actions/upload-artifact@'));
  assert.equal(mockUpload.with.name, 'smoke-report-${{ needs.version.outputs.semver-version }}');

  const smoke = verifyWorkflow.jobs['g1-smoke'];
  assert.equal(smoke.name, 'Real G1 smoke');
  assert.equal(smoke.needs, 'version');
  assert.equal(smoke.uses, './.github/workflows/report.yml');
  assert.equal(smoke.with.definitions, 'tests/smoke/g1/*.toml');
  assert.equal(smoke.with.repository, 'Living-Proof-Labs/livingproof-unitree-g1');
  assert.equal(smoke.with.ref, G1_COMMIT);
  assert.equal(smoke.with['artifact-name'], 'g1-verification-report');
  assert.notEqual(smoke.with['artifact-name'], 'verification-report');
  assert.match(String(smoke.if), /needs\.version\.result == 'skipped'/);

  const check = verifyWorkflow.jobs['g1-smoke-check'];
  assert.deepEqual(check.needs, ['version', 'g1-smoke']);
  assert.equal(check.env.G1_REF, 'v2.3.34');
  assert.equal(check.env.G1_COMMIT, G1_COMMIT);
  const g1Download = check.steps.find((step) => step.uses && step.uses.startsWith('actions/download-artifact@'));
  assert.equal(g1Download.with.name, 'g1-verification-report');
  const reportScript = stepNamed(check, 'Verify the full report').run;
  assert.match(reportScript, /report\.html/);
  assert.match(reportScript, /^set -x$/m);
  assert.match(reportScript, /evidence\/manifest\.json/);
  assert.match(reportScript, /\.trials\.results \| length/);
  assert.match(reportScript, /\.trials\.count/);
  assert.match(reportScript, /livingproof-hallway-observation\/2\.0\.0/);
  assert.doesNotMatch(reportScript, /evidence\/observation\.json/);
  assert.match(reportScript, /summaries\[0\]\.n' "\$dir\/report\.json"\)" = "1"/);
  assert.match(reportScript, /definitions\[0\]\}" = "unitree-g1-stock-mujoco-hallway"/);
  assert.match(reportScript, /test ! -e artifacts\/smoke-report\/fixture-hallway/);
  assert.match(reportScript, /test ! -e artifacts\/smoke-report\/fixture-scenario/);
  assert.doesNotMatch(reportScript, /= "PASS"/);
  const g1Site = check.steps.find((step) => step.uses === './site');
  assert.ok(g1Site, 'g1-smoke-check must build the real report with ./site');
  assert.equal(g1Site.with['runs-dir'], 'artifacts/smoke-report');
  assert.equal(g1Site.with['output-dir'], 'artifacts/g1-site-check');
  assert.equal(g1Site.with['reset-history'], 'false');
  assert.equal(g1Site.with['restore-history'], 'false');
  assert.equal(g1Site.with['upload-artifact'], 'false');
  const g1DownloadAt = check.steps.indexOf(g1Download);
  const g1SiteAt = check.steps.indexOf(g1Site);
  assert.ok(g1DownloadAt !== -1 && g1SiteAt > g1DownloadAt);
  const g1Require = stepNamed(check, 'Require the G1 site build');
  assert.equal(g1Require.if, 'always()');
  assert.match(g1Require.run, /require_reports\.py g1 artifacts\/g1-site-check/);
  assert.match(g1Require.env.SITE_STATUS, /steps\.g1-site\.outcome/);
  assert.doesNotMatch(g1Require.run, /test -f/);
  assert.ok(check.steps.indexOf(g1Require) > g1SiteAt);

  const site = verifyWorkflow.jobs['site-smoke'];
  assert.equal(site.needs, 'action-smoke');
  const siteDownload = site.steps.find((step) => step.uses && step.uses.startsWith('actions/download-artifact@'));
  assert.equal(siteDownload.with.name, 'verification-report');
  const siteBuild = site.steps.find((step) => step.uses === './site');
  assert.equal(siteBuild.with['reset-history'], 'true');

  assert.equal(reportWorkflow.on.workflow_call.inputs['artifact-name'].default, 'verification-report');
  const collectSteps = reportWorkflow.jobs.collect.steps;
  const reportUpload = collectSteps.find(
    (step) =>
      step.uses &&
      step.uses.startsWith('actions/upload-artifact@') &&
      step.with?.name === '${{ inputs.artifact-name }}',
  );
  assert.equal(reportUpload.with.name, '${{ inputs.artifact-name }}');
  const declarationFailure = collectSteps.find(
    (step) => step.name === 'Fail when an evaluator declaration produced no provenance',
  );
  assert.ok(collectSteps.indexOf(declarationFailure) > collectSteps.indexOf(reportUpload));
  const planArtifactName =
    "${{ inputs.artifact-name == 'verification-report' && 'verification-plan' || format('{0}-plan', inputs.artifact-name) }}";
  const planSteps = ['plan', 'fixture', 'collect'].flatMap((jobName) =>
    reportWorkflow.jobs[jobName].steps.filter(
      (step) => typeof step.with?.path === 'string' && step.with.path.startsWith('artifacts/verification-plan'),
    ),
  );
  assert.equal(planSteps.length, 3);
  for (const step of planSteps) {
    assert.equal(step.with.name, planArtifactName);
    assert.notEqual(step.with.name, 'verification-plan');
  }
  const fixtureDownload = reportWorkflow.jobs.collect.steps.find((step) =>
    String(step.with?.path ?? '').includes('downloaded/fixture'),
  );
  assert.match(String(fixtureDownload.with.pattern), /inputs\.artifact-name/);
  assert.notEqual(fixtureDownload.with.pattern, 'vra-fixture-*');
  assert.equal(
    reportWorkflow.jobs['walk-hallway'].uses,
    `Living-Proof-Labs/capability-verifier/.github/workflows/walk-hallway.yml@${VERIFIER_WORKFLOW_SHA}`,
  );
  assert.equal(
    reportWorkflow.jobs.scenario.uses,
    `Living-Proof-Labs/capability-verifier/.github/workflows/scenario.yml@${VERIFIER_WORKFLOW_SHA}`,
  );
  assert.equal('evaluator-commit' in reportWorkflow.jobs['walk-hallway'].with, false);
  assert.equal('evaluator-commit' in reportWorkflow.jobs.scenario.with, false);
  assert.equal(reportWorkflow.jobs['walk-hallway'].with.evaluators, '${{ needs.plan.outputs.evaluators }}');
  assert.equal(reportWorkflow.jobs.scenario.with.evaluators, '${{ needs.plan.outputs.evaluators }}');

  assert.deepEqual(verifyWorkflow.jobs['verification-gate'].needs, gateNeeds);
  assert.deepEqual(verificationJobs, gateNeeds);
  for (const job of ['action-smoke', 'action-smoke-check', 'g1-smoke', 'g1-smoke-check']) {
    assert.ok(verifyWorkflow.jobs['verification-gate'].needs.includes(job), `${job} is required by the gate`);
  }
});

test('removing either smoke job from the gate fails the gate', () => {
  for (const event of ['pull_request', 'merge_group', 'push']) {
    const baseRef = event === 'pull_request' ? 'main' : '';
    const version = event === 'push' || baseRef === 'main' ? 'success' : 'skipped';
    const needs = Object.fromEntries(verificationJobs.map((job) => [job, { result: job === 'version' ? version : 'success' }]));
    for (const job of ['action-smoke', 'g1-smoke']) {
      const failed = structuredClone(needs);
      failed[job].result = 'failure';
      assert.throws(() => verifyResults(failed, event, baseRef), new RegExp(`${job} must be success`));
    }
  }
});
