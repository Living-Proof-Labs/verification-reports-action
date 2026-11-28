// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { parseToml, VERIFIER_WORKFLOW_SHA, VERIFIER_WORKFLOW_TAG } from '../src/definitions.mjs';
import { checkPin, collectPins, ignoredEvaluatorNotice, resolvedTrialInputs } from '../src/provenance.mjs';
import { imageDigestFromBody } from '../src/github-provenance.mjs';

// Commits and digests recorded from a read-only live resolve on 2026-11-24.
// The release notes of worlds v1.4.0 and specs v2.3.0
// list other changelog SHAs; the peeled tag is what these records keep.
const LOCKS = ['unitree-g1-stock-mujoco-corner-left', 'booster-t1-stock-mujoco-hallway'];

function loadLock(id) {
  const directory = new URL('./fixtures/real-locks/', import.meta.url);
  const lock = JSON.parse(readFileSync(new URL(`${id}.lock.json`, directory), 'utf8'));
  const toml = parseToml(readFileSync(new URL(`${id}.toml`, directory), 'utf8'), `${id}.toml`);
  const recorded = JSON.parse(readFileSync(new URL(`${id}.result.json`, directory), 'utf8'));
  return {
    item: {
      id,
      path: `${id}.toml`,
      lock,
      sources: {
        world: toml.world ?? null,
        releases: toml.releases ?? null,
        evaluators: toml.evaluators ?? null,
      },
    },
    recorded,
  };
}

function evidenceFor(pin, recorded) {
  const files = {};
  for (const path of pin.paths) {
    const hash = pin.lockRaw.files?.[path] || pin.lockRaw.sha256 || pin.sourceWorld?.sha256;
    files[path] = { published: hash, found: hash };
  }
  return {
    releaseFound: true,
    tagCommit: recorded.commit,
    publishedCommit: null,
    files,
    imageDigest: pin.kind === 'image' ? recorded.digest : null,
    publishedDigest: pin.kind === 'image' ? recorded.digest : null,
    sourceDigest: pin.paths.length === 0 && pin.kind !== 'image' ? recorded.digest : null,
    attestation: { published: false, status: 404 },
  };
}

const VERIFIER_RELEASE = JSON.parse(
  readFileSync(new URL('./fixtures/releases/capability-verifier-v3.1.1.json', import.meta.url), 'utf8')
);

function dropPassedFields(lock) {
  const copy = structuredClone(lock);
  delete copy.releases.verifier.commit;
  delete copy.releases.verifier.image;
  delete copy.releases.verifier.digest;
  delete copy.releases.reporter.image;
  delete copy.releases.reporter.digest;
  copy.releases.verifier.tag = VERIFIER_WORKFLOW_TAG;
  return copy;
}

for (const id of LOCKS) {
  test(`${id} as published is refused because it still carries the passed pin fields`, () => {
    const { item } = loadLock(id);
    assert.throws(() => collectPins(item), /capability-verifier: releases\.verifier\.commit is refused/);
  });

  test(`${id} passes once those fields are absent and the verifier tag is ${VERIFIER_WORKFLOW_TAG}`, () => {
    const { item, recorded } = loadLock(id);
    item.lock = dropPassedFields(item.lock);
    const pins = collectPins(item);
    const comparable = recorded.filter((entry) => pins.some((pin) => pin.name === entry.name));
    assert.equal(pins.length, comparable.length);
    assert.ok(recorded.some((entry) => !pins.some((pin) => pin.name === entry.name)));
    const notice = ignoredEvaluatorNotice(item.lock);
    assert.equal(typeof notice, 'string');
    for (const key of Object.keys(item.lock.evaluators)) {
      assert.equal(notice.includes(key), false);
    }
    const records = [];
    for (const expected of comparable) {
      const pin = pins.find((entry) => entry.name === expected.name);
      const evidence =
        expected.name === 'capability-verifier'
          ? {
              ...evidenceFor(pin, {
                ...expected,
                commit: VERIFIER_RELEASE.tagCommit,
                digest: imageDigestFromBody(VERIFIER_RELEASE.body, VERIFIER_RELEASE.image),
              }),
              publishedCommit: null,
            }
          : evidenceFor(pin, expected);
      const record = checkPin(pin, evidence);
      records.push(record);
      if (expected.name === 'capability-verifier') {
        assert.equal(record.tag, VERIFIER_WORKFLOW_TAG);
        assert.equal(record.commit, VERIFIER_WORKFLOW_SHA);
        assert.equal(record.digest, VERIFIER_RELEASE.digest);
      } else {
        assert.equal(record.repository, expected.repository);
        assert.equal(record.tag, expected.tag);
        assert.equal(record.commit, expected.commit);
        assert.equal(record.digest, expected.digest);
      }
      assert.equal(record.status, 'resolved');
    }
    const inputs = resolvedTrialInputs(records);
    assert.equal(inputs.verifier_commit, VERIFIER_RELEASE.tagCommit);
    assert.equal(
      inputs.verifier_image,
      `${VERIFIER_RELEASE.image}:${VERIFIER_RELEASE.tag.slice(1)}@${VERIFIER_RELEASE.digest}`
    );
    const reporter = recorded.find((entry) => entry.name === 'livingproof-capability-report');
    assert.equal(inputs.reporter_image.endsWith(`@${reporter.digest}`), true);
    assert.equal('evaluator_commit' in inputs, false);
    assert.equal(
      pins.some((pin) => pin.name === 'livingproof-evaluator-scenario'),
      false
    );
  });
}
