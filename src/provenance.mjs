// SPDX-License-Identifier: Apache-2.0
import {
  apiPathSegments,
  sha256,
  ReportError,
  VERIFIER_WORKFLOW_SHA,
  VERIFIER_WORKFLOW_TAG,
} from './definitions.mjs';

const REPOSITORY = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
const TAG = /^v\d+\.\d+\.\d+$/;
const SHA = /^[0-9a-f]{40}$/;
const HEX64 = /^[0-9a-f]{64}$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;

const VERIFIER_IMAGE = 'ghcr.io/living-proof-labs/capability-verifier-hallway';
const REPORTER_IMAGE = 'ghcr.io/living-proof-labs/livingproof-capability-report';

/**
 * The tag is what people edit. capability-verifier v2.13.0 takes the resolved
 * verifier commit, verifier image, reporter image and scenario-evaluator commit
 * as workflow inputs. Those lock fields are refused when present. The fields
 * v2.13.0 still reads from the lock stay, and must match the resolved tag.
 * Declaration lines from the caller evaluators input own every other evaluators entry.
 */
/**
 * required: the trial reads the lock field and does not substitute a default.
 * optional: absent is allowed; a present value must still be a valid untrimmed
 * string and match the resolved tag. rejected: the field must be absent. The
 * action passes the resolved value instead.
 */
const SPECS = [
  {
    name: 'capability-verifier',
    lockField: 'releases.verifier',
    expectedRepository: 'Living-Proof-Labs/capability-verifier',
    kind: 'image',
    workflow: true,
    defaultImage: VERIFIER_IMAGE,
    fields: { repository: 'required', tag: 'required' },
    rejected: ['commit', 'image', 'digest'],
    read: (lock, sources) => fromRelease(lock.releases?.verifier, sources?.releases?.verifier),
  },
  {
    name: 'livingproof-sim-mujoco',
    lockField: 'releases.runner',
    expectedRepository: 'Living-Proof-Labs/livingproof-sim-mujoco',
    kind: 'source',
    fields: { repository: 'required', tag: 'required', commit: 'optional' },
    read: (lock, sources) => fromRelease(lock.releases?.runner, sources?.releases?.runner),
  },
  {
    name: 'robot-capability-specs',
    lockField: 'releases.specs',
    expectedRepository: 'Living-Proof-Labs/robot-capability-specs',
    kind: 'files',
    fields: { repository: 'required', tag: 'required', commit: 'required', files: 'optional' },
    read: (lock, sources) =>
      fromRelease(lock.releases?.specs, sources?.releases?.specs, true, {
        sourceParameters: lock.sources?.parameters ?? null,
        scenario: lock.scenario,
      }),
  },
  {
    name: 'livingproof-worlds',
    lockField: 'world',
    expectedRepository: 'Living-Proof-Labs/livingproof-worlds',
    kind: 'files',
    fields: { repository: 'required', tag: 'required', commit: 'optional', sha256: 'optional' },
    read: (lock, sources) => fromWorld(lock.world, sources?.world, lock.sources?.world),
  },
  {
    name: 'livingproof-evaluator-scenario',
    lockField: 'evaluators.livingproof-evaluator-scenario',
    expectedRepository: 'Living-Proof-Labs/livingproof-evaluator-scenario',
    kind: 'source',
    fields: { repository: 'required', tag: 'required' },
    rejected: ['commit'],
    read: (lock, sources) =>
      fromRelease(
        lock.evaluators?.['livingproof-evaluator-scenario'],
        sources?.evaluators?.['livingproof-evaluator-scenario']
      ),
  },
  {
    name: 'livingproof-capability-report',
    lockField: 'releases.reporter',
    expectedRepository: 'Living-Proof-Labs/livingproof-capability-report',
    kind: 'image',
    defaultImage: REPORTER_IMAGE,
    fields: { repository: 'required', tag: 'required', commit: 'required' },
    rejected: ['image', 'digest'],
    read: (lock, sources) => fromRelease(lock.releases?.reporter, sources?.releases?.reporter),
  },
];

export const REQUIRED_INPUTS = SPECS.map((spec) => spec.name);

function text(value) {
  return typeof value === 'string' ? value.trim() : '';
}

function lockRaw(entry) {
  if (!entry || typeof entry !== 'object') {
    return { repository: undefined, tag: undefined, commit: undefined, image: undefined, digest: undefined, sha256: undefined, files: undefined };
  }
  return {
    repository: entry.repository,
    tag: entry.tag,
    commit: entry.commit,
    image: entry.image,
    digest: entry.digest,
    sha256: entry.sha256,
    files: entry.files,
  };
}

function fromRelease(lockEntry, tomlEntry, withFiles = false, options = {}) {
  const repository = text(tomlEntry?.repository) || text(lockEntry?.repository);
  const tag = text(tomlEntry?.tag) || text(lockEntry?.tag);
  if (!repository && !tag && !lockEntry && !tomlEntry) return null;
  const files = {};
  const raw = lockRaw(lockEntry);
  if (withFiles && raw.files && typeof raw.files === 'object' && !Array.isArray(raw.files)) {
    for (const path of Object.keys(raw.files)) files[path] = true;
  }
  const parameters = parametersBinding(options.sourceParameters, options.scenario, withFiles);
  if (parameters.file) files[parameters.file] = true;
  return {
    repository,
    tag,
    image: text(lockEntry?.image),
    files,
    lockRaw: raw,
    definitionRaw: lockRaw(tomlEntry),
    sourceParameters: parameters.entry,
    parametersFile: parameters.file,
  };
}

function scenarioParametersFile(scenario) {
  if (typeof scenario !== 'string' || scenario.length === 0) return null;
  const id = scenario.split('@', 1)[0];
  if (id.length === 0) return null;
  return `specs/scenarios/${id}/parameters.json`;
}

function parametersBinding(entry, scenario, withFiles) {
  if (!withFiles || !entry || typeof entry !== 'object') return { entry: null, file: null };
  if (entry.path !== undefined && entry.path !== null) {
    apiPathSegments(entry.path, 'robot-capability-specs: sources.parameters.path');
  }
  if (entry.sha256 !== undefined && entry.sha256 !== null) {
    assertLockString('robot-capability-specs', 'sources.parameters.sha256', entry.sha256, FIELD_FORM.sha256);
  }
  const file = scenarioParametersFile(scenario);
  if (!file) {
    throw new ReportError(
      'robot-capability-specs: scenario is missing. expected a scenario id so sources.parameters.sha256 can be checked against specs/scenarios/<id>/parameters.json, found none.'
    );
  }
  apiPathSegments(file, 'robot-capability-specs: scenario parameters file');
  return { entry, file };
}

function fromWorld(lockWorld, tomlWorld, sourceWorld) {
  const repository = text(tomlWorld?.repository) || text(lockWorld?.repository);
  const tag = text(tomlWorld?.tag) || text(lockWorld?.tag);
  const id = text(tomlWorld?.id) || text(lockWorld?.id);
  const version = text(tomlWorld?.version) || text(lockWorld?.version);
  if (!repository && !tag && !id && !version) return null;
  const files = {};
  if (id && version) {
    for (const [field, value] of [['id', id], ['version', version]]) {
      if (apiPathSegments(value, `livingproof-worlds: world.${field}`).length !== 1) {
        throw new ReportError(
          `livingproof-worlds: world.${field} is not a safe API path. expected one path segment, found ${JSON.stringify(value)}.`
        );
      }
    }
    files[`worlds/${id}/${version}/world.json`] = true;
  }
  return {
    repository,
    tag,
    image: text(lockWorld?.image),
    files,
    worldId: id,
    worldVersion: version,
    lockRaw: lockRaw(lockWorld),
    definitionRaw: lockRaw(tomlWorld),
    sourceWorld: sourceWorld && typeof sourceWorld === 'object' ? sourceWorld : null,
  };
}

function requirePin(spec, raw) {
  if (!raw) {
    if (!spec.defaultRepository) {
      throw new ReportError(
        `${spec.name}: pin is missing. expected a repository and a vX.Y.Z tag, found no pin.`
      );
    }
    raw = {
      repository: '',
      tag: '',
      image: '',
      files: {},
      lockRaw: lockRaw(null),
      definitionRaw: lockRaw(null),
    };
  }
  const image = spec.defaultImage || '';
  applyScenarioDefaults(spec, raw);
  assertDroppedFields(spec, raw.lockRaw ?? {});
  assertConsumedLock(spec, raw.repository, raw.tag, image, raw.lockRaw, raw.definitionRaw, raw.sourceWorld);
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
    tag: raw.tag,
    image,
    paths: Object.keys(raw.files).sort(),
    lockField: spec.lockField,
    lockRaw: raw.lockRaw,
    sourceWorld: raw.sourceWorld ?? null,
    sourceParameters: raw.sourceParameters ?? null,
    parametersFile: raw.parametersFile ?? null,
    repository: spec.expectedRepository,
  };
}

function assertDroppedFields(spec, raw) {
  for (const key of spec.rejected ?? []) {
    if (raw[key] === undefined) continue;
    throw new ReportError(
      `${spec.name}: ${spec.lockField}.${key} is refused. expected the field to be absent so the resolved value is passed to capability-verifier, found ${JSON.stringify(raw[key])}.`
    );
  }
}

const FIELD_FORM = {
  repository: { pattern: REPOSITORY, expected: 'an OWNER/NAME repository' },
  tag: { pattern: TAG, expected: 'a vX.Y.Z tag' },
  commit: { pattern: SHA, expected: 'a 40-character commit' },
  image: { pattern: null, expected: 'the default image name' },
  digest: { pattern: /^(?:sha256:)?[0-9a-f]{64}$/, expected: 'a sha256 digest' },
  sha256: { pattern: HEX64, expected: 'a 64-character sha256' },
};

function assertLockString(name, field, value, form) {
  if (value === undefined || value === null) {
    throw new ReportError(`${name}: ${field} is missing. expected ${form.expected}, found none.`);
  }
  if (typeof value !== 'string') {
    throw new ReportError(`${name}: ${field} is not a string. expected ${form.expected}, found ${JSON.stringify(value)}.`);
  }
  if (value === '') {
    throw new ReportError(`${name}: ${field} is empty. expected ${form.expected}, found empty.`);
  }
  if (value !== value.trim()) {
    throw new ReportError(`${name}: ${field} has surrounding whitespace. expected ${form.expected}, found ${JSON.stringify(value)}.`);
  }
  if (form.pattern && !form.pattern.test(value)) {
    throw new ReportError(`${name}: ${field} is not the expected form. expected ${form.expected}, found ${JSON.stringify(value)}.`);
  }
}

function applyScenarioDefaults(spec, raw) {
  const lock = raw.lockRaw ?? {};
  const defined = raw.definitionRaw ?? {};
  if (spec.defaultRepository && lock.repository == null) {
    if (defined.repository != null && !sameRepository(defined.repository, spec.defaultRepository)) {
      throw new ReportError(
        `${spec.name}: ${spec.lockField}.repository is not what scenario.yml runs. expected ${spec.defaultRepository}, found ${defined.repository}.`
      );
    }
    raw.repository = spec.defaultRepository;
  }
  if (spec.defaultTag && lock.tag == null) {
    if (defined.tag != null && defined.tag !== spec.defaultTag) {
      throw new ReportError(
        `${spec.name}: ${spec.lockField}.tag is not what scenario.yml runs. expected ${spec.defaultTag}, found ${defined.tag}.`
      );
    }
    raw.tag = spec.defaultTag;
  }
}

function assertExpectedRepository(spec, value, where) {
  if (sameRepository(value, spec.expectedRepository)) return;
  throw new ReportError(
    `${spec.name}: ${spec.lockField}.repository is not the expected repository. expected ${spec.expectedRepository}, found ${value}.`
  );
}

function assertConsumedLock(spec, repository, tag, image, raw, definition, sourceWorld) {
  const values = raw ?? {};
  const defined = definition ?? {};
  if (defined.repository !== undefined && defined.repository !== null) {
    assertLockString(spec.name, `${spec.lockField}.repository`, defined.repository, FIELD_FORM.repository);
  }
  if (defined.tag !== undefined && defined.tag !== null) {
    assertLockString(spec.name, `${spec.lockField}.tag`, defined.tag, FIELD_FORM.tag);
  }
  for (const [key, rule] of Object.entries(spec.fields)) {
    if (key === 'files') continue;
    const field = `${spec.lockField}.${key}`;
    const value = values[key];
    const absent = value === undefined || value === null;
    if (absent) {
      if (rule === 'required') {
        throw new ReportError(`${spec.name}: ${field} is missing. expected ${FIELD_FORM[key].expected}, found none.`);
      }
      continue;
    }
    assertLockString(spec.name, field, value, FIELD_FORM[key]);
    if (key === 'repository' && defined.repository != null && !sameRepository(value, defined.repository)) {
      throw new ReportError(
        `${spec.name}: ${field} differs from the definition. expected ${defined.repository}, found ${value}.`
      );
    }
    if (key === 'repository') assertExpectedRepository(spec, value);
    if (key === 'tag' && defined.tag != null && value !== defined.tag) {
      throw new ReportError(
        `${spec.name}: ${field} differs from the definition. expected ${defined.tag}, found ${value}.`
      );
    }
    if (key === 'tag' && defined.tag == null && value !== tag) {
      throw new ReportError(
        `${spec.name}: ${field} differs from the definition. expected ${tag}, found ${value}.`
      );
    }
    if (key === 'image' && value !== image) {
      throw new ReportError(
        `${spec.name}: ${field} name is not the default. expected ${image}, found ${value}.`
      );
    }
  }
  if (!sameRepository(repository, spec.expectedRepository)) {
    assertExpectedRepository(spec, repository);
  }
  if (spec.kind !== 'image' && values.image !== undefined && values.image !== null && values.image !== '') {
    throw new ReportError(
      `${spec.name}: ${spec.lockField}.image name is not the default. expected no image, found ${JSON.stringify(values.image)}.`
    );
  }
  const files = values.files;
  if (spec.name === 'livingproof-worlds') {
    const hasSha = values.sha256 !== undefined && values.sha256 !== null && values.sha256 !== '';
    const sourceSha = sourceWorld && typeof sourceWorld === 'object' ? sourceWorld.sha256 : undefined;
    const hasSource = sourceSha !== undefined && sourceSha !== null && sourceSha !== '';
    if (!hasSha && !hasSource) {
      throw new ReportError(
        `${spec.name}: world is unbound. expected world.sha256 or sources.world, found neither.`
      );
    }
    if (hasSource) assertLockString(spec.name, 'sources.world.sha256', sourceSha, FIELD_FORM.sha256);
  }
  if (files !== undefined && files !== null && spec.fields.files) {
    if (typeof files !== 'object' || Array.isArray(files)) {
      throw new ReportError(
        `${spec.name}: ${spec.lockField}.files is not a string map. expected file sha256 values, found ${JSON.stringify(files)}.`
      );
    }
    for (const [path, hash] of Object.entries(files)) {
      // The key becomes the path of a token-bearing contents API request.
      apiPathSegments(path, `${spec.name}: ${spec.lockField}.files key`);
      assertLockString(spec.name, `${spec.lockField}.files[${path}]`, hash, FIELD_FORM.sha256);
    }
  }
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

const VERIFIER_IMAGE_INPUT =
  /^ghcr\.io\/living-proof-labs\/capability-verifier-hallway:(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)@sha256:[0-9a-f]{64}$/;
const REPORTER_IMAGE_INPUT =
  /^ghcr\.io\/living-proof-labs\/livingproof-capability-report:(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)@sha256:[0-9a-f]{64}$/;

function imageInput(name, tag, digest, label, pattern) {
  const semver = typeof tag === 'string' ? tag.replace(/^v/, '') : '';
  const ref = `${name}:${semver}@${digest}`;
  if (!pattern.test(ref)) {
    throw new ReportError(
      `${label} is not a resolved image ref. expected ${name}:<semver>@sha256:<64 hex>, found ${JSON.stringify(ref)}.`
    );
  }
  return ref;
}

/**
 * The four inputs capability-verifier v2.13.0 scenario.yml accepts. Built from
 * the commits and image digests the action already resolved from the tags.
 */
export function resolvedTrialInputs(records) {
  const byName = new Map((records ?? []).map((record) => [record.name, record]));
  const verifier = byName.get('capability-verifier');
  const reporter = byName.get('livingproof-capability-report');
  const evaluator = byName.get('livingproof-evaluator-scenario');
  if (!verifier) {
    throw new ReportError('resolved pins are missing capability-verifier, so no trial can start.');
  }
  if (!reporter) {
    throw new ReportError('resolved pins are missing livingproof-capability-report, so no trial can start.');
  }
  if (!evaluator) {
    throw new ReportError('resolved pins are missing livingproof-evaluator-scenario, so no trial can start.');
  }
  if (!SHA.test(verifier.commit)) {
    throw new ReportError(
      `verifier-commit is not a resolved commit. expected a 40-character commit, found ${JSON.stringify(verifier.commit)}.`
    );
  }
  if (!SHA.test(evaluator.commit)) {
    throw new ReportError(
      `evaluator-commit is not a resolved commit. expected a 40-character commit, found ${JSON.stringify(evaluator.commit)}.`
    );
  }
  return {
    verifier_commit: verifier.commit,
    verifier_image: imageInput(VERIFIER_IMAGE, verifier.tag, verifier.digest, 'verifier-image', VERIFIER_IMAGE_INPUT),
    reporter_image: imageInput(REPORTER_IMAGE, reporter.tag, reporter.digest, 'reporter-image', REPORTER_IMAGE_INPUT),
    evaluator_commit: evaluator.commit,
  };
}

function disagree(pin, field, expected, found) {
  throw new ReportError(
    `${pin.name}: ${field} differs from the resolved tag. expected ${expected}, found ${found}.`
  );
}

/**
 * Lock fields the trial still reads must equal the values resolved from the tag.
 * Fields passed as workflow inputs are refused earlier and are not compared here.
 */
export function assertLockAgrees(pin, resolved) {
  const raw = pin.lockRaw ?? {};
  const fileHash = (resolved.digest || '').replace(/^sha256:/, '');
  if (pin.lockField === 'releases.runner' && raw.commit != null && raw.commit !== resolved.commit) {
    disagree(pin, `${pin.lockField}.commit`, resolved.commit, raw.commit);
  }
  if (pin.lockField === 'releases.specs' && raw.commit !== resolved.commit) {
    disagree(pin, `${pin.lockField}.commit`, resolved.commit, raw.commit);
  }
  if (pin.lockField === 'releases.reporter' && raw.commit !== resolved.commit) {
    disagree(pin, `${pin.lockField}.commit`, resolved.commit, raw.commit);
  }
  if (pin.lockField === 'world' && raw.commit != null && raw.commit !== resolved.commit) {
    disagree(pin, `${pin.lockField}.commit`, resolved.commit, raw.commit);
  }
  if (pin.lockField === 'world' && typeof raw.sha256 === 'string' && raw.sha256 !== fileHash) {
    disagree(pin, `${pin.lockField}.sha256`, fileHash, raw.sha256);
  }
  const sourceSha = pin.sourceWorld?.sha256;
  if (pin.lockField === 'world' && typeof sourceSha === 'string' && sourceSha !== fileHash) {
    disagree(pin, 'sources.world.sha256', fileHash, sourceSha);
  }
  if (pin.lockField === 'releases.specs' && raw.files && typeof raw.files === 'object' && !Array.isArray(raw.files)) {
    for (const [path, locked] of Object.entries(raw.files)) {
      const found = resolved.files?.[path] || '';
      if (locked !== found) disagree(pin, `${pin.lockField}.files[${path}]`, found || 'the file sha256', locked);
    }
  }
  const parametersSha = pin.sourceParameters?.sha256;
  if (pin.lockField === 'releases.specs' && typeof parametersSha === 'string') {
    const found = resolved.files?.[pin.parametersFile] || '';
    if (parametersSha !== found) disagree(pin, 'sources.parameters.sha256', found || 'the file sha256', parametersSha);
  }
}

/** The one evaluator the trial still resolves. Every other lock evaluators entry is ignored. */
const RESOLVED_EVALUATOR = 'livingproof-evaluator-scenario';

export const IGNORED_EVALUATOR_NOTICE =
  'notice: a lock evaluators entry is ignored; the caller evaluators list declares those lines';

export function ignoredEvaluatorNotice(lock) {
  const evaluators = lock?.evaluators;
  if (!evaluators || typeof evaluators !== 'object' || Array.isArray(evaluators)) return null;
  const extra = Object.keys(evaluators).filter((key) => key !== RESOLVED_EVALUATOR);
  if (extra.length === 0) return null;
  return IGNORED_EVALUATOR_NOTICE;
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
