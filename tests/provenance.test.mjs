// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { VERIFIER_WORKFLOW_SHA } from '../src/definitions.mjs';
import {
  MAX_BUNDLE_BYTES,
  imageDigestFromBody,
  openAttestations,
  parseSha256Sums,
  publishedCommitFromRelease,
  readAttestation,
  resolvePin,
} from '../src/github-provenance.mjs';
import { decodeSnappyRaw } from '../src/snappy.mjs';
import { collectReports } from '../src/collect.mjs';
import { writePlan } from '../src/plan.mjs';
import {
  REQUIRED_INPUTS,
  assertPinnedInputs,
  checkPin,
  collectPins,
  fixtureProvenance,
  runIfProvenancePasses,
} from '../src/provenance.mjs';
import { pinnedInputLine, renderReport, writeConfiguration } from '../src/report.mjs';
import { runAction } from '../src/run.mjs';

const COMMIT = VERIFIER_WORKFLOW_SHA;
const OTHER = '1111111111111111111111111111111111111111';
const NEW = '2222222222222222222222222222222222222222';
const FILE_HASH = 'ab'.repeat(32);
const OTHER_HASH = 'cd'.repeat(32);
const IMAGE = `sha256:${'ef'.repeat(32)}`;
const SOURCE = `sha256:${'12'.repeat(32)}`;
const STALE_COMMIT = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
const STALE_DIGEST = `sha256:${'00'.repeat(32)}`;

function sampleLock() {
  return {
    releases: {
      verifier: {
        repository: 'Living-Proof-Labs/capability-verifier',
        tag: 'v2.11.4',
        commit: COMMIT,
        image: 'ghcr.io/living-proof-labs/capability-verifier-hallway',
        digest: IMAGE,
      },
      runner: {
        repository: 'Living-Proof-Labs/livingproof-sim-mujoco',
        tag: 'v3.7.4',
        commit: COMMIT,
      },
      specs: {
        repository: 'Living-Proof-Labs/robot-capability-specs',
        tag: 'v3.1.0',
        commit: COMMIT,
        files: { 'specs/scenarios/walk-corner-left/description.json': FILE_HASH },
      },
      reporter: {
        repository: 'Living-Proof-Labs/livingproof-capability-report',
        tag: 'v3.15.4',
        commit: COMMIT,
        image: 'ghcr.io/living-proof-labs/livingproof-capability-report',
        digest: IMAGE,
      },
    },
    world: {
      id: 'hallway-corner',
      version: '0.2.0',
      repository: 'Living-Proof-Labs/livingproof-worlds',
      tag: 'v1.4.0',
      commit: COMMIT,
      sha256: FILE_HASH,
    },
    evaluators: {
      'livingproof-evaluator-scenario': {
        repository: 'Living-Proof-Labs/livingproof-evaluator-scenario',
        tag: 'v1.5.3',
        commit: COMMIT,
      },
      'livingproof-evaluator-run': {
        repository: 'Living-Proof-Labs/livingproof-evaluator-run',
        tag: 'v1.8.6',
        commit: COMMIT,
      },
    },
  };
}

function passingEvidence(pin) {
  if (pin.kind === 'image') {
    return {
      releaseFound: true,
      tagCommit: COMMIT,
      publishedCommit: COMMIT,
      files: {},
      imageDigest: IMAGE,
      publishedDigest: IMAGE,
      sourceDigest: null,
      attestation: { published: false, status: 404 },
    };
  }
  if (pin.paths.length > 0) {
    const files = {};
    for (const file of pin.paths) files[file] = { published: FILE_HASH, found: FILE_HASH };
    return {
      releaseFound: true,
      tagCommit: COMMIT,
      publishedCommit: COMMIT,
      files,
      imageDigest: null,
      publishedDigest: null,
      sourceDigest: null,
      attestation: { published: false, status: 404 },
    };
  }
  return {
    releaseFound: true,
    tagCommit: COMMIT,
    publishedCommit: COMMIT,
    files: {},
    imageDigest: null,
    publishedDigest: SOURCE,
    sourceDigest: SOURCE,
    attestation: { published: false, status: 404 },
  };
}

function client(mutate = () => {}) {
  return {
    async resolve(pin) {
      const evidence = passingEvidence(pin);
      mutate(pin, evidence);
      return evidence;
    },
  };
}

function item(lock = sampleLock(), sources = {}) {
  return { path: 'verifications/corner.toml', id: 'corner', lock, sources };
}

test('a passing resolution records repository, tag, commit, digest, and check result', async () => {
  const records = await assertPinnedInputs(item(), client());
  assert.deepEqual(
    records.map((record) => record.name),
    REQUIRED_INPUTS
  );
  for (const record of records) {
    assert.equal(record.status, 'not-published');
    assert.match(record.repository, /^Living-Proof-Labs\//);
    assert.match(record.tag, /^v\d+\.\d+\.\d+$/);
    assert.equal(record.commit, COMMIT);
    assert.match(record.digest, /^sha256:[0-9a-f]{64}$/);
    assert.equal(record.checks.release, 'passed');
    assert.equal(record.checks.commit, 'passed');
    assert.equal(record.checks.digest, 'passed');
    assert.equal(record.attestation, 'not-published');
    assert.notEqual(record.commit, STALE_COMMIT);
    assert.notEqual(record.digest, STALE_DIGEST);
  }
  const specs = records.find((record) => record.name === 'robot-capability-specs');
  assert.equal(specs.digest, `sha256:${FILE_HASH}`);
});

test('a tag moved to another commit refuses before a report is written', async () => {
  const output = path.join(mkdtempSync(path.join(tmpdir(), 'vra-moved-')), 'report.json');
  let trials = 0;
  await assert.rejects(
    () =>
      runIfProvenancePasses(
        item(),
        client((pin, evidence) => {
          if (pin.name !== 'livingproof-sim-mujoco') return;
          evidence.publishedCommit = COMMIT;
          evidence.tagCommit = OTHER;
        }),
        {},
        () => {
          trials += 1;
          writeFileSync(output, '{}\n');
        }
      ),
    (error) => {
      assert.match(error.message, /livingproof-sim-mujoco/);
      assert.match(error.message, /tag v3\.7\.4 moved/);
      assert.match(error.message, new RegExp(`expected commit ${COMMIT}`));
      assert.match(error.message, new RegExp(`found commit ${OTHER}`));
      return true;
    }
  );
  assert.equal(trials, 0);
  assert.equal(existsSync(output), false);
});

test('a wrong sha256 refuses and names the expected and found values', async () => {
  await assert.rejects(
    () =>
      assertPinnedInputs(
        item(),
        client((pin, evidence) => {
          if (pin.name !== 'livingproof-worlds') return;
          const file = 'worlds/hallway-corner/0.2.0/world.json';
          evidence.files[file] = { published: FILE_HASH, found: OTHER_HASH };
        })
      ),
    (error) => {
      assert.match(error.message, /livingproof-worlds/);
      assert.match(error.message, /worlds\/hallway-corner\/0\.2\.0\/world\.json/);
      assert.match(error.message, new RegExp(`expected ${FILE_HASH}`));
      assert.match(error.message, new RegExp(`found ${OTHER_HASH}`));
      return true;
    }
  );
});

test('an image digest that differs from the release notes refuses', async () => {
  const found = `sha256:${'44'.repeat(32)}`;
  await assert.rejects(
    () =>
      assertPinnedInputs(
        item(),
        client((pin, evidence) => {
          if (pin.name !== 'livingproof-capability-report') return;
          evidence.imageDigest = found;
        })
      ),
    (error) => {
      assert.match(error.message, /livingproof-capability-report/);
      assert.match(error.message, /image digest mismatch/);
      assert.match(error.message, new RegExp(`expected ${IMAGE}`));
      assert.match(error.message, new RegExp(`found ${found}`));
      return true;
    }
  );
});

test('a missing release or a missing tag fails and is not recorded as not-published', () => {
  const pin = collectPins(item()).find((entry) => entry.name === 'livingproof-sim-mujoco');
  const attestation = { published: false, status: 404 };
  assert.throws(
    () => checkPin(pin, { releaseFound: false, tagCommit: null, attestation }),
    /livingproof-sim-mujoco: release Living-Proof-Labs\/livingproof-sim-mujoco@v3\.7\.4 is missing/
  );
  assert.throws(
    () =>
      checkPin(pin, {
        releaseFound: true,
        tagCommit: null,
        publishedCommit: null,
        files: {},
        sourceDigest: SOURCE,
        attestation,
      }),
    /livingproof-sim-mujoco: tag v3\.7\.4 did not resolve/
  );
});

test('a missing release refuses and names the input and the tag', async () => {
  await assert.rejects(
    () =>
      assertPinnedInputs(
        item(),
        client((pin, evidence) => {
          if (pin.name === 'robot-capability-specs') evidence.releaseFound = false;
        })
      ),
    /robot-capability-specs: release Living-Proof-Labs\/robot-capability-specs@v3\.1\.0 is missing\. expected a GitHub Release for v3\.1\.0, found no release/
  );
});

function reporterAttestation(pin, overrides) {
  return {
    published: true,
    status: 200,
    subjectDigest: IMAGE,
    repository: pin.repository,
    gitCommit: COMMIT,
    ...overrides,
  };
}

async function refusesBeforeReport(mutate, pattern) {
  const output = path.join(mkdtempSync(path.join(tmpdir(), 'vra-attest-')), 'report.json');
  let trials = 0;
  await assert.rejects(
    () =>
      runIfProvenancePasses(item(), client(mutate), {}, () => {
        trials += 1;
        writeFileSync(output, '{}\n');
      }),
    pattern
  );
  assert.equal(trials, 0);
  assert.equal(existsSync(output), false);
}

test('attestation digest mismatch fails the run', async () => {
  const found = `sha256:${'99'.repeat(32)}`;
  await refusesBeforeReport(
    (pin, evidence) => {
      if (pin.name !== 'livingproof-capability-report') return;
      evidence.attestation = reporterAttestation(pin, { subjectDigest: found });
    },
    (error) => {
      assert.match(error.message, /livingproof-capability-report: attestation digest mismatch/);
      assert.match(error.message, new RegExp(`expected subject ${IMAGE}`));
      assert.match(error.message, new RegExp(`found subject ${found}`));
      return true;
    }
  );
});

test('attestation source repository mismatch fails the run', async () => {
  await refusesBeforeReport(
    (pin, evidence) => {
      if (pin.name !== 'livingproof-capability-report') return;
      evidence.attestation = reporterAttestation(pin, { repository: 'Other/report' });
    },
    /livingproof-capability-report: attestation source repository mismatch\. expected Living-Proof-Labs\/livingproof-capability-report, found Other\/report/
  );
});

test('attestation commit mismatch fails the run', async () => {
  await refusesBeforeReport(
    (pin, evidence) => {
      if (pin.name !== 'livingproof-capability-report') return;
      evidence.publishedCommit = COMMIT;
      evidence.attestation = reporterAttestation(pin, { gitCommit: OTHER });
    },
    new RegExp(
      `livingproof-capability-report: attestation commit mismatch\\. expected commit ${COMMIT}, found commit ${OTHER}`
    )
  );
});

test('an attestation HTTP status other than 200 or 404 fails the run', async () => {
  for (const status of [403, 500]) {
    await refusesBeforeReport(
      (pin, evidence) => {
        evidence.attestation = { published: false, status };
      },
      new RegExp(`capability-verifier: attestation request failed\\. expected HTTP 200 or HTTP 404, found HTTP ${status}`)
    );
  }
});

function renderedReport(inputs) {
  const directory = mkdtempSync(path.join(tmpdir(), 'vra-status-'));
  writeConfiguration(directory, {
    files: renderReport({ capability: 'walk', system: { id: 'robot' }, grade: 'PASS' }),
    evidence: null,
    runRecord: { inputs },
  });
  return {
    report: JSON.parse(readFileSync(path.join(directory, 'report.json'), 'utf8')),
    run: JSON.parse(readFileSync(path.join(directory, 'run.json'), 'utf8')),
    markdown: readFileSync(path.join(directory, 'report.md'), 'utf8'),
    html: readFileSync(path.join(directory, 'report.html'), 'utf8'),
    txt: readFileSync(path.join(directory, 'report.txt'), 'utf8'),
  };
}

function assertStatusRendered(record) {
  const line = pinnedInputLine(record);
  const view = renderedReport([record]);
  const pattern = new RegExp(line.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  assert.equal(view.report.inputs[0].status, record.status);
  assert.equal(view.run.inputs[0].status, record.status);
  assert.match(view.markdown, pattern);
  assert.match(view.html, pattern);
  assert.match(view.txt, pattern);
  assert.match(view.html, /<h2>Pinned inputs<\/h2>/);
  assert.doesNotMatch(view.txt, /<!--/);
}

test('verified status is stored on inputs and rendered in the report', async () => {
  const records = await assertPinnedInputs(
    item(),
    client((pin, evidence) => {
      if (pin.name !== 'livingproof-capability-report') return;
      evidence.attestation = reporterAttestation(pin);
    })
  );
  const reporter = records.find((record) => record.name === 'livingproof-capability-report');
  assert.equal(reporter.status, 'verified');
  assert.match(pinnedInputLine(reporter), /livingproof-capability-report: verified \(attestation matched\)/);
  assertStatusRendered(reporter);
});

test('not-published status is stored on inputs and rendered in the report', async () => {
  const records = await assertPinnedInputs(item(), client());
  const reporter = records.find((record) => record.name === 'livingproof-capability-report');
  assert.equal(reporter.status, 'not-published');
  assert.match(
    pinnedInputLine(reporter),
    /livingproof-capability-report: not-published \(no attestation published; commit checked\)/
  );
  assertStatusRendered(reporter);
});

test('resolved status is stored on inputs and rendered in the report', async () => {
  const records = await assertPinnedInputs(
    item(),
    client((pin, evidence) => {
      if (pin.name !== 'livingproof-worlds') return;
      evidence.publishedCommit = null;
    })
  );
  const world = records.find((record) => record.name === 'livingproof-worlds');
  assert.equal(world.status, 'resolved');
  assert.equal(world.commit, COMMIT);
  assert.match(pinnedInputLine(world), /livingproof-worlds: resolved \(no published commit; tag peeled\)/);
  assertStatusRendered(world);
});

test('fixture status is stored on inputs and rendered in the report', () => {
  const record = fixtureProvenance({
    lock: sampleLock(),
  }).find((entry) => entry.name === 'capability-verifier');
  assert.equal(record.status, 'fixture');
  assert.match(
    pinnedInputLine(record),
    /capability-verifier: fixture \(this repository's fixture; release not checked\)/
  );
  assertStatusRendered(record);
});

test('a verifier tag that peels to another commit refuses before the workflow runs', async () => {
  await assert.rejects(
    () =>
      assertPinnedInputs(
        item(),
        client((pin, evidence) => {
          if (pin.name !== 'capability-verifier') return;
          evidence.tagCommit = OTHER;
          evidence.publishedCommit = OTHER;
        })
      ),
    new RegExp(
      `capability-verifier: workflow pin mismatch\\. expected commit ${COMMIT} \\(v2\\.11\\.4\\), found commit ${OTHER} for tag v2\\.11\\.4`
    )
  );
});

test('a tag bump that leaves the lock commit and digest fails before a plan is written', async () => {
  const root = mkdtempSync(path.join(tmpdir(), 'vra-bump-'));
  const reportDir = path.join(root, 'report');
  try {
    const lock = sampleLock();
    lock.releases.reporter.tag = 'v3.16.0';
    const toml = [
      'format = "livingproof-verification-definition/0.1.0"',
      'id = "corner"',
      'procedure = "scenario"',
      'scenario = "walk-corner-left@0.2.0"',
      '',
      '[robot]',
      'configurations = ["stock"]',
      '',
    ].join('\n');
    mkdirSync(path.join(root, 'verifications'));
    writeFileSync(path.join(root, 'verifications/corner.toml'), toml);
    const digest = createHash('sha256').update(toml).digest('hex');
    const onDisk = {
      format: 'livingproof-verification-lock/0.2.0',
      definition: { sha256: digest },
      procedure: 'scenario',
      configurations: [{ name: 'stock' }],
      ...lock,
    };
    const lockPath = path.join(root, 'verifications/corner.lock.json');
    const before = `${JSON.stringify(onDisk, null, 2)}\n`;
    writeFileSync(lockPath, before);
    const planPath = path.join(root, 'plan.json');
    const newDigest = `sha256:${'34'.repeat(32)}`;
    let trials = 0;
    await assert.rejects(
      () =>
        writePlan(
      {
        GITHUB_REPOSITORY: 'Example/robot',
        GITHUB_SHA: '0123456789abcdef0123456789abcdef01234567',
        GITHUB_RUN_ID: '7',
        INPUT_DEFINITIONS: 'verifications/*.toml',
        INPUT_PLAN_PATH: planPath,
      },
      root,
      client((pin, evidence) => {
        if (pin.name !== 'livingproof-capability-report') return;
        assert.equal(pin.tag, 'v3.16.0');
        evidence.tagCommit = NEW;
        evidence.publishedCommit = NEW;
        evidence.imageDigest = newDigest;
        evidence.publishedDigest = newDigest;
      })
      ),
      /livingproof-capability-report: releases\.reporter\.commit differs from the resolved tag/
    );
    assert.equal(existsSync(planPath), false);
    assert.equal(readFileSync(lockPath, 'utf8'), before);
    const agreed = JSON.parse(before);
    agreed.releases.reporter.commit = NEW;
    agreed.releases.reporter.digest = newDigest;
    writeFileSync(lockPath, `${JSON.stringify(agreed, null, 2)}\n`);
    const planned = await writePlan(
      {
        GITHUB_REPOSITORY: 'Example/robot',
        GITHUB_SHA: '0123456789abcdef0123456789abcdef01234567',
        GITHUB_RUN_ID: '7',
        INPUT_DEFINITIONS: 'verifications/*.toml',
        INPUT_PLAN_PATH: planPath,
      },
      root,
      client((pin, evidence) => {
        if (pin.name !== 'livingproof-capability-report') return;
        evidence.tagCommit = NEW;
        evidence.publishedCommit = NEW;
        evidence.imageDigest = newDigest;
        evidence.publishedDigest = newDigest;
      })
    );
    const reporter = planned.plan.definitions[0].provenance.find(
      (record) => record.name === 'livingproof-capability-report'
    );
    assert.equal(reporter.tag, 'v3.16.0');
    assert.equal(reporter.commit, NEW);
    assert.equal(reporter.digest, newDigest);
    assert.equal(reporter.status, 'not-published');
    await assert.rejects(
      () =>
        runIfProvenancePasses(
          item(),
          client((pin, evidence) => {
            if (pin.name === 'capability-verifier') evidence.releaseFound = false;
          }),
          {},
          () => {
            trials += 1;
            mkdirSync(reportDir);
            writeFileSync(path.join(reportDir, 'report.json'), '{}\n');
          }
        ),
      /found no release/
    );
    assert.equal(trials, 0);
    assert.equal(existsSync(reportDir), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('a failed plan writes no plan file and collect writes no report', async () => {
  const root = mkdtempSync(path.join(tmpdir(), 'vra-refuse-'));
  try {
    const toml = [
      'format = "livingproof-verification-definition/0.1.0"',
      'id = "corner"',
      'procedure = "scenario"',
      '',
      '[robot]',
      'configurations = ["stock"]',
      '',
    ].join('\n');
    mkdirSync(path.join(root, 'verifications'));
    writeFileSync(path.join(root, 'verifications/corner.toml'), toml);
    const digest = createHash('sha256').update(toml).digest('hex');
    writeFileSync(
      path.join(root, 'verifications/corner.lock.json'),
      `${JSON.stringify({
        format: 'livingproof-verification-lock/0.2.0',
        definition: { sha256: digest },
        procedure: 'scenario',
        configurations: [{ name: 'stock' }],
        ...sampleLock(),
      })}\n`
    );
    const planPath = path.join(root, 'plan.json');
    const outputDir = path.join(root, 'out');
    await assert.rejects(
      () =>
        writePlan(
          {
            GITHUB_REPOSITORY: 'Example/robot',
            GITHUB_SHA: '0123456789abcdef0123456789abcdef01234567',
            INPUT_DEFINITIONS: 'verifications/*.toml',
            INPUT_PLAN_PATH: planPath,
          },
          root,
          client((pin, evidence) => {
            if (pin.name === 'capability-verifier') evidence.releaseFound = false;
          })
        ),
      /found no release/
    );
    assert.equal(existsSync(planPath), false);
    assert.equal(existsSync(outputDir), false);

    const failedPlan = path.join(root, 'failed-plan.json');
    writeFileSync(
      failedPlan,
      `${JSON.stringify({
        definitions: [
          {
            id: 'corner',
            path: 'verifications/corner.toml',
            execution: 'capability-verifier',
            configurations: ['stock'],
            provenance: REQUIRED_INPUTS.map((name) => ({ name, result: 'fixture' })),
          },
        ],
      })}\n`
    );
    assert.throws(
      () =>
        collectReports(
          {
            INPUT_PLAN_PATH: failedPlan,
            INPUT_OUTPUT_DIR: outputDir,
            GITHUB_REPOSITORY: 'Example/robot',
            GITHUB_SHA: '0123456789abcdef0123456789abcdef01234567',
          },
          root
        ),
      /so no report can be written/
    );
    assert.equal(existsSync(outputDir), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('fixture reports record every input without calling the release client', async () => {
  const repo = fileURLToPath(new URL('..', import.meta.url));
  const calls = [];
  const planned = await writePlan(
    {
      GITHUB_REPOSITORY: 'Living-Proof-Labs/verification-reports-action',
      WORKFLOW_REPOSITORY: 'Living-Proof-Labs/verification-reports-action',
      GITHUB_SHA: '0123456789abcdef0123456789abcdef01234567',
      GITHUB_RUN_ID: '1',
      INPUT_DEFINITIONS: 'tests/fixtures/robot/verifications/*.toml',
      INPUT_PLAN_PATH: path.join(mkdtempSync(path.join(tmpdir(), 'vra-fix-plan-')), 'plan.json'),
    },
    repo,
    {
      async resolve(pin) {
        calls.push(pin.name);
        throw new Error('fixture plan must not resolve releases');
      },
    }
  );
  assert.equal(calls.length, 0);
  for (const definition of planned.plan.definitions) {
    assert.deepEqual(
      definition.provenance.map((record) => record.name),
      REQUIRED_INPUTS
    );
    assert.equal(definition.provenance.every((record) => record.result === 'fixture'), true);
  }

  const outputDir = path.join(mkdtempSync(path.join(tmpdir(), 'vra-fix-out-')), 'out');
  runAction(
    {
      GITHUB_REPOSITORY: 'Living-Proof-Labs/verification-reports-action',
      WORKFLOW_REPOSITORY: 'Living-Proof-Labs/verification-reports-action',
      GITHUB_SHA: '0123456789abcdef0123456789abcdef01234567',
      GITHUB_RUN_ID: '1',
      GITHUB_SERVER_URL: 'https://github.com',
      VERIFICATION_CREATED: '2026-11-23T00:00:00Z',
      INPUT_DEFINITIONS: 'tests/fixtures/robot/verifications/*.toml',
      INPUT_OUTPUT_DIR: outputDir,
      INPUT_VERSION: '1.2.3',
    },
    repo
  );
  for (const id of ['fixture-hallway', 'fixture-scenario']) {
    const directory = path.join(outputDir, id, 'stock');
    const report = JSON.parse(readFileSync(path.join(directory, 'report.json'), 'utf8'));
    const run = JSON.parse(readFileSync(path.join(directory, 'run.json'), 'utf8'));
    assert.deepEqual(report.inputs, run.inputs);
    assert.equal(run.inputs.length, REQUIRED_INPUTS.length);
    const verifier = run.inputs.find((record) => record.name === 'capability-verifier');
    assert.equal(verifier.repository, 'Living-Proof-Labs/capability-verifier');
    assert.equal(verifier.tag, 'v2.11.4');
    assert.equal(verifier.commit, STALE_COMMIT);
    assert.equal(verifier.result, 'fixture');
  }
});

test('release notes yield the tag commit and not the image digest', () => {
  const body = [
    '## [2.11.4]',
    '([9481a541157eed6598fcfd7c65c38823c0473b57](https://github.com/Living-Proof-Labs/capability-verifier/commit/9481a541157eed6598fcfd7c65c38823c0473b57))',
    '([0c00c388c693012392da0c1cd391046c752e9b3c](https://github.com/Living-Proof-Labs/capability-verifier/commit/0c00c388c693012392da0c1cd391046c752e9b3c))',
    '`ghcr.io/living-proof-labs/capability-verifier-hallway:2.11.4@sha256:c76f1d53914b6ba4f364ccb906d9580153e300f6160c6c7ed169e3d7e4456209`',
  ].join('\n');
  assert.equal(publishedCommitFromRelease({ target_commitish: 'main', body }, { published: false }), COMMIT);
  assert.equal(
    imageDigestFromBody(body, 'ghcr.io/living-proof-labs/capability-verifier-hallway'),
    'sha256:c76f1d53914b6ba4f364ccb906d9580153e300f6160c6c7ed169e3d7e4456209'
  );
  assert.equal(
    publishedCommitFromRelease({ target_commitish: OTHER, body: '' }, { published: false }),
    OTHER
  );
  const statement = {
    subject: [{ digest: { sha256: 'ef'.repeat(32) } }],
    predicateType: 'https://slsa.dev/provenance/v1',
    predicate: {
      buildDefinition: {
        externalParameters: { workflow: { repository: 'Living-Proof-Labs/capability-verifier', path: '.github/workflows/release.yml' } },
        resolvedDependencies: [{ digest: { gitCommit: COMMIT } }],
      },
    },
  };
  const payload = {
    attestations: [
      { bundle: { dsseEnvelope: { payload: Buffer.from(JSON.stringify(statement)).toString('base64') } } },
    ],
  };
  const attestation = readAttestation(payload);
  assert.equal(attestation.published, true);
  assert.equal(attestation.gitCommit, COMMIT);
  assert.equal(attestation.subjectDigest, IMAGE);
  assert.equal(attestation.repository, 'Living-Proof-Labs/capability-verifier');
  assert.equal(publishedCommitFromRelease({ target_commitish: 'main', body }, attestation), COMMIT);
  assert.deepEqual(parseSha256Sums(`${FILE_HASH}  worlds/hallway-corner/0.2.0/world.json\n`), {
    'worlds/hallway-corner/0.2.0/world.json': FILE_HASH,
  });
  assert.deepEqual(readAttestation({ attestations: [] }), { published: false });
});

test('the GitHub client resolves a release, peels a tag, and does not send the token to codeload', async () => {
  const seen = [];
  const fileBytes = Buffer.from('description');
  const fileHash = createHash('sha256').update(fileBytes).digest('hex');
  function respond(status, body, headers = {}, raw = null) {
    return {
      status,
      ok: status >= 200 && status < 300,
      headers: { get: (name) => headers[name.toLowerCase()] ?? null },
      json: async () => body,
      text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
      arrayBuffer: async () => raw ?? new Uint8Array(),
      body: null,
    };
  }
  const fetchImpl = async (url, options = {}) => {
    seen.push({ url, authorization: options.headers?.Authorization ?? null });
    if (url.endsWith('/releases/tags/v3.1.0')) {
      return respond(200, {
        tag_name: 'v3.1.0',
        target_commitish: 'main',
        body: `commit ${COMMIT}`,
        assets: [{ name: 'SHA256SUMS', browser_download_url: 'https://example.test/SHA256SUMS' }],
      });
    }
    if (url.endsWith('/git/ref/tags/v3.1.0')) {
      return respond(200, { object: { type: 'tag', sha: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' } });
    }
    if (url.endsWith('/git/tags/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')) {
      return respond(200, { object: { type: 'commit', sha: COMMIT } });
    }
    if (url === 'https://example.test/SHA256SUMS') {
      return respond(200, `${fileHash}  specs/scenarios/walk-corner-left/description.json\n`);
    }
    if (url.includes('/contents/specs/')) {
      return respond(200, { encoding: 'base64', content: fileBytes.toString('base64') });
    }
    if (url.includes('/attestations/')) return respond(404, { message: 'Not Found' });
    if (url.startsWith('https://codeload.github.com/')) return respond(200, '', {}, Buffer.from('archive'));
    return respond(500, { message: url });
  };
  const pin = collectPins(item()).find((entry) => entry.name === 'robot-capability-specs');
  const evidence = await resolvePin(pin, {
    token: 'ghs_testtoken',
    api: 'https://api.github.com',
    fetch: fetchImpl,
  });
  assert.equal(evidence.releaseFound, true);
  assert.equal(evidence.tagCommit, COMMIT);
  assert.equal(evidence.publishedCommit, COMMIT);
  assert.equal(evidence.files[pin.paths[0]].found, fileHash);
  assert.equal(evidence.files[pin.paths[0]].published, fileHash);
  assert.deepEqual(evidence.attestation, { published: false, status: 404 });
  assert.equal(seen.some((call) => call.url.includes('/releases/tags/') && call.authorization === 'Bearer ghs_testtoken'), true);

  const source = collectPins(item()).find((entry) => entry.name === 'livingproof-sim-mujoco');
  seen.length = 0;
  const fetchSource = async (url, options = {}) => {
    seen.push({ url, authorization: options.headers?.Authorization ?? null });
    if (url.includes('/releases/tags/')) {
      return respond(200, { tag_name: 'v3.7.4', target_commitish: COMMIT, body: '', assets: [] });
    }
    if (url.includes('/git/ref/tags/')) return respond(200, { object: { type: 'commit', sha: COMMIT } });
    if (url.startsWith('https://codeload.github.com/')) return respond(200, '', {}, Buffer.from('archive'));
    if (url.includes('/attestations/')) return respond(404, {});
    return respond(500, {});
  };
  const sourceEvidence = await resolvePin(source, {
    token: 'ghs_testtoken',
    api: 'https://api.github.com',
    fetch: fetchSource,
  });
  assert.equal(sourceEvidence.tagCommit, COMMIT);
  assert.equal(sourceEvidence.publishedCommit, COMMIT);
  assert.equal(sourceEvidence.sourceDigest, `sha256:${createHash('sha256').update('archive').digest('hex')}`);
  const codeloadUrl = seen.find((call) => call.url.startsWith('https://codeload.github.com/')).url;
  assert.match(codeloadUrl, new RegExp(`/tar.gz/${COMMIT}$`));
  assert.doesNotMatch(codeloadUrl, /refs\/tags/);
  const codeload = seen.find((call) => call.url.startsWith('https://codeload.github.com/'));
  assert.ok(codeload);
  assert.equal(codeload.authorization, null);

  const missing = await resolvePin(pin, {
    token: '',
    api: 'https://api.github.com',
    fetch: async () => respond(404, { message: 'Not Found' }),
  });
  assert.equal(missing.releaseFound, false);

  await assert.rejects(
    () =>
      resolvePin(source, {
        token: '',
        api: 'https://api.github.com',
        fetch: async (url, options) => {
          if (url.includes('/attestations/')) return respond(403, { message: 'Forbidden' });
          return fetchSource(url, options);
        },
      }),
    /livingproof-sim-mujoco: attestation request failed\. expected HTTP 200 or HTTP 404, found HTTP 403/
  );
  await assert.rejects(
    () =>
      resolvePin(source, {
        token: '',
        api: 'https://api.github.com',
        fetch: async (url, options) => {
          if (url.includes('/attestations/')) return respond(200, { attestations: [] });
          return fetchSource(url, options);
        },
      }),
    /HTTP 200 with no attestation/
  );
});

test('an attacker image name, a wrong commit, and a wrong digest fail before a plan is written', async () => {
  const dead = 'dead'.repeat(10);
  const badDigest = `sha256:${'66'.repeat(32)}`;
  const attacker = sampleLock();
  attacker.releases.verifier.image = 'ghcr.io/attacker/hallway';
  attacker.releases.verifier.commit = dead;
  attacker.releases.verifier.digest = badDigest;
  await assert.rejects(
    () => assertPinnedInputs(item(attacker), client()),
    (error) => {
      assert.match(error.message, /capability-verifier: releases\.verifier\.image/);
      assert.match(error.message, /expected ghcr.io\/living-proof-labs\/capability-verifier-hallway/);
      assert.match(error.message, /found ghcr.io\/attacker\/hallway/);
      return true;
    }
  );

  const wrongCommit = sampleLock();
  wrongCommit.releases.verifier.commit = dead;
  await assert.rejects(
    () => assertPinnedInputs(item(wrongCommit), client()),
    new RegExp(`capability-verifier: releases\\.verifier\\.commit differs from the resolved tag\\. expected ${COMMIT}, found ${dead}`)
  );

  const wrongDigest = sampleLock();
  wrongDigest.releases.reporter.digest = badDigest;
  await assert.rejects(
    () => assertPinnedInputs(item(wrongDigest), client()),
    new RegExp(
      `livingproof-capability-report: releases\\.reporter\\.digest differs from the resolved tag\\. expected ${IMAGE}, found ${badDigest}`
    )
  );
  const root = mkdtempSync(path.join(tmpdir(), 'vra-stale-'));
  assert.equal(existsSync(path.join(root, 'plan.json')), false);
});

test('dot segments in a repository are rejected before a URL is built', () => {
  for (const repository of ['../..', 'Living-Proof-Labs/..', './capability-verifier', 'foo/']) {
    const lock = sampleLock();
    lock.releases.runner.repository = repository;
    assert.throws(() => collectPins(item(lock)), /livingproof-sim-mujoco: repository is not OWNER\/NAME/);
  }
});

test('a source sha256 mismatch fails', async () => {
  const found = `sha256:${'99'.repeat(32)}`;
  await assert.rejects(
    () =>
      assertPinnedInputs(
        item(),
        client((pin, evidence) => {
          if (pin.name !== 'livingproof-sim-mujoco') return;
          evidence.publishedDigest = found;
        })
      ),
    new RegExp(`livingproof-sim-mujoco: sha256 mismatch for Living-Proof-Labs\\/livingproof-sim-mujoco@v3\\.7\\.4\\. expected ${found}, found ${SOURCE}`)
  );
});

test('the reporter v3.15.4 notes digest is read from image@sha256', () => {
  const notes = [
    'Image: `ghcr.io/living-proof-labs/livingproof-capability-report:3.15.4`.',
    'Registry manifest digest: `sha256:b91557cbb024614e441462ced9941e3da1f40cabe96fbfd9a3f5f82d1908db07`.',
    'docker pull ghcr.io/living-proof-labs/livingproof-capability-report@sha256:b91557cbb024614e441462ced9941e3da1f40cabe96fbfd9a3f5f82d1908db07',
  ].join('\n');
  assert.equal(
    imageDigestFromBody(notes, 'ghcr.io/living-proof-labs/livingproof-capability-report'),
    'sha256:b91557cbb024614e441462ced9941e3da1f40cabe96fbfd9a3f5f82d1908db07'
  );
});

test('the image digest is the hash of the manifest bytes', async () => {
  const manifest = Buffer.from('{"schemaVersion":2,"mediaType":"application/vnd.oci.image.index.v1+json"}\n');
  const hashed = `sha256:${createHash('sha256').update(manifest).digest('hex')}`;
  const pin = collectPins(item()).find((entry) => entry.name === 'capability-verifier');
  function respond(status, body, headers = {}, raw = null) {
    return {
      status,
      ok: status >= 200 && status < 300,
      headers: { get: (name) => headers[name.toLowerCase()] ?? null },
      json: async () => body,
      text: async () => '',
      arrayBuffer: async () => raw ?? Buffer.from(''),
      body: null,
    };
  }
  const fetchImpl = async (url) => {
    if (url.includes('/releases/tags/')) {
      return respond(200, { tag_name: 'v2.11.4', target_commitish: COMMIT, body: '', assets: [] });
    }
    if (url.includes('/git/ref/tags/')) return respond(200, { object: { type: 'commit', sha: COMMIT } });
    if (url.includes('ghcr.io/token')) return respond(200, { token: 'registry' });
    if (url.includes('/manifests/')) return respond(200, {}, { 'docker-content-digest': hashed }, manifest);
    if (url.includes('/attestations/')) return respond(404, { message: 'Not Found' });
    return respond(500, { message: url });
  };
  const evidence = await resolvePin(pin, { token: 'ghs_testtoken', api: 'https://api.github.com', fetch: fetchImpl });
  assert.equal(evidence.imageDigest, hashed);
  await assert.rejects(
    () =>
      resolvePin(pin, {
        token: 'ghs_testtoken',
        api: 'https://api.github.com',
        fetch: async (url) => {
          if (url.includes('/manifests/')) return respond(200, {}, { 'docker-content-digest': `sha256:${'ab'.repeat(32)}` }, manifest);
          return fetchImpl(url);
        },
      }),
    /capability-verifier: image digest mismatch/
  );
});

function byteStream(bytes, headers = {}) {
  const payload = Buffer.from(bytes);
  let sent = false;
  return {
    ok: true,
    status: 200,
    headers: { get: (name) => headers[name.toLowerCase()] ?? null },
    body: {
      getReader() {
        return {
          async read() {
            if (sent) return { done: true, value: undefined };
            sent = true;
            return { done: false, value: payload };
          },
          async cancel() {},
          releaseLock() {},
        };
      },
    },
  };
}

function attestationEntry(repository, digestHex, workflow = '.github/workflows/release.yml') {
  const statement = {
    predicateType: 'https://slsa.dev/provenance/v1',
    subject: [{ name: 'artifact', digest: { sha256: digestHex } }],
    predicate: {
      buildDefinition: {
        externalParameters: { workflow: { repository, path: workflow } },
        resolvedDependencies: [{ digest: { gitCommit: COMMIT } }],
      },
    },
  };
  return {
    bundle: { dsseEnvelope: { payload: Buffer.from(JSON.stringify(statement)).toString('base64') } },
  };
}

test('a captured cli/cli attestation verifies from bundle_url after a foreign entry', async () => {
  const digest = 'sha256:4835dbdbdcf8ba8bcb69515da797375d2eb955f824ebe686df8df07876a69fe8';
  const releaseBytes = readFileSync(new URL('./fixtures/attestations/cli-release.sn', import.meta.url));
  const slsaBytes = readFileSync(new URL('./fixtures/attestations/cli-slsa.sn', import.meta.url));
  const decodedBundle = JSON.parse(decodeSnappyRaw(slsaBytes).toString('utf8'));
  assert.equal(decodedBundle.mediaType, 'application/vnd.dev.sigstore.bundle.v0.3+json');
  const fetches = [];
  const selected = await openAttestations(
    [
      { bundle: null, bundle_url: 'https://tmaproduction.blob.core.windows.net/attestations/cli-release.sn' },
      { bundle: null, bundle_url: 'https://tmaproduction.blob.core.windows.net/attestations/cli-slsa.sn' },
    ],
    digest,
    async (url, options) => {
      fetches.push({ url, authorization: options?.headers?.Authorization ?? null });
      const body = url.endsWith('cli-release.sn') ? releaseBytes : slsaBytes;
      return byteStream(body);
    },
    'cli/cli'
  );
  assert.equal(fetches.length, 2);
  assert.equal(fetches.every((call) => call.authorization === null), true);
  assert.equal(selected.repository, 'cli/cli');
  assert.equal(selected.predicateType, 'https://slsa.dev/provenance/v1');
  assert.equal(selected.workflowPath, '.github/workflows/deployment.yml');
  assert.equal(selected.gitCommit, 'e1219499a578b69e3437adaa31cd60614e23c802');
  assert.equal(selected.subjectDigest, digest);

  const lowered = collectPins(item()).find((entry) => entry.name === 'livingproof-evaluator-run');
  const pin = { ...lowered, repository: 'cli/cli' };
  const evidence = passingEvidence(pin);
  evidence.attestation = {
    published: true,
    status: 200,
    subjectDigest: SOURCE,
    repository: 'https://github.com/CLI/cli',
    gitCommit: COMMIT,
    workflowPath: '.github/workflows/deployment.yml',
    predicateType: 'https://slsa.dev/provenance/v1',
  };
  const record = checkPin(pin, evidence);
  assert.equal(record.status, 'verified');
  assert.equal(record.workflow, '.github/workflows/deployment.yml');

  const second = `sha256:${'ab'.repeat(32)}`;
  const statement = {
    predicateType: 'https://slsa.dev/provenance/v1',
    subject: [
      { name: 'other', digest: { sha256: 'cd'.repeat(32) } },
      { name: 'match', digest: { sha256: 'ab'.repeat(32) } },
    ],
    predicate: {
      buildDefinition: {
        externalParameters: {
          workflow: {
            repository: 'https://github.com/living-proof-labs/robot-capability-specs',
            path: '.github/workflows/build.yml',
          },
        },
        resolvedDependencies: [{ digest: { gitCommit: COMMIT } }],
      },
    },
  };
  const atIndexOne = await openAttestations(
    [
      {
        bundle: {
          dsseEnvelope: { payload: Buffer.from(JSON.stringify({ predicateType: 'https://example.test/other' })).toString('base64') },
        },
      },
      { bundle: { dsseEnvelope: { payload: Buffer.from(JSON.stringify(statement)).toString('base64') } } },
    ],
    second,
    async () => {
      throw new Error('inline bundles are not fetched');
    }
  );
  assert.equal(atIndexOne.subjectDigest, second);
  assert.equal(atIndexOne.repository, 'living-proof-labs/robot-capability-specs');
  assert.equal(atIndexOne.workflowPath, '.github/workflows/build.yml');
});

test('a later attestation matches when the first entry is a different repository', async () => {
  const digestHex = 'ab'.repeat(32);
  const digest = `sha256:${digestHex}`;
  const selected = await openAttestations(
    [
      attestationEntry('https://github.com/other/other', digestHex, '.github/workflows/foreign.yml'),
      attestationEntry('https://github.com/cli/cli', digestHex, '.github/workflows/deployment.yml'),
    ],
    digest,
    async () => {
      throw new Error('inline bundles are not fetched');
    },
    'cli/cli'
  );
  assert.equal(selected.repository, 'cli/cli');
  assert.equal(selected.subjectDigest, digest);
  assert.equal(selected.workflowPath, '.github/workflows/deployment.yml');
  assert.match(selected.reasons.join('; '), /entry 0:.*repository other\/other/);
  assert.doesNotMatch(selected.reasons.join('; '), /entry 1/);

  const missed = await openAttestations(
    [
      attestationEntry('https://github.com/other/other', digestHex),
      attestationEntry('https://github.com/also/wrong', digestHex),
    ],
    digest,
    async () => {
      throw new Error('inline bundles are not fetched');
    },
    'cli/cli'
  );
  assert.equal(missed.match, null);
  assert.match(missed.error, /entry 0:.*repository other\/other/);
  assert.match(missed.error, /entry 1:.*repository also\/wrong/);
});

test('bundle_url is https on the attestation host and the body is capped', async () => {
  const digest = `sha256:${'ab'.repeat(32)}`;
  let calls = 0;
  await assert.rejects(
    () =>
      openAttestations(
        [{ bundle: null, bundle_url: 'http://tmaproduction.blob.core.windows.net/a.sn' }],
        digest,
        async () => {
          calls += 1;
          throw new Error('fetched');
        },
        'cli/cli'
      ),
    /bundle_url must be https/
  );
  await assert.rejects(
    () =>
      openAttestations(
        [{ bundle: null, bundle_url: 'https://evil.example/a.sn' }],
        digest,
        async () => {
          calls += 1;
          throw new Error('fetched');
        },
        'cli/cli'
      ),
    /bundle_url host is not allowed\. expected tmaproduction\.blob\.core\.windows\.net, found evil\.example/
  );
  assert.equal(calls, 0);
  let reads = 0;
  await assert.rejects(
    () =>
      openAttestations(
        [{ bundle: null, bundle_url: 'https://tmaproduction.blob.core.windows.net/a.sn' }],
        digest,
        async (url, options) => {
          assert.equal(options, undefined);
          return {
            ok: true,
            status: 200,
            headers: { get: (name) => (name.toLowerCase() === 'content-length' ? String(MAX_BUNDLE_BYTES + 1) : null) },
            body: {
              getReader() {
                return {
                  async read() {
                    reads += 1;
                    throw new Error('body was read');
                  },
                };
              },
            },
          };
        },
        'cli/cli'
      ),
    new RegExp(`bundle_url content-length ${MAX_BUNDLE_BYTES + 1} exceeds ${MAX_BUNDLE_BYTES} bytes`)
  );
  assert.equal(reads, 0);
  await assert.rejects(
    () =>
      openAttestations(
        [{ bundle: null, bundle_url: 'https://tmaproduction.blob.core.windows.net/a.sn' }],
        digest,
        async () => ({
          ok: true,
          status: 200,
          headers: { get: () => null },
          body: {
            getReader() {
              let sent = false;
              return {
                async read() {
                  if (sent) return { done: true, value: undefined };
                  sent = true;
                  return { done: false, value: { byteLength: MAX_BUNDLE_BYTES + 1 } };
                },
                async cancel() {},
                releaseLock() {},
              };
            },
          },
        }),
        'cli/cli'
      ),
    new RegExp(`bundle_url exceeded ${MAX_BUNDLE_BYTES} bytes`)
  );
});

test('README says a tag bump also updates the lock commit and digest', () => {
  const readme = readFileSync(new URL('../README.md', import.meta.url), 'utf8');
  assert.match(readme, /bumping a tag also means updating that input's commit and digest/);
  assert.doesNotMatch(readme, /needs no hand relock/);
  assert.match(readme, /No `report\.json` is written/);
  assert.match(readme, /livingproof-evaluator-run/);
  assert.match(readme, /docs\/provenance\.md/);
  assert.match(readme, /`verified`/);
  assert.match(readme, /`not-published`/);
  assert.match(readme, /`resolved`/);
  assert.match(readme, /`fixture`/);
  assert.match(readme, /attestation matched/);
  assert.match(readme, /codeload archive of the peeled commit/);
  assert.match(readme, /synthetic sha256 of those file hashes/);
  assert.match(readme, /`status: resolved` can sit beside `attestation: not-published`/);
  assert.match(readme, /GITHUB_TOKEN` from `github\.token`/);
  assert.match(readme, /That 404 is not recorded as `not-published`/);
});
