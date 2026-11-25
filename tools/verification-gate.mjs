#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Same contract as Living-Proof-Labs/.github tools/github/verification-gate.mjs:
// every needed job must be the expected result. Failure, cancellation, neutral,
// missing, and an unexpected skip fail. `version` is the only expected skip,
// and only when .github/workflows/verify.yml already skips it.
import { pathToFileURL } from 'node:url';

export const verificationJobs = [
  'verify',
  'site-policy',
  'version',
  'action-smoke',
  'action-smoke-check',
  'g1-smoke',
  'g1-smoke-check',
  'site-smoke',
];

export function versionRuns(event, baseRef = '') {
  return event === 'push' || (event === 'pull_request' && baseRef === 'main');
}

export function verifyResults(needs, event, baseRef = '') {
  if (!['pull_request', 'merge_group', 'push'].includes(event)) {
    throw new Error('Unsupported verification event.');
  }
  if (
    !needs ||
    typeof needs !== 'object' ||
    Array.isArray(needs) ||
    Object.keys(needs).length !== verificationJobs.length ||
    Object.keys(needs).some((job) => !verificationJobs.includes(job))
  ) {
    throw new Error('Missing or unexpected verification job evidence.');
  }
  for (const job of verificationJobs) {
    const expected = job === 'version' && !versionRuns(event, baseRef) ? 'skipped' : 'success';
    if (needs[job]?.result !== expected) {
      throw new Error(`Verification job ${job} must be ${expected}; observed ${needs[job]?.result ?? 'missing'}.`);
    }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    verifyResults(
      JSON.parse(process.env.VERIFICATION_NEEDS ?? ''),
      process.env.VERIFICATION_EVENT,
      process.env.VERIFICATION_BASE_REF ?? '',
    );
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
