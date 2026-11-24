// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { parseToml } from '../src/definitions.mjs';
import { checkPin, collectPins } from '../src/provenance.mjs';

// Commits and digests recorded from a read-only live resolve on 2026-11-24.
// The release notes of worlds v1.4.0, specs v2.3.0 and evaluator-scenario v1.5.3
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

for (const id of LOCKS) {
  test(`${id} passes against the recorded live resolution`, () => {
    const { item, recorded } = loadLock(id);
    const pins = collectPins(item);
    assert.equal(pins.length, recorded.length);
    for (const expected of recorded) {
      const pin = pins.find((entry) => entry.name === expected.name);
      const record = checkPin(pin, evidenceFor(pin, expected));
      assert.equal(record.repository, expected.repository);
      assert.equal(record.tag, expected.tag);
      assert.equal(record.commit, expected.commit);
      assert.equal(record.digest, expected.digest);
      assert.equal(record.status, 'resolved');
    }
  });
}
