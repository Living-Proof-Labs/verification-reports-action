// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync, cpSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { VERIFIER_WORKFLOW_SHA, VERIFIER_WORKFLOW_TAG } from '../src/definitions.mjs';
import { imageDigestFromBody } from '../src/github-provenance.mjs';
import { writePlan } from '../src/plan.mjs';
import { resolvePin } from '../src/github-provenance.mjs';
import { checkPin, collectPins, resolvedTrialInputs } from '../src/provenance.mjs';

const OTHER = '1111111111111111111111111111111111111111';
const RELEASE = JSON.parse(
  readFileSync(new URL('./fixtures/releases/capability-verifier-v3.1.3.json', import.meta.url), 'utf8')
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

test('the pinned verifier release fixture is the commit and hallway digest the workflow pins', () => {
  assert.equal(RELEASE.tagCommit, VERIFIER_WORKFLOW_SHA);
  assert.equal(RELEASE.tag, VERIFIER_WORKFLOW_TAG);
  assert.equal(imageDigestFromBody(RELEASE.body, RELEASE.image), RELEASE.digest);
});

test('each passed field on the real unitree lock is refused even when the value matches the release', () => {
  const published = realLock();
  const cases = [
    ['releases.verifier.commit', () => published.releases.verifier.commit, /releases\.verifier\.commit is refused/],
    ['releases.verifier.image', () => published.releases.verifier.image, /releases\.verifier\.image is refused/],
    ['releases.verifier.digest', () => published.releases.verifier.digest, /releases\.verifier\.digest is refused/],
    ['releases.reporter.image', () => published.releases.reporter.image, /releases\.reporter\.image is refused/],
    ['releases.reporter.digest', () => published.releases.reporter.digest, /releases\.reporter\.digest is refused/],
  ];
  for (const [label, value, pattern] of cases) {
    const lock = dropPassedFields(published);
    lock.releases.verifier.tag = VERIFIER_WORKFLOW_TAG;
    const [, ...rest] = label.split('.');
    const field = rest.at(-1);
    lock.releases[rest[0]][field] = value();
    assert.throws(() => collectPins(item(lock)), pattern, label);
  }
});

test('a one-line verifier tag edit on the real lock passes the pinned commit and image', () => {
  const lock = dropPassedFields(realLock());
  assert.equal(lock.releases.verifier.tag, 'v2.11.4');
  lock.releases.verifier.tag = VERIFIER_WORKFLOW_TAG;
  const records = passReal(lock);
  const inputs = resolvedTrialInputs(records);
  assert.equal(inputs.verifier_commit, RELEASE.tagCommit);
  assert.equal(inputs.verifier_image, `${RELEASE.image}:${RELEASE.tag.slice(1)}@${RELEASE.digest}`);
  const reporter = recordedByName().get('livingproof-capability-report');
  assert.equal(
    inputs.reporter_image,
    `ghcr.io/living-proof-labs/livingproof-capability-report:3.15.4@${reporter.digest}`
  );
  assert.deepEqual(Object.keys(inputs).sort(), ['reporter_image', 'verifier_commit', 'verifier_image']);
  assert.equal(lock.releases.runner.commit, realLock().releases.runner.commit);
});

test('the Scenario lock entry is not a pin: its tag and commit change nothing the plan passes', () => {
  const lock = dropPassedFields(realLock());
  lock.releases.verifier.tag = VERIFIER_WORKFLOW_TAG;
  const baseline = resolvedTrialInputs(passReal(lock));
  lock.evaluators['livingproof-evaluator-scenario'].tag = 'v0.0.1';
  lock.evaluators['livingproof-evaluator-scenario'].commit = OTHER;
  const records = passReal(lock);
  assert.equal(records.some((record) => record.name === 'livingproof-evaluator-scenario'), false);
  assert.deepEqual(resolvedTrialInputs(records), baseline);
  delete lock.evaluators['livingproof-evaluator-scenario'];
  assert.deepEqual(resolvedTrialInputs(passReal(lock)), baseline);
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

test('a reporter commit that disagrees with the real tag is refused, and an extra evaluators entry is not', () => {
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

  const extra = dropPassedFields(realLock());
  extra.releases.verifier.tag = VERIFIER_WORKFLOW_TAG;
  for (const key of Object.keys(extra.evaluators)) extra.evaluators[key].commit = OTHER;
  const records = passReal(extra);
  for (const key of Object.keys(extra.evaluators)) {
    assert.equal(records.some((record) => record.name === key), false, key);
  }
});

test('resolved trial inputs reject a commit or image ref capability-verifier would not accept', () => {
  const records = passReal(dropPassedFields({ ...realLock(), releases: { ...realLock().releases, verifier: { ...realLock().releases.verifier, tag: VERIFIER_WORKFLOW_TAG } } }));
  const brokenCommit = records.map((record) =>
    record.name === 'capability-verifier' ? { ...record, commit: 'abc' } : record
  );
  assert.throws(() => resolvedTrialInputs(brokenCommit), /verifier-commit is not a resolved commit/);
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
        INPUT_EVALUATORS: [
          'Living-Proof-Labs/livingproof-evaluator-scenario@v1.6.0/livingproof-evaluator.json',
          'Living-Proof-Labs/livingproof-evaluator-run@v1.9.0/livingproof-evaluator.json',
        ].join('\n'),
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
    assert.equal(scenario[0].verifier_image, `${RELEASE.image}:${RELEASE.tag.slice(1)}@${RELEASE.digest}`);
    assert.equal(planned.outputs.evaluators_run, 'true');
    assert.match(planned.outputs.evaluators, /@v1\.9\.0\//);
    assert.match(planned.outputs.evaluators, /livingproof-evaluator-scenario@v1\.6\.0\/livingproof-evaluator\.json/);
    assert.equal(planned.plan.evaluators.lines.length, 2);
    assert.equal('evaluator_commit' in scenario[0], false);
    assert.equal(planned.plan.definitions[0].provenance.some((record) => /scenario/.test(record.name)), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

const CORNER_PARAMETERS = 'specs/scenarios/walk-corner-left/parameters.json';
const HALLWAY_PARAMETERS = 'specs/scenarios/walk-hallway/parameters.json';
const CORNER_HASH = 'ab'.repeat(32);
const HALLWAY_HASH = 'cd'.repeat(32);

function specsReadyLock() {
  const lock = dropPassedFields(realLock());
  lock.releases.verifier.tag = VERIFIER_WORKFLOW_TAG;
  delete lock.releases.specs.files[CORNER_PARAMETERS];
  return lock;
}

function specsEvidence(specs, hashes) {
  const files = {};
  for (const path of specs.paths) {
    const hash = hashes[path];
    files[path] = { published: hash, found: hash };
  }
  return {
    releaseFound: true,
    tagCommit: specs.lockRaw.commit,
    publishedCommit: null,
    files,
    imageDigest: null,
    publishedDigest: null,
    sourceDigest: null,
    attestation: { published: false, status: 404 },
  };
}

test('a robot path of parameters.json is accepted when the scenario file hash matches', () => {
  const lock = specsReadyLock();
  lock.sources.parameters = { path: 'parameters.json', sha256: CORNER_HASH };
  const specs = collectPins(item(lock)).find((pin) => pin.name === 'robot-capability-specs');
  assert.equal(specs.paths.includes(CORNER_PARAMETERS), true);
  assert.equal(specs.paths.includes('parameters.json'), false);
  const description = lock.releases.specs.files['specs/scenarios/walk-corner-left/description.json'];
  const record = checkPin(
    specs,
    specsEvidence(specs, {
      [CORNER_PARAMETERS]: CORNER_HASH,
      'specs/scenarios/walk-corner-left/description.json': description,
    })
  );
  assert.equal(record.status, 'resolved');
});

test("path parameters.json with another scenario's hash is refused", () => {
  const lock = specsReadyLock();
  lock.sources.parameters = { path: 'parameters.json', sha256: HALLWAY_HASH };
  const specs = collectPins(item(lock)).find((pin) => pin.name === 'robot-capability-specs');
  assert.equal(specs.parametersFile, CORNER_PARAMETERS);
  const description = lock.releases.specs.files['specs/scenarios/walk-corner-left/description.json'];
  assert.throws(
    () =>
      checkPin(
        specs,
        specsEvidence(specs, {
          [CORNER_PARAMETERS]: CORNER_HASH,
          'specs/scenarios/walk-corner-left/description.json': description,
        })
      ),
    new RegExp(`sources\\.parameters\\.sha256 differs from the resolved tag\\. expected ${CORNER_HASH}, found ${HALLWAY_HASH}`)
  );
});

test("another scenario's parameters file is refused", () => {
  const lock = specsReadyLock();
  lock.sources.parameters = { path: HALLWAY_PARAMETERS, sha256: HALLWAY_HASH };
  const specs = collectPins(item(lock)).find((pin) => pin.name === 'robot-capability-specs');
  assert.equal(specs.paths.includes(CORNER_PARAMETERS), true);
  assert.equal(specs.paths.includes(HALLWAY_PARAMETERS), false);
  const description = lock.releases.specs.files['specs/scenarios/walk-corner-left/description.json'];
  assert.throws(
    () =>
      checkPin(
        specs,
        specsEvidence(specs, {
          [CORNER_PARAMETERS]: CORNER_HASH,
          'specs/scenarios/walk-corner-left/description.json': description,
        })
      ),
    new RegExp(`sources\\.parameters\\.sha256 differs from the resolved tag\\. expected ${CORNER_HASH}, found ${HALLWAY_HASH}`)
  );
});

test('a non-string sources.parameters.sha256 is refused', () => {
  const lock = specsReadyLock();
  lock.sources.parameters = { path: 'parameters.json', sha256: 12345 };
  assert.throws(
    () => collectPins(item(lock)),
    /robot-capability-specs: sources\.parameters\.sha256 is not a string/
  );
});

test('null is refused for each dropped lock field', () => {
  const fields = [
    ['releases.verifier.commit', (lock) => { lock.releases.verifier.commit = null; }, /releases\.verifier\.commit is refused/],
    ['releases.verifier.image', (lock) => { lock.releases.verifier.image = null; }, /releases\.verifier\.image is refused/],
    ['releases.verifier.digest', (lock) => { lock.releases.verifier.digest = null; }, /releases\.verifier\.digest is refused/],
    ['releases.reporter.image', (lock) => { lock.releases.reporter.image = null; }, /releases\.reporter\.image is refused/],
    ['releases.reporter.digest', (lock) => { lock.releases.reporter.digest = null; }, /releases\.reporter\.digest is refused/],
  ];
  for (const [label, set, pattern] of fields) {
    const lock = dropPassedFields(realLock());
    lock.releases.verifier.tag = VERIFIER_WORKFLOW_TAG;
    set(lock);
    assert.throws(() => collectPins(item(lock)), pattern, label);
  }
});

test('a null image digest is refused before a record is returned', () => {
  const lock = dropPassedFields(realLock());
  lock.releases.verifier.tag = VERIFIER_WORKFLOW_TAG;
  const verifier = collectPins(item(lock)).find((pin) => pin.name === 'capability-verifier');
  assert.throws(
    () =>
      checkPin(verifier, {
        releaseFound: true,
        tagCommit: VERIFIER_WORKFLOW_SHA,
        publishedCommit: null,
        files: {},
        imageDigest: null,
        publishedDigest: null,
        sourceDigest: null,
        attestation: { published: false, status: 404 },
      }),
    /capability-verifier: digest is missing/
  );
});

function trialRecords() {
  return [
    { name: 'capability-verifier', tag: 'v2.12.5', commit: 'a'.repeat(40), digest: `sha256:${'ab'.repeat(32)}` },
    { name: 'livingproof-capability-report', tag: 'v3.15.4', commit: 'b'.repeat(40), digest: `sha256:${'cd'.repeat(32)}` },
  ];
}

test('resolved trial inputs refuse a verifier image with trailing bytes or uppercase hex', () => {
  const trailing = trialRecords().map((record) =>
    record.name === 'capability-verifier' ? { ...record, digest: `sha256:${'ab'.repeat(32)}trailing` } : record
  );
  assert.throws(() => resolvedTrialInputs(trailing), /verifier-image is not a resolved image ref/);
  const uppercase = trialRecords().map((record) =>
    record.name === 'capability-verifier' ? { ...record, digest: `sha256:${'AB'.repeat(32)}` } : record
  );
  assert.throws(() => resolvedTrialInputs(uppercase), /verifier-image is not a resolved image ref/);
});

test('resolved trial inputs refuse a reporter image with trailing bytes or uppercase hex', () => {
  const trailing = trialRecords().map((record) =>
    record.name === 'livingproof-capability-report'
      ? { ...record, digest: `sha256:${'cd'.repeat(32)}trailing` }
      : record
  );
  assert.throws(() => resolvedTrialInputs(trailing), /reporter-image is not a resolved image ref/);
  const uppercase = trialRecords().map((record) =>
    record.name === 'livingproof-capability-report'
      ? { ...record, digest: `sha256:${'CD'.repeat(32)}` }
      : record
  );
  assert.throws(() => resolvedTrialInputs(uppercase), /reporter-image is not a resolved image ref/);
});

test('a malformed sha256 string is refused at every FIELD_FORM.sha256 check', () => {
  const short = 'ab'.repeat(31);
  const upper = 'AB'.repeat(32);
  const nonHex = 'g'.repeat(64);
  const long = 'a'.repeat(65);
  const cases = [
    ['sources.parameters.sha256 short', () => {
      const lock = specsReadyLock();
      lock.sources.parameters = { path: 'parameters.json', sha256: short };
      collectPins(item(lock));
    }, /sources\.parameters\.sha256 is not the expected form/],
    ['sources.parameters.sha256 uppercase', () => {
      const lock = specsReadyLock();
      lock.sources.parameters = { path: 'parameters.json', sha256: upper };
      collectPins(item(lock));
    }, /sources\.parameters\.sha256 is not the expected form/],
    ['sources.parameters.sha256 non-hex', () => {
      const lock = specsReadyLock();
      lock.sources.parameters = { path: 'parameters.json', sha256: nonHex };
      collectPins(item(lock));
    }, /sources\.parameters\.sha256 is not the expected form/],
    ['sources.parameters.sha256 65 characters', () => {
      const lock = specsReadyLock();
      lock.sources.parameters = { path: 'parameters.json', sha256: long };
      collectPins(item(lock));
    }, /sources\.parameters\.sha256 is not the expected form/],
    ['world.sha256 uppercase', () => {
      const lock = specsReadyLock();
      lock.world.sha256 = upper;
      collectPins(item(lock));
    }, /world\.sha256 is not the expected form/],
    ['sources.world.sha256 short', () => {
      const lock = specsReadyLock();
      lock.sources.world.sha256 = short;
      collectPins(item(lock));
    }, /sources\.world\.sha256 is not the expected form/],
    ['releases.specs.files non-hex', () => {
      const lock = specsReadyLock();
      lock.releases.specs.files['specs/scenarios/walk-corner-left/description.json'] = nonHex;
      collectPins(item(lock));
    }, /releases\.specs\.files\[specs\/scenarios\/walk-corner-left\/description\.json\] is not the expected form/],
    ['releases.specs.files 65 characters', () => {
      const lock = specsReadyLock();
      const file = 'specs/scenarios/walk-corner-left/description.json';
      lock.releases.specs.files[file] = long;
      collectPins(item(lock));
    }, /releases\.specs\.files\[specs\/scenarios\/walk-corner-left\/description\.json\] is not the expected form/],
  ];
  for (const [label, run, pattern] of cases) {
    assert.throws(run, pattern, label);
  }
});

test('resolved trial inputs refuse each missing record on its own', () => {
  for (const name of ['capability-verifier', 'livingproof-capability-report']) {
    const records = trialRecords().filter((record) => record.name !== name);
    assert.throws(() => resolvedTrialInputs(records), new RegExp(`resolved pins are missing ${name}`));
  }
});

test('resolved trial inputs do not need, name, or read a Scenario record', () => {
  const inputs = resolvedTrialInputs(trialRecords());
  assert.deepEqual(Object.keys(inputs).sort(), ['reporter_image', 'verifier_commit', 'verifier_image']);
  const withBroken = [
    ...trialRecords(),
    { name: 'livingproof-evaluator-scenario', tag: 'v1.5.3', commit: 'abc', digest: 'nope' },
  ];
  assert.deepEqual(resolvedTrialInputs(withBroken), inputs);
});

function respond(status, body) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: () => null },
    json: async () => body,
    text: async () => '',
    arrayBuffer: async () => new Uint8Array(),
    body: null,
  };
}

async function peel(refStatus, refBody) {
  const pin = {
    name: 'robot-capability-specs',
    repository: 'Living-Proof-Labs/robot-capability-specs',
    tag: 'v3.1.0',
    kind: 'files',
    paths: ['specs/scenarios/walk-corner-left/description.json'],
    image: '',
  };
  return resolvePin(pin, {
    token: '',
    api: 'https://api.github.com',
    fetch: async (url) => {
      if (url.includes('/releases/tags/')) {
        return respond(200, { tag_name: 'v3.1.0', target_commitish: 'main', body: '', assets: [] });
      }
      if (url.includes('/git/ref/tags/')) return respond(refStatus, refBody);
      if (url.includes('/git/tags/')) return respond(200, { object: { type: 'tag', sha: 'e'.repeat(40) } });
      if (url.includes('/contents/')) {
        return respond(200, { encoding: 'base64', content: Buffer.from('x').toString('base64') });
      }
      if (url.includes('/attestations/')) return respond(404, {});
      return respond(500, { message: url });
    },
  });
}

test('a deleted tag and a non-commit tag object do not become the resolved commit', async () => {
  const deleted = await peel(404, { object: { type: 'commit', sha: 'c'.repeat(40) } });
  assert.equal(deleted.tagCommit, null);
  for (const type of ['tree', 'blob', 'tag']) {
    const odd = await peel(200, { object: { type, sha: 'd'.repeat(40) } });
    assert.equal(odd.tagCommit, null, type);
  }
});

function refuseParametersPath(pathValue) {
  const lock = specsReadyLock();
  lock.sources.parameters = { path: pathValue, sha256: CORNER_HASH };
  assert.throws(
    () => collectPins(item(lock)),
    /robot-capability-specs: sources\.parameters\.path is not a safe API path/
  );
}

for (const [pathValue, why] of [
  ['..', 'a .. segment'],
  ['../secrets', 'a .. segment'],
  ['%2e%2e/%2fetc', 'an encoded traversal'],
  ['%2e%2e', 'encoded dot-dot'],
  ['%2f', 'an encoded slash'],
  ['/etc/passwd', 'an absolute path'],
  ['specs//x.json', 'an empty segment'],
  ['specs\\..\\x.json', 'a backslash'],
  ['%252e%252e/user', 'percent-encoding decoded repeatedly'],
]) {
  test(`sources.parameters.path with ${why} is refused`, () => {
    refuseParametersPath(pathValue);
  });
}

function refuseScenarioParameters(scenario) {
  const lock = specsReadyLock();
  lock.scenario = scenario;
  lock.sources.parameters = { path: 'parameters.json', sha256: CORNER_HASH };
  assert.throws(
    () => collectPins(item(lock)),
    /robot-capability-specs: scenario parameters file is not a safe API path/
  );
}

for (const [scenario, why] of [
  ['../user@0.1.0', 'a .. segment'],
  ['%2e%2e/%2f@0.1.0', 'an encoded traversal'],
  ['/abs@0.1.0', 'an absolute path'],
]) {
  test(`scenario parameters file with ${why} is refused`, () => {
    refuseScenarioParameters(scenario);
  });
}

test('a valid scenario parameters file is accepted', () => {
  const lock = specsReadyLock();
  lock.sources.parameters = { path: 'parameters.json', sha256: CORNER_HASH };
  const specs = collectPins(item(lock)).find((pin) => pin.name === 'robot-capability-specs');
  assert.equal(specs.parametersFile, CORNER_PARAMETERS);
});

function refuseSpecsFile(pathValue) {
  const lock = specsReadyLock();
  lock.releases.specs.files = { [pathValue]: CORNER_HASH };
  assert.throws(
    () => collectPins(item(lock)),
    /robot-capability-specs: releases\.specs\.files key is not a safe API path/
  );
}

for (const [pathValue, why] of [
  ['..', 'a .. segment'],
  ['%2e%2e/%2fetc', 'an encoded traversal'],
  ['/etc/passwd', 'an absolute path'],
]) {
  test(`releases.specs.files key with ${why} is refused`, () => {
    refuseSpecsFile(pathValue);
  });
}

test('a valid releases.specs.files key is accepted', () => {
  const lock = specsReadyLock();
  const specs = collectPins(item(lock)).find((pin) => pin.name === 'robot-capability-specs');
  assert.equal(specs.paths.includes('specs/scenarios/walk-corner-left/description.json'), true);
});
