// SPDX-License-Identifier: Apache-2.0
import { sha256, ReportError, VERIFIER_WORKFLOW_SHA, VERIFIER_WORKFLOW_TAG } from './definitions.mjs';

const REPOSITORY = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const TAG = /^v\d+\.\d+\.\d+$/;
const SHA = /^[0-9a-f]{40}$/;
const HEX64 = /^[0-9a-f]{64}$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;

const VERIFIER_IMAGE = 'ghcr.io/living-proof-labs/capability-verifier-hallway';
const REPORTER_IMAGE = 'ghcr.io/living-proof-labs/livingproof-capability-report';

/**
 * The tag is what people edit. assertLockAgrees is the temporary check that
 * the lock fields capability-verifier reads are exactly the resolved values.
 * When scenario.yml takes those resolved values as inputs, delete
 * assertLockAgrees and pass the resolved commit and image reference through.
 */
const SPECS = [
  {
    name: 'capability-verifier',
    lockField: 'releases.verifier',
    kind: 'image',
    workflow: true,
    defaultImage: VERIFIER_IMAGE,
    read: (lock, sources) => fromRelease(lock.releases?.verifier, sources?.releases?.verifier),
  },
  {
    name: 'livingproof-sim-mujoco',
    lockField: 'releases.runner',
    kind: 'source',
    read: (lock, sources) => fromRelease(lock.releases?.runner, sources?.releases?.runner),
  },
  {
    name: 'robot-capability-specs',
    lockField: 'releases.specs',
    kind: 'files',
    read: (lock, sources) => fromRelease(lock.releases?.specs, sources?.releases?.specs, true),
  },
  {
    name: 'livingproof-worlds',
    lockField: 'world',
    kind: 'files',
    read: (lock, sources) => fromWorld(lock.world, sources?.world),
  },
  {
    name: 'livingproof-evaluator-scenario',
    lockField: 'evaluators.livingproof-evaluator-scenario',
    kind: 'source',
    read: (lock, sources) =>
      fromRelease(
        lock.evaluators?.['livingproof-evaluator-scenario'],
        sources?.evaluators?.['livingproof-evaluator-scenario']
      ),
  },
  {
    name: 'livingproof-evaluator-run',
    lockField: 'evaluators.livingproof-evaluator-run',
    kind: 'source',
    read: (lock, sources) =>
      fromRelease(
        lock.evaluators?.['livingproof-evaluator-run'],
        sources?.evaluators?.['livingproof-evaluator-run']
      ),
  },
  {
    name: 'livingproof-capability-report',
    lockField: 'releases.reporter',
    kind: 'image',
    defaultImage: REPORTER_IMAGE,
    read: (lock, sources) => fromRelease(lock.releases?.reporter, sources?.releases?.reporter),
  },
];

export const REQUIRED_INPUTS = SPECS.map((spec) => spec.name);

function text(value) {
  return typeof value === 'string' ? value.trim() : '';
}

function fromRelease(lockEntry, tomlEntry, withFiles = false) {
  const repository = text(tomlEntry?.repository) || text(lockEntry?.repository);
  const tag = text(tomlEntry?.tag) || text(lockEntry?.tag);
  if (!repository && !tag && !lockEntry && !tomlEntry) return null;
  const files = {};
  const lockFiles = {};
  if (withFiles && lockEntry?.files && typeof lockEntry.files === 'object' && !Array.isArray(lockEntry.files)) {
    for (const [path, hash] of Object.entries(lockEntry.files)) {
      files[path] = true;
      if (typeof hash === 'string') lockFiles[path] = hash.trim().toLowerCase();
    }
  }
  return {
    repository,
    tag,
    image: text(lockEntry?.image),
    files,
    lockCommit: text(lockEntry?.commit),
    lockDigest: text(lockEntry?.digest),
    lockSha256: text(lockEntry?.sha256).toLowerCase(),
    lockImage: text(lockEntry?.image),
    lockFiles,
  };
}

function fromWorld(lockWorld, tomlWorld) {
  const repository = text(tomlWorld?.repository) || text(lockWorld?.repository);
  const tag = text(tomlWorld?.tag) || text(lockWorld?.tag);
  const id = text(tomlWorld?.id) || text(lockWorld?.id);
  const version = text(tomlWorld?.version) || text(lockWorld?.version);
  if (!repository && !tag && !id && !version) return null;
  const files = {};
  if (id && version && !id.includes('/') && !version.includes('/') && !id.includes('..') && !version.includes('..')) {
    files[`worlds/${id}/${version}/world.json`] = true;
  }
  return {
    repository,
    tag,
    image: text(lockWorld?.image),
    files,
    worldId: id,
    worldVersion: version,
    lockCommit: text(lockWorld?.commit),
    lockDigest: text(lockWorld?.digest),
    lockSha256: text(lockWorld?.sha256).toLowerCase(),
    lockImage: text(lockWorld?.image),
    lockFiles: {},
  };
}

function requirePin(spec, raw) {
  if (!raw) {
    throw new ReportError(
      `${spec.name}: pin is missing. expected a repository and a vX.Y.Z tag, found no pin.`
    );
  }
  const segments = raw.repository.split('/');
  if (
    !REPOSITORY.test(raw.repository) ||
    segments.length !== 2 ||
    segments.some((segment) => segment === '' || segment === '.' || segment === '..')
  ) {
    throw new ReportError(
      `${spec.name}: repository is not OWNER/NAME. expected OWNER/NAME, found ${JSON.stringify(raw.repository)}.`
    );
  }
  if (!TAG.test(raw.tag)) {
    throw new ReportError(
      `${spec.name}: tag is not a release tag. expected a vX.Y.Z tag, found ${JSON.stringify(raw.tag)}.`
    );
  }
  if (raw.image && raw.image !== spec.defaultImage) {
    throw new ReportError(
      `${spec.name}: ${spec.lockField}.image name is not the default. expected ${spec.defaultImage || 'no image'}, found ${raw.image}.`
    );
  }
  const image = spec.defaultImage || '';
  if (spec.kind === 'image' && !image.startsWith('ghcr.io/living-proof-labs/')) {
    throw new ReportError(
      `${spec.name}: ${spec.lockField}.image name is not the default. expected a ghcr.io/living-proof-labs image, found ${JSON.stringify(image)}.`
    );
  }
  if (spec.name === 'livingproof-worlds' && Object.keys(raw.files).length === 0) {
    throw new ReportError(
      `${spec.name}: world id and version are missing. expected worlds/<id>/<version>/world.json, found id ${JSON.stringify(raw.worldId)} and version ${JSON.stringify(raw.worldVersion)}.`
    );
  }
  return {
    name: spec.name,
    kind: spec.kind,
    workflow: Boolean(spec.workflow),
    repository: raw.repository,
    tag: raw.tag,
    image,
    paths: Object.keys(raw.files).sort(),
    lockField: spec.lockField,
    lockCommit: raw.lockCommit || '',
    lockDigest: raw.lockDigest || '',
    lockSha256: raw.lockSha256 || '',
    lockImage: raw.lockImage || '',
    lockFiles: raw.lockFiles || {},
  };
}

export function normaliseRepository(value) {
  if (typeof value !== 'string') return '';
  let name = value.trim().replace(/\.git$/i, '');
  const url = name.match(/^https?:\/\/github\.com\/([^/\s]+)\/([^/\s#?]+)/i);
  if (url) name = `${url[1]}/${url[2]}`;
  return name;
}

export function sameRepository(left, right) {
  const a = normaliseRepository(left);
  const b = normaliseRepository(right);
  return a.length > 0 && a.toLowerCase() === b.toLowerCase();
}

/**
 * Lock fields that the trial reads must equal the values resolved from the tag.
 * This is the drop-in point for the follow-up that passes those resolved values
 * into capability-verifier and stops reading them from the lock.
 */
export function assertLockAgrees(pin, resolved) {
  if (pin.lockCommit && pin.lockCommit !== resolved.commit) {
    throw new ReportError(
      `${pin.name}: ${pin.lockField}.commit differs from the resolved tag. expected ${resolved.commit}, found ${pin.lockCommit}.`
    );
  }
  if (pin.lockDigest && asDigest(pin.lockDigest) !== resolved.digest) {
    throw new ReportError(
      `${pin.name}: ${pin.lockField}.digest differs from the resolved tag. expected ${resolved.digest}, found ${pin.lockDigest}.`
    );
  }
  const fileHash = (resolved.digest || '').replace(/^sha256:/, '');
  if (pin.lockSha256 && pin.lockSha256 !== fileHash) {
    throw new ReportError(
      `${pin.name}: ${pin.lockField}.sha256 differs from the resolved tag. expected ${fileHash}, found ${pin.lockSha256}.`
    );
  }
  for (const [path, locked] of Object.entries(pin.lockFiles)) {
    const found = resolved.files?.[path] || '';
    if (locked !== found) {
      throw new ReportError(
        `${pin.name}: ${pin.lockField}.files[${path}] differs from the resolved tag. expected ${found || 'the file sha256'}, found ${locked}.`
      );
    }
  }
  if (pin.kind === 'image' && pin.lockImage) {
    const version = pin.tag.replace(/^v/, '');
    const expected = `${pin.image}:${version}@${resolved.digest}`;
    const foundDigest = asDigest(pin.lockDigest) || resolved.digest;
    const found = `${pin.lockImage}:${version}@${foundDigest}`;
    if (found !== expected) {
      throw new ReportError(
        `${pin.name}: ${pin.lockField}.image differs from the resolved tag. expected ${expected}, found ${found}.`
      );
    }
  }
}

export function collectPins(item) {
  const lock = item?.lock && typeof item.lock === 'object' ? item.lock : {};
  const sources = item?.sources && typeof item.sources === 'object' ? item.sources : {};
  return SPECS.map((spec) => requirePin(spec, spec.read(lock, sources)));
}

export function fixtureProvenance(item) {
  const lock = item?.lock && typeof item.lock === 'object' ? item.lock : {};
  const sources = item?.sources && typeof item.sources === 'object' ? item.sources : {};
  return SPECS.map((spec) => {
    const raw = spec.read(lock, sources);
    const digest = digestHint(spec, lock);
    return {
      name: spec.name,
      repository: raw?.repository || null,
      tag: raw?.tag || null,
      commit: commitHint(spec, lock),
      digest: digest || null,
      status: 'fixture',
      result: 'fixture',
      attestation: 'fixture',
      checks: { release: 'fixture', commit: 'fixture', digest: 'fixture', attestation: 'fixture' },
    };
  });
}

function commitHint(spec, lock) {
  const entry = entryFor(spec, lock);
  const commit = text(entry?.commit);
  return SHA.test(commit) ? commit : null;
}

function digestHint(spec, lock) {
  const entry = entryFor(spec, lock);
  return text(entry?.digest) || text(entry?.sha256);
}

function entryFor(spec, lock) {
  if (spec.name === 'capability-verifier') return lock.releases?.verifier;
  if (spec.name === 'livingproof-sim-mujoco') return lock.releases?.runner;
  if (spec.name === 'robot-capability-specs') return lock.releases?.specs;
  if (spec.name === 'livingproof-worlds') return lock.world;
  if (spec.name === 'livingproof-evaluator-scenario') return lock.evaluators?.['livingproof-evaluator-scenario'];
  if (spec.name === 'livingproof-evaluator-run') return lock.evaluators?.['livingproof-evaluator-run'];
  if (spec.name === 'livingproof-capability-report') return lock.releases?.reporter;
  return null;
}

function asDigest(value) {
  if (typeof value !== 'string' || !value) return null;
  if (DIGEST.test(value)) return value;
  if (HEX64.test(value)) return `sha256:${value}`;
  return null;
}

function publishedCommitOf(evidence) {
  if (typeof evidence.publishedCommit === 'string' && SHA.test(evidence.publishedCommit)) {
    return evidence.publishedCommit;
  }
  return null;
}

export const PROVENANCE_STATUSES = ['verified', 'not-published', 'resolved', 'fixture'];

export function subjectDigest(pin, evidence) {
  if (pin.kind === 'image') return asDigest(evidence.imageDigest);
  if (pin.paths.length === 1) {
    const found = evidence.files?.[pin.paths[0]]?.found;
    return asDigest(found);
  }
  if (pin.paths.length > 1) {
    const lines = pin.paths.map((path) => {
      const found = evidence.files?.[path]?.found ?? '';
      return `${found}  ${path}`;
    });
    return `sha256:${sha256(lines.join('\n'))}`;
  }
  return asDigest(evidence.sourceDigest);
}

function checkFiles(pin, evidence) {
  let compared = false;
  for (const path of pin.paths) {
    const row = evidence.files?.[path];
    const found = text(row?.found);
    const published = text(row?.published);
    if (!HEX64.test(found)) {
      throw new ReportError(
        `${pin.name}: sha256 mismatch for ${path}. expected a sha256 of the file at ${pin.tag}, found ${found || 'no file'}.`
      );
    }
    if (!published) continue;
    compared = true;
    if (!HEX64.test(published) || published !== found) {
      throw new ReportError(
        `${pin.name}: sha256 mismatch for ${path}. expected ${published || 'a published sha256'}, found ${found}.`
      );
    }
  }
  return compared ? 'passed' : 'resolved';
}

function checkAttestation(pin, digest, tagCommit, attestation) {
  if (attestation?.published === true) {
    const subject = asDigest(attestation.subjectDigest);
    if (subject !== digest) {
      throw new ReportError(
        `${pin.name}: attestation digest mismatch. expected subject ${digest}, found subject ${subject || 'none'}.`
      );
    }
    if (!sameRepository(attestation.repository, pin.repository)) {
      throw new ReportError(
        `${pin.name}: attestation source repository mismatch. expected ${pin.repository}, found ${attestation.repository || 'none'}.`
      );
    }
    if (attestation.gitCommit !== tagCommit) {
      throw new ReportError(
        `${pin.name}: attestation commit mismatch. expected commit ${tagCommit}, found commit ${attestation.gitCommit || 'none'}.`
      );
    }
    return 'verified';
  }
  if (attestation?.published === false && attestation.status === 404) return 'not-published';
  const status = attestation?.status ?? 'none';
  throw new ReportError(
    `${pin.name}: attestation request failed. expected HTTP 200 or HTTP 404, found HTTP ${status}.`
  );
}

export function checkPin(pin, evidence, options = {}) {
  const workflowSha = options.workflowSha ?? VERIFIER_WORKFLOW_SHA;
  const workflowTag = options.workflowTag ?? VERIFIER_WORKFLOW_TAG;
  if (!evidence || evidence.releaseFound !== true) {
    throw new ReportError(
      `${pin.name}: release ${pin.repository}@${pin.tag} is missing. expected a GitHub Release for ${pin.tag}, found no release.`
    );
  }
  const tagCommit = text(evidence.tagCommit);
  if (!SHA.test(tagCommit)) {
    throw new ReportError(
      `${pin.name}: tag ${pin.tag} did not resolve. expected a commit for ${pin.repository}@${pin.tag}, found ${tagCommit || 'no tag'}.`
    );
  }
  const publishedCommit = publishedCommitOf(evidence);
  if (pin.workflow && tagCommit !== workflowSha) {
    throw new ReportError(
      `${pin.name}: workflow pin mismatch. expected commit ${workflowSha} (${workflowTag}), found commit ${tagCommit} for tag ${pin.tag}.`
    );
  }
  let digestCheck = 'resolved';
  if (pin.kind === 'files' || (pin.kind === 'source' && pin.paths.length > 0)) {
    digestCheck = checkFiles(pin, evidence);
  }
  const digest = subjectDigest(pin, evidence);
  if (!digest) {
    throw new ReportError(
      `${pin.name}: digest is missing. expected a sha256 digest for ${pin.repository}@${pin.tag}, found none.`
    );
  }
  if (pin.kind === 'image') {
    const published = asDigest(evidence.publishedDigest);
    const found = asDigest(evidence.imageDigest);
    if (published && published !== found) {
      throw new ReportError(
        `${pin.name}: image digest mismatch for ${pin.image}. expected ${published}, found ${found || 'none'}.`
      );
    }
    digestCheck = published ? 'passed' : 'resolved';
  } else if (pin.paths.length === 0) {
    const published = asDigest(evidence.publishedDigest);
    if (published && published !== digest) {
      throw new ReportError(
        `${pin.name}: sha256 mismatch for ${pin.repository}@${pin.tag}. expected ${published}, found ${digest}.`
      );
    }
    digestCheck = published ? 'passed' : 'resolved';
  }
  const attestation = checkAttestation(pin, digest, tagCommit, evidence.attestation);
  if (publishedCommit && publishedCommit !== tagCommit) {
    throw new ReportError(
      `${pin.name}: tag ${pin.tag} moved. expected commit ${publishedCommit}, found commit ${tagCommit}.`
    );
  }
  const status = attestation === 'verified' ? 'verified' : publishedCommit ? 'not-published' : 'resolved';
  assertLockAgrees(pin, {
    commit: tagCommit,
    digest,
    files: Object.fromEntries(pin.paths.map((path) => [path, evidence.files?.[path]?.found || ''])),
  });
  return {
    name: pin.name,
    repository: pin.repository,
    tag: pin.tag,
    commit: tagCommit,
    digest,
    workflow: evidence.attestation?.workflowPath || '',
    status,
    result: status,
    attestation: status === 'verified' ? 'verified' : 'not-published',
    checks: {
      release: 'passed',
      commit: publishedCommit ? 'passed' : 'resolved',
      digest: digestCheck,
      attestation: status === 'verified' ? 'verified' : 'not-published',
    },
  };
}

export async function assertPinnedInputs(item, client, options = {}) {
  const pins = collectPins(item);
  const records = [];
  for (const pin of pins) {
    const evidence = await client.resolve(pin);
    records.push(checkPin(pin, evidence, options));
  }
  return records;
}

/** Run the writer only after every pin passes. A refusal leaves the report unwritten. */
export async function runIfProvenancePasses(item, client, options, write) {
  const records = await assertPinnedInputs(item, client, options);
  return write(records);
}

export function assertRecordsPassed(item) {
  const where = item?.path || item?.id || 'plan';
  const records = item?.provenance;
  if (!Array.isArray(records)) {
    throw new ReportError(`${where}: provenance is missing, so no report can be written.`);
  }
  const names = new Set(records.map((record) => record?.name));
  for (const name of REQUIRED_INPUTS) {
    if (!names.has(name)) {
      throw new ReportError(`${where}: provenance for ${name} is missing, so no report can be written.`);
    }
  }
  for (const record of records) {
    if (!['verified', 'not-published', 'resolved'].includes(record?.status)) {
      throw new ReportError(
        `${where}: ${record?.name ?? 'input'} provenance status is ${JSON.stringify(record?.status)}, so no report can be written.`
      );
    }
  }
}
