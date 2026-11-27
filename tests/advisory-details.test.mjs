// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { copyCitedDetails } from '../src/assemble.mjs';
import { sha256 } from '../src/definitions.mjs';

function report(uri) {
  return {
    source_bundle: {
      assessment: {
        advisory_results: {
          cited: { advisory: { details_file: { uri } } },
        },
      },
    },
  };
}

function layout(uri, bytes, digest) {
  const root = mkdtempSync(path.join(tmpdir(), 'vra-details-'));
  const artifact = path.join(root, 'artifact');
  const destination = path.join(root, 'run');
  mkdirSync(artifact, { recursive: true });
  mkdirSync(destination, { recursive: true });
  writeFileSync(path.join(artifact, 'SHA256SUMS'), `${digest}  ${uri}\n`);
  return { artifact, destination };
}

test('assemble refuses a cited details file with a wrong sha', () => {
  const bytes = Buffer.from('{"ok":true}\n');
  const { artifact, destination } = layout('notes.json', bytes, '0'.repeat(64));
  writeFileSync(path.join(artifact, 'notes.json'), bytes);
  assert.throws(
    () => copyCitedDetails(artifact, destination, report('notes.json')),
    /does not match SHA256SUMS/,
  );
});

test('assemble refuses a cited details path that leaves the report', () => {
  const bytes = Buffer.from('{}\n');
  const digest = sha256(bytes);
  const { artifact, destination } = layout('../x.json', bytes, digest);
  writeFileSync(path.join(artifact, '..', 'x.json'), bytes);
  assert.throws(
    () => copyCitedDetails(artifact, destination, report('../x.json')),
    /is not a relative path/,
  );
});

test('assemble refuses a cited absolute details path', () => {
  const { artifact, destination } = layout('/tmp/x.json', Buffer.from(''), '0'.repeat(64));
  assert.throws(
    () => copyCitedDetails(artifact, destination, report('/tmp/x.json')),
    /is absolute/,
  );
});

test('assemble refuses a cited details symlink', () => {
  const bytes = Buffer.from('{"via":"link"}\n');
  const { artifact, destination } = layout('notes.json', bytes, sha256(bytes));
  writeFileSync(path.join(artifact, 'target.json'), bytes);
  symlinkSync('target.json', path.join(artifact, 'notes.json'));
  assert.throws(
    () => copyCitedDetails(artifact, destination, report('notes.json')),
    /is a symlink/,
  );
});

test('assemble refuses a cited details file missing from SHA256SUMS', () => {
  const bytes = Buffer.from('{}\n');
  const { artifact, destination } = layout('other.json', bytes, sha256(bytes));
  writeFileSync(path.join(artifact, 'notes.json'), bytes);
  writeFileSync(path.join(artifact, 'other.json'), bytes);
  assert.throws(
    () => copyCitedDetails(artifact, destination, report('notes.json')),
    /not listed in SHA256SUMS/,
  );
});

test('assemble accepts one advisory result or a list of them', () => {
  const bytes = Buffer.from('{"n":1}\n');
  const digest = sha256(bytes);
  const { artifact, destination } = layout('notes.json', bytes, digest);
  writeFileSync(path.join(artifact, 'notes.json'), bytes);
  const one = { advisory: { details_file: { uri: 'notes.json' } } };
  copyCitedDetails(artifact, destination, {
    source_bundle: { assessment: { advisory_results: one } },
  });
  const listed = {
    source_bundle: { assessment: { advisory_results: { example: [one] } } },
  };
  copyCitedDetails(artifact, destination, listed);
});
