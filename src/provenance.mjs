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
 * Pins the report resolves from release tags. Commits and digests stored beside
 * those tags are not expected values: a tag bump does not need them rewritten.
 */
const SPECS = [
  {
    name: 'capability-verifier',
    kind: 'image',
    workflow: true,
    defaultImage: VERIFIER_IMAGE,
    read: (lock, sources) => fromRelease(lock.releases?.verifier, sources?.releases?.verifier),
  },
  {
    name: 'livingproof-sim-mujoco',
    kind: 'source',
    read: (lock, sources) => fromRelease(lock.releases?.runner, sources?.releases?.runner),
  },
  {
    name: 'robot-capability-specs',
    kind: 'files',
    read: (lock, sources) => fromRelease(lock.releases?.specs, sources?.releases?.specs, true),
  },
  {
    name: 'livingproof-worlds',
    kind: 'files',
    read: (lock, sources) => fromWorld(lock.world, sources?.world),
  },
  {
    name: 'livingproof-evaluator-scenario',
    kind: 'source',
    read: (lock, sources) =>
      fromRelease(
        lock.evaluators?.['livingproof-evaluator-scenario'],
        sources?.evaluators?.['livingproof-evaluator-scenario']
      ),
  },
  {
    name: 'livingproof-evaluator-run',
    kind: 'source',
    read: (lock, sources) =>
      fromRelease(
        lock.evaluators?.['livingproof-evaluator-run'],
        sources?.evaluators?.['livingproof-evaluator-run']
      ),
  },
  {
    name: 'livingproof-capability-report',
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
  if (withFiles && lockEntry?.files && typeof lockEntry.files === 'object' && !Array.isArray(lockEntry.files)) {
    for (const path of Object.keys(lockEntry.files)) files[path] = true;
  }
  return {
    repository,
    tag,
    image: text(lockEntry?.image),
    files,
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
  return { repository, tag, image: '', files, worldId: id, worldVersion: version };
}

function requirePin(spec, raw) {
  if (!raw) {
    throw new ReportError(
      `${spec.name}: pin is missing. expected a repository and a vX.Y.Z tag, found no pin.`
    );
  }
  if (!REPOSITORY.test(raw.repository)) {
    throw new ReportError(
      `${spec.name}: repository is not OWNER/NAME. expected OWNER/NAME, found ${JSON.stringify(raw.repository)}.`
    );
  }
  if (!TAG.test(raw.tag)) {
    throw new ReportError(
      `${spec.name}: tag is not a release tag. expected a vX.Y.Z tag, found ${JSON.stringify(raw.tag)}.`
    );
  }
  const image = raw.image || spec.defaultImage || '';
  if (spec.kind === 'image' && !image.startsWith('ghcr.io/')) {
    throw new ReportError(
      `${spec.name}: image host is unsupported. expected a ghcr.io image, found ${JSON.stringify(image)}.`
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
  };
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
  const gitCommit = evidence.attestation?.gitCommit;
  if (evidence.attestation?.published && typeof gitCommit === 'string' && SHA.test(gitCommit)) {
    return gitCommit;
  }
  return null;
}

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

function checkAttestation(pin, digest, attestation) {
  if (!attestation || attestation.published !== true) return 'not-published';
  const subject = asDigest(attestation.subjectDigest);
  if (subject !== digest) {
    throw new ReportError(
      `${pin.name}: attestation mismatch. expected subject ${digest} from ${pin.repository}, found subject ${subject || 'none'} from ${attestation.repository || 'unknown'}.`
    );
  }
  if (attestation.repository && attestation.repository !== pin.repository) {
    throw new ReportError(
      `${pin.name}: attestation mismatch. expected subject ${digest} from ${pin.repository}, found subject ${subject} from ${attestation.repository}.`
    );
  }
  return 'passed';
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
  if (publishedCommit && publishedCommit !== tagCommit) {
    throw new ReportError(
      `${pin.name}: tag ${pin.tag} moved. expected commit ${publishedCommit}, found commit ${tagCommit}.`
    );
  }
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
  const attestation = checkAttestation(pin, digest, evidence.attestation);
  return {
    name: pin.name,
    repository: pin.repository,
    tag: pin.tag,
    commit: tagCommit,
    digest,
    result: 'passed',
    attestation,
    checks: {
      release: 'passed',
      commit: publishedCommit ? 'passed' : 'resolved',
      digest: digestCheck,
      attestation,
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
    if (record?.result !== 'passed') {
      throw new ReportError(
        `${where}: ${record?.name ?? 'input'} provenance result is ${JSON.stringify(record?.result)}, so no report can be written.`
      );
    }
  }
}
