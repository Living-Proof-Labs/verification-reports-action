// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const PIN_RE = /Living-Proof-Labs\/livingproof-evaluator-run@v[0-9][0-9.]*(?:-[0-9A-Za-z.]+)?\/livingproof-evaluator\.json/g;

function declaredPin() {
  const raw = readFileSync(path.join(root, 'evaluators.yml'), 'utf8');
  const match = raw.match(
    /^-\s+(Living-Proof-Labs\/livingproof-evaluator-run@v[0-9][0-9.]*(?:-[0-9A-Za-z.]+)?\/livingproof-evaluator\.json)\s*$/m,
  );
  assert.ok(match, 'evaluators.yml must declare a Run Integrity pin');
  return match[1];
}

function markdownFiles() {
  const found = [];
  const visit = (relative) => {
    const absolute = path.join(root, relative);
    if (statSync(absolute).isDirectory()) {
      for (const name of readdirSync(absolute)) {
        if (name === 'node_modules' || name === '.git' || name === 'fixtures' || name === 'old-runs') {
          continue;
        }
        visit(path.join(relative, name));
      }
      return;
    }
    if (relative.endsWith('.md')) found.push(relative);
  };
  visit('README.md');
  visit('docs');
  return found;
}

test('documentation examples of the Run Integrity pin match evaluators.yml', () => {
  const pin = declaredPin();
  const stale = [];
  for (const file of markdownFiles()) {
    const text = readFileSync(path.join(root, file), 'utf8');
    for (const match of text.matchAll(PIN_RE)) {
      if (match[0] !== pin) stale.push(`${file}: ${match[0]}`);
    }
  }
  assert.deepEqual(stale, []);
});
