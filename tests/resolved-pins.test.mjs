// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync, cpSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { VERIFIER_WORKFLOW_SHA, VERIFIER_WORKFLOW_TAG } from '../src/definitions.mjs';
import { imageDigestFromBody } from '../src/github-provenance.mjs';
import { writePlan } from '../src/plan.mjs';
import { checkPin, collectPins, resolvedTrialInputs } from '../src/provenance.mjs';

const OTHER = '1111111111111111111111111111111111111111';
const RELEASE = JSON.parse(
  readFileSync(new URL('./fixtures/releases/capability-verifier-v2.12.0.json', import.meta.url), 'utf8')
);
const EVALUATOR_RELEASE = JSON.parse(
  readFileSync(new URL('./fixtures/releases/evaluator-scenario-v1.5.3.json', import.meta.url), 'utf8')
);

function realLock(id = 'unitree-g1-stock-mujoco-corner-left') {
  return JSON.parse(readFileSync(new URL(`./fixtures/real-locks/${id}.lock.json`, import.meta.url), 'utf8'));
}

function realResult(id = 'unitree-g1-stock-mujoco-corner-left') {
  return JSON.parse(readFileSync(new URL(`./fixtures/real-locks/${id}.result.json`, import.meta.url), 'utf8'));
}

function dropPassedFields(lock) {
  const copy = structuredClone(lock);
  delete copy.releases.verifier.commit;
  delete copy.releases.verifier.image;
  delete copy.releases.verifier.digest;
  delete copy.releases.reporter.image;
  delete copy.releases.reporter.digest;
  delete copy.evaluators['livingproof-evaluator-scenario'].commit;
  return copy;
}

function item(lock) {
  return { path: 'verifications/corner.toml', id: 'corner', lock, sources: {} };
}

function evidenceFor(pin, recorded) {
  const files = {};
  for (const file of pin.paths) {
    const hash = pin.lockRaw.files?.[file] || pin.lockRaw.sha256 || pin.sourceWorld?.sha256 || recorded.files?.[file];
    files[file] = { published: hash, found: hash };
  }
  if (pin.kind === 'image') {
    return {
      releaseFound: true,
      tagCommit: recorded.commit,
      publishedCommit: null,
      files,
      imageDigest: recorded.digest,
      publishedDigest: recorded.digest,
      sourceDigest: null,
      attestation: { published: false, status: 404 },
    };
  }
  if (pin.paths.length > 0) {
    return {
      releaseFound: true,
      tagCommit: recorded.commit,
      publishedCommit: null,
      files,
      imageDigest: null,
      publishedDigest: null,
      sourceDigest: null,
      attestation: { published: false, status: 404 },
    };
  }
  return {
    releaseFound: true,
    tagCommit: recorded.commit,
    publishedCommit: null,
    files,
    imageDigest: null,
    publishedDigest: recorded.digest,
    sourceDigest: recorded.digest,
    attestation: { published: false, status: 404 },
  };
}

function recordedByName(id) {
  return new Map(realResult(id).map((entry) => [entry.name, entry]));
}

function passReal(lock, mutate = () => {}) {
  const recorded = recordedByName();
  const verifierDigest = imageDigestFromBody(RELEASE.body, RELEASE.image);
  assert.equal(verifierDigest, RELEASE.digest);
  const pins = collectPins(item(lock));
  const records = [];
  for (const pin of pins) {
    const expected = recorded.get(pin.name);
    const evidence = evidenceFor(
      pin,
      pin.name === 'capability-verifier'
        ? { commit: RELEASE.tagCommit, digest: verifierDigest }
        : { commit: expected.commit, digest: expected.digest }
    );
    mutate(pin, evidence);
    records.push(checkPin(pin, evidence));
  }
  return records;
}

test('the v2.12.0 release fixture is the commit and hallway digest the workflow pins', () => {
  assert.equal(RELEASE.tagCommit, VERIFIER_WORKFLOW_SHA);
  assert.equal(RELEASE.tag, VERIFIER_WORKFLOW_TAG);
  assert.equal(imageDigestFromBody(RELEASE.body, RELEASE.image), RELEASE.digest);
  assert.equal(EVALUATOR_RELEASE.tagCommit, 'ca9d337a771269d07feec94c99f199bef7fca808');
});

test('each passed field on the real unitree lock is refused even when the value matches the release', () => {
  const published = realLock();
  const cases = [
    ['releases.verifier.commit', () => published.releases.verifier.commit, /releases\.verifier\.commit is refused/],
    ['releases.verifier.image', () => published.releases.verifier.image, /releases\.verifier\.image is refused/],
    ['releases.verifier.digest', () => published.releases.verifier.digest, /releases\.verifier\.digest is refused/],
    ['releases.reporter.image', () => published.releases.reporter.image, /releases\.reporter\.image is refused/],
    ['releases.reporter.digest', () => published.releases.reporter.digest, /releases\.reporter\.digest is refused/],
    [
      'evaluators.livingproof-evaluator-scenario.commit',
      () => published.evaluators['livingproof-evaluator-scenario'].commit,
      /evaluators\.livingproof-evaluator-scenario\.commit is refused/,
    ],
  ];
  for (const [label, value, pattern] of cases) {
    const lock = dropPassedFields(published);
    lock.releases.verifier.tag = VERIFIER_WORKFLOW_TAG;
    const [head, ...rest] = label.split('.');
    let cursor = lock;
    if (head === 'evaluators') {
      cursor = lock.evaluators['livingproof-evaluator-scenario'];
      cursor.commit = value();
    } else {
      const field = rest.at(-1);
      cursor = lock.releases[rest[0]];
      cursor[field] = value();
    }
    assert.throws(() => collectPins(item(lock)), pattern, label);
  }
});

test('a one-line verifier tag edit on the real lock passes the v2.12.0 commit and image', () => {
  const lock = dropPassedFields(realLock());
  assert.equal(lock.releases.verifier.tag, 'v2.11.4');
  lock.releases.verifier.tag = VERIFIER_WORKFLOW_TAG;
  const records = passReal(lock);
  const inputs = resolvedTrialInputs(records);
  assert.equal(inputs.verifier_commit, RELEASE.tagCommit);
  assert.equal(inputs.verifier_image, `${RELEASE.image}:2.12.0@${RELEASE.digest}`);
  const reporter = recordedByName().get('livingproof-capability-report');
  assert.equal(
    inputs.reporter_image,
    `ghcr.io/living-proof-labs/livingproof-capability-report:3.15.4@${reporter.digest}`
  );
  assert.equal(inputs.evaluator_commit, EVALUATOR_RELEASE.tagCommit);
  assert.equal(lock.releases.runner.commit, realLock().releases.runner.commit);
});

test('a one-line scenario-evaluator tag edit resolves the real v1.5.3 commit', () => {
  const lock = dropPassedFields(realLock());
  lock.releases.verifier.tag = VERIFIER_WORKFLOW_TAG;
  lock.evaluators['livingproof-evaluator-scenario'].tag = 'v1.5.2';
  const before = passReal(lock, (pin, evidence) => {
    if (pin.name !== 'livingproof-evaluator-scenario') return;
    evidence.tagCommit = OTHER;
  });
  assert.equal(resolvedTrialInputs(before).evaluator_commit, OTHER);
  lock.evaluators['livingproof-evaluator-scenario'].tag = EVALUATOR_RELEASE.tag;
  const after = passReal(lock);
  assert.equal(resolvedTrialInputs(after).evaluator_commit, EVALUATOR_RELEASE.tagCommit);
});

test('a runner, specs, or world tag edit that leaves the real commit and hashes is refused', () => {
  const base = dropPassedFields(realLock());
  base.releases.verifier.tag = VERIFIER_WORKFLOW_TAG;

  const runner = structuredClone(base);
  runner.releases.runner.tag = 'v3.9.10';
  assert.throws(
    () =>
      passReal(runner, (pin, evidence) => {
        if (pin.name !== 'livingproof-sim-mujoco') return;
        evidence.tagCommit = OTHER;
      }),
    new RegExp(`livingproof-sim-mujoco: releases\\.runner\\.commit differs from the resolved tag\\. expected ${OTHER}`)
  );

  const specs = structuredClone(base);
  specs.releases.specs.tag = 'v3.2.0';
  specs.releases.specs.commit = OTHER;
  assert.throws(
    () =>
      passReal(specs, (pin, evidence) => {
        if (pin.name !== 'robot-capability-specs') return;
        evidence.tagCommit = OTHER;
        for (const file of Object.keys(evidence.files)) {
          evidence.files[file] = { published: 'ab'.repeat(32), found: 'ab'.repeat(32) };
        }
      }),
    /robot-capability-specs: releases\.specs\.files\[/
  );

  const specsCommit = structuredClone(base);
  specsCommit.releases.specs.tag = 'v3.2.0';
  assert.throws(
    () =>
      passReal(specsCommit, (pin, evidence) => {
        if (pin.name !== 'robot-capability-specs') return;
        evidence.tagCommit = OTHER;
      }),
    new RegExp(`robot-capability-specs: releases\\.specs\\.commit differs from the resolved tag\\. expected ${OTHER}`)
  );

  const world = structuredClone(base);
  world.world.tag = 'v1.5.0';
  world.world.commit = OTHER;
  assert.throws(
    () =>
      passReal(world, (pin, evidence) => {
        if (pin.name !== 'livingproof-worlds') return;
        evidence.tagCommit = OTHER;
        const found = 'cd'.repeat(32);
        for (const file of Object.keys(evidence.files)) evidence.files[file] = { published: found, found };
      }),
    /livingproof-worlds: world\.sha256 differs from the resolved tag/
  );

  const worldCommit = structuredClone(base);
  worldCommit.world.tag = 'v1.5.0';
  assert.throws(
    () =>
      passReal(worldCommit, (pin, evidence) => {
        if (pin.name !== 'livingproof-worlds') return;
        evidence.tagCommit = OTHER;
      }),
    new RegExp(`livingproof-worlds: world\\.commit differs from the resolved tag\\. expected ${OTHER}`)
  );
});

test('the real sources.parameters and sources.world hashes are refused when they disagree', () => {
  const parameters = dropPassedFields(realLock());
  parameters.releases.verifier.tag = VERIFIER_WORKFLOW_TAG;
  parameters.sources.parameters.sha256 = 'ff'.repeat(32);
  assert.throws(
    () => passReal(parameters),
    /robot-capability-specs: sources\.parameters\.sha256 differs from the resolved tag/
  );

  const world = dropPassedFields(realLock());
  world.releases.verifier.tag = VERIFIER_WORKFLOW_TAG;
  world.sources.world.sha256 = 'ff'.repeat(32);
  assert.throws(() => passReal(world), /livingproof-worlds: sources\.world\.sha256 differs from the resolved tag/);
});

test('a reporter commit and an evaluator-run commit that disagree with the real tag are refused', () => {
  const reporter = dropPassedFields(realLock());
  reporter.releases.verifier.tag = VERIFIER_WORKFLOW_TAG;
  assert.throws(
    () =>
      passReal(reporter, (pin, evidence) => {
        if (pin.name !== 'livingproof-capability-report') return;
        evidence.tagCommit = OTHER;
        evidence.imageDigest = realResult().find((entry) => entry.name === 'livingproof-capability-report').digest;
        evidence.publishedDigest = evidence.imageDigest;
      }),
    new RegExp(`livingproof-capability-report: releases\\.reporter\\.commit differs from the resolved tag\\. expected ${OTHER}`)
  );

  const evaluatorRun = dropPassedFields(realLock());
  evaluatorRun.releases.verifier.tag = VERIFIER_WORKFLOW_TAG;
  evaluatorRun.evaluators['livingproof-evaluator-run'].commit = OTHER;
  assert.throws(
    () => passReal(evaluatorRun),
    new RegExp(
      `livingproof-evaluator-run: evaluators\\.livingproof-evaluator-run\\.commit differs from the resolved tag\\. expected ${recordedByName().get('livingproof-evaluator-run').commit}, found ${OTHER}`
    )
  );
});

test('resolved trial inputs reject a commit or image ref capability-verifier would not accept', () => {
  const records = passReal(dropPassedFields({ ...realLock(), releases: { ...realLock().releases, verifier: { ...realLock().releases.verifier, tag: VERIFIER_WORKFLOW_TAG } } }));
  const brokenCommit = records.map((record) =>
    record.name === 'capability-verifier' ? { ...record, commit: 'abc' } : record
  );
  assert.throws(() => resolvedTrialInputs(brokenCommit), /verifier-commit is not a resolved commit/);
  const brokenEvaluator = records.map((record) =>
    record.name === 'livingproof-evaluator-scenario' ? { ...record, commit: 'abc' } : record
  );
  assert.throws(() => resolvedTrialInputs(brokenEvaluator), /evaluator-commit is not a resolved commit/);
  const brokenImage = records.map((record) =>
    record.name === 'capability-verifier' ? { ...record, digest: 'sha256:zzzz' } : record
  );
  assert.throws(() => resolvedTrialInputs(brokenImage), /verifier-image is not a resolved image ref/);
  const brokenReporter = records.map((record) =>
    record.name === 'livingproof-capability-report' ? { ...record, digest: 'not-a-digest' } : record
  );
  assert.throws(() => resolvedTrialInputs(brokenReporter), /reporter-image is not a resolved image ref/);
});

test('a one-line verifier tag edit is what the plan passes, and the lock file is not rewritten', async () => {
  const root = mkdtempSync(path.join(tmpdir(), 'vra-tag-only-'));
  try {
    const source = new URL('./fixtures/real-locks/', import.meta.url);
    mkdirSync(path.join(root, 'verifications'));
    const tomlName = 'unitree-g1-stock-mujoco-corner-left.toml';
    cpSync(new URL(tomlName, source), path.join(root, 'verifications', tomlName));
    const lock = dropPassedFields(realLock());
    lock.releases.verifier.tag = VERIFIER_WORKFLOW_TAG;
    const toml = readFileSync(path.join(root, 'verifications', tomlName), 'utf8');
    lock.definition.sha256 = (await import('node:crypto')).createHash('sha256').update(toml).digest('hex');
    lock.definition.path = tomlName;
    const lockPath = path.join(root, 'verifications', tomlName.replace(/\.toml$/, '.lock.json'));
    const before = `${JSON.stringify(lock, null, 2)}\n`;
    writeFileSync(lockPath, before);
    const planned = await writePlan(
      {
        GITHUB_REPOSITORY: 'Example/robot',
        GITHUB_SHA: '0123456789abcdef0123456789abcdef01234567',
        INPUT_DEFINITIONS: 'verifications/*.toml',
        INPUT_PLAN_PATH: path.join(root, 'plan.json'),
      },
      root,
      {
        async resolve(pin) {
          const recorded = recordedByName().get(pin.name);
          const commit = pin.name === 'capability-verifier' ? RELEASE.tagCommit : recorded.commit;
          const digest = pin.name === 'capability-verifier' ? RELEASE.digest : recorded.digest;
          return evidenceFor(pin, { commit, digest });
        },
      }
    );
    assert.equal(readFileSync(lockPath, 'utf8'), before);
    const scenario = JSON.parse(planned.outputs.scenario);
    assert.equal(scenario.length, 1);
    assert.equal(scenario[0].verifier_commit, RELEASE.tagCommit);
    assert.equal(scenario[0].verifier_image, `${RELEASE.image}:2.12.0@${RELEASE.digest}`);
    assert.equal(scenario[0].evaluator_commit, EVALUATOR_RELEASE.tagCommit);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
