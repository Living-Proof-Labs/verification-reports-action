// SPDX-License-Identifier: Apache-2.0
import { createHash } from 'node:crypto';
import { lstatSync, readdirSync, readFileSync, realpathSync } from 'node:fs';
import path from 'node:path';

export class ReportError extends Error {
  constructor(message) {
    super(message);
    this.name = 'ReportError';
  }
}

// GitHub API paths built from lock or caller data. Every segment must be a plain name:
// no "." or ".." segment (fetch's URL parser would resolve it), no empty segment or
// leading "/", no backslash (read as "/"), no "%" (so no encoded form), no control
// character, and no "?" or "#" (which would end the path).
const API_PATH_FORBIDDEN = /[\u0000-\u001f\u007f\\%?#]/;

export function apiPathSegments(value, label) {
  const refuse = (why) => {
    throw new ReportError(
      `${label} is not a safe API path. expected relative path segments, found ${JSON.stringify(value)} (${why}).`
    );
  };
  if (typeof value !== 'string' || value === '') refuse('empty or not a string');
  const bad = value.match(API_PATH_FORBIDDEN);
  if (bad) refuse(`${JSON.stringify(bad[0])} is not allowed`);
  const segments = value.split('/');
  // This also refuses a leading or trailing '/'.
  if (segments.some((segment) => segment === '')) refuse('empty segment');
  if (segments.some((segment) => segment === '.' || segment === '..')) refuse('dot segment');
  return segments;
}

// Builds `${api}/<segments>?<query>`. Each part is checked with apiPathSegments and each
// segment is percent-encoded, so no value can change the path or add to the query.
export function githubApiUrl(api, parts, { query = null, label = 'GitHub API path' } = {}) {
  const path = parts
    .flatMap((part) => apiPathSegments(part, label))
    // ':' is left as is: it is inert in a path and attestation digests use it.
    .map((segment) => encodeURIComponent(segment).replace(/%3A/g, ':'))
    .join('/');
  const search = query ? new URLSearchParams(query).toString() : '';
  return `${api}/${path}${search ? `?${search}` : ''}`;
}

/** capability-verifier release the report workflow calls. Plan refuses a tag that peels elsewhere. */
export const VERIFIER_WORKFLOW_SHA = 'fdbf233f6fbaef3bf6589a056821bde3ebb634e2';
export const VERIFIER_WORKFLOW_TAG = 'v2.12.0';
export const VERIFIER_OWNER = 'Living-Proof-Labs/capability-verifier';

/** This repository. Fixture reports are produced only when it calls its own workflow. */
export const THIS_REPOSITORY = 'Living-Proof-Labs/verification-reports-action';

/** One path segment: a lowercase slug, no slash, and no "..". */
export const CONFIGURATION_SLUG = /^[a-z0-9][a-z0-9._-]*$/;

const BLOCKED_TOML_KEYS = new Set(['__proto__', 'constructor', 'prototype']);

export const PROCEDURES = {
  'walk-hallway': '.github/workflows/walk-hallway.yml',
  scenario: '.github/workflows/scenario.yml',
};

const SHA = /^[0-9a-f]{40}$/;

export function verifierWorkflow(procedure) {
  const file = PROCEDURES[procedure];
  if (!file) {
    throw new ReportError(
      `procedure ${procedure} is not one of ${Object.keys(PROCEDURES).join(', ')}`
    );
  }
  return `${VERIFIER_OWNER}/${file}@${VERIFIER_WORKFLOW_SHA}`;
}

/**
 * True only when `job.workflow_repository == github.repository` and that caller
 * is this repository. A different repository whose name ends in
 * `/verification-reports-action` is not a fixture.
 */
export function isFixtureCaller(callerRepository, workflowRepository) {
  if (typeof callerRepository !== 'string' || typeof workflowRepository !== 'string') return false;
  const caller = callerRepository.trim();
  const workflow = workflowRepository.trim();
  return caller.length > 0 && caller === workflow && caller === THIS_REPOSITORY;
}

export function isFixtureDefinition(definitionPath, callerRepository, workflowRepository) {
  const normalized = definitionPath.split(path.sep).join('/');
  return (
    isFixtureCaller(callerRepository, workflowRepository) &&
    normalized.includes('tests/fixtures/robot/verifications/') &&
    normalized.endsWith('.toml')
  );
}

export function assertConfigurationSlug(name, where) {
  if (
    typeof name !== 'string' ||
    name.includes('..') ||
    name.includes('/') ||
    name.includes('\\') ||
    !CONFIGURATION_SLUG.test(name)
  ) {
    throw new ReportError(
      `${where}: configuration ${JSON.stringify(name)} must match ${CONFIGURATION_SLUG} and must not contain "/", "\\", or "..". Example: stock.`
    );
  }
}

export function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

function stripComment(line) {
  let inString = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    if (char === '"') inString = !inString;
    if (char === '#' && !inString) return line.slice(0, index);
  }
  return line;
}

function parseValue(raw) {
  const value = raw.trim();
  if (value.startsWith('"') && value.endsWith('"')) return value.slice(1, -1);
  if (value.startsWith('[')) {
    const body = value.slice(1, value.lastIndexOf(']'));
    if (!body.trim()) return [];
    return body.split(',').map((item) => {
      const text = item.trim();
      if (text.startsWith('"') && text.endsWith('"')) return text.slice(1, -1);
      throw new ReportError(`unsupported array item ${text}`);
    });
  }
  return value;
}

function createTable() {
  return Object.create(null);
}

function assertTomlKey(key, file) {
  if (BLOCKED_TOML_KEYS.has(key)) {
    throw new ReportError(
      `${file}: key ${JSON.stringify(key)} is not allowed. Use a table or key name other than __proto__, constructor, or prototype.`
    );
  }
}

/** Subset of TOML used by livingproof-verification-definition/0.1.0. */
export function parseToml(text, file) {
  const root = createTable();
  let table = root;
  for (const rawLine of text.split(/\r?\n/)) {
    const line = stripComment(rawLine).trim();
    if (!line) continue;
    const tableMatch = line.match(/^\[([A-Za-z0-9_.-]+)\]$/);
    if (tableMatch) {
      table = root;
      for (const part of tableMatch[1].split('.')) {
        assertTomlKey(part, file);
        const existing = table[part];
        if (!existing || typeof existing !== 'object' || Array.isArray(existing)) {
          table[part] = createTable();
        }
        table = table[part];
      }
      continue;
    }
    const separator = line.indexOf('=');
    if (separator === -1) throw new ReportError(`${file}: cannot parse ${rawLine}`);
    const key = line.slice(0, separator).trim();
    assertTomlKey(key, file);
    table[key] = parseValue(line.slice(separator + 1));
  }
  return root;
}

function checkoutRoot(root) {
  const resolved = path.resolve(root);
  try {
    return realpathSync(resolved);
  } catch (error) {
    throw new ReportError(`checkout ${resolved} is not a directory: ${error.message}`);
  }
}

function containedPath(rootReal, relativePath, label) {
  const normalized = String(relativePath).split(path.sep).join('/');
  if (normalized.startsWith('/') || normalized.split('/').includes('..')) {
    throw new ReportError(
      `${label} escapes the checkout. Stay inside the caller checkout; ".." is rejected.`
    );
  }
  const segments = normalized.split('/').filter((segment) => segment.length > 0 && segment !== '.');
  let current = rootReal;
  for (const segment of segments) {
    current = path.join(current, segment);
    let stat;
    try {
      stat = lstatSync(current);
    } catch (error) {
      const wrapped = new ReportError(`${label} is not inside the checkout (${current}): ${error.message}`);
      wrapped.code = error.code;
      throw wrapped;
    }
    if (stat.isSymbolicLink()) {
      throw new ReportError(`${label} is a symlink, which is rejected (${current})`);
    }
  }
  let real;
  try {
    real = realpathSync(current);
  } catch (error) {
    const wrapped = new ReportError(`${label} is not inside the checkout (${current}): ${error.message}`);
    wrapped.code = error.code;
    throw wrapped;
  }
  const relative = path.relative(rootReal, real);
  if (relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new ReportError(`${label} resolves outside the checkout (${real})`);
  }
  return real;
}

export function expandDefinitions(pattern, root) {
  const normalized = pattern.trim().split(path.sep).join('/');
  if (!normalized) throw new ReportError('definitions must be a non-empty glob');
  if (normalized.startsWith('/') || normalized.includes('\\') || normalized.split('/').includes('..')) {
    throw new ReportError(
      `definitions glob ${pattern} escapes the checkout. Use one directory inside the caller checkout, for example verifications/*.toml.`
    );
  }
  const slash = normalized.lastIndexOf('/');
  const directory = slash === -1 ? '.' : normalized.slice(0, slash);
  const base = slash === -1 ? normalized : normalized.slice(slash + 1);
  if (directory.includes('*') || base.includes('**')) {
    throw new ReportError(
      `definitions glob ${pattern} is not supported. Use one directory and a single * in the file name, for example verifications/*.toml.`
    );
  }
  const rootReal = checkoutRoot(root);
  let directoryReal;
  try {
    directoryReal = containedPath(rootReal, directory, `definitions glob ${pattern}`);
  } catch (error) {
    if (error instanceof ReportError && error.code === 'ENOENT') {
      throw new ReportError(
        `definitions glob ${pattern} did not match a directory (${directory}): ${error.message}`
      );
    }
    throw error;
  }
  let names;
  try {
    names = readdirSync(directoryReal);
  } catch (error) {
    throw new ReportError(
      `definitions glob ${pattern} did not match a directory (${directoryReal}): ${error.message}`
    );
  }
  const expression = new RegExp(
    `^${base.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replaceAll('*', '.*')}$`
  );
  return names
    .filter((name) => expression.test(name) && name.endsWith('.toml'))
    .sort()
    .map((name) => {
      const relative = (directory === '.' ? name : `${directory}/${name}`).split(path.sep).join('/');
      containedPath(rootReal, relative, `definitions glob ${pattern}`);
      return relative;
    });
}

function configurationNames(toml, lock, file) {
  const fromToml = toml.robot?.configurations;
  if (!Array.isArray(fromToml) || fromToml.length === 0 || fromToml.some((name) => typeof name !== 'string')) {
    throw new ReportError(`${file}: [robot].configurations must be a non-empty list of names`);
  }
  for (const name of fromToml) assertConfigurationSlug(name, file);
  const locked = Array.isArray(lock.configurations)
    ? lock.configurations.map((entry) => entry?.name).filter((name) => typeof name === 'string')
    : fromToml;
  const missing = fromToml.filter((name) => !locked.includes(name));
  if (missing.length > 0) {
    throw new ReportError(
      `${file}: configurations ${missing.join(', ')} are not in the lock`
    );
  }
  return fromToml;
}

export function loadDefinition(relativePath, root) {
  const file = relativePath.split(path.sep).join('/');
  const rootReal = checkoutRoot(root);
  const absolute = containedPath(rootReal, file, file);
  const bytes = readFileSync(absolute);
  const text = bytes.toString('utf8');
  if (!text.includes('livingproof-verification-definition/')) return null;
  const toml = parseToml(text, file);
  const format = toml.format;
  if (typeof format !== 'string' || !format.startsWith('livingproof-verification-definition/')) {
    throw new ReportError(`${file}: format must be livingproof-verification-definition/...`);
  }
  if (typeof toml.id !== 'string' || !/^[a-z0-9][a-z0-9-]*$/.test(toml.id)) {
    throw new ReportError(`${file}: id must be a lowercase slug`);
  }
  if (!Object.hasOwn(PROCEDURES, toml.procedure)) {
    throw new ReportError(
      `${file}: procedure must be one of ${Object.keys(PROCEDURES).join(', ')}`
    );
  }
  const lockRelative = file.replace(/\.toml$/, '.lock.json');
  let lockAbsolute;
  try {
    lockAbsolute = containedPath(rootReal, lockRelative, lockRelative);
  } catch (error) {
    if (error instanceof ReportError && error.code === 'ENOENT') {
      throw new ReportError(`${lockRelative} is missing or is not JSON: ${error.message}`);
    }
    throw error;
  }
  let lock;
  try {
    lock = JSON.parse(readFileSync(lockAbsolute, 'utf8'));
  } catch (error) {
    throw new ReportError(`${lockRelative} is missing or is not JSON: ${error.message}`);
  }
  if (typeof lock.format !== 'string' || !lock.format.startsWith('livingproof-verification-lock/')) {
    throw new ReportError(`${lockRelative}: format must be livingproof-verification-lock/...`);
  }
  const digest = sha256(bytes);
  if (lock.definition?.sha256 !== digest) {
    throw new ReportError(
      `${lockRelative}: definition sha256 is ${lock.definition?.sha256 ?? 'missing'}, not ${digest}. The definition changed after it was locked.`
    );
  }
  if (lock.procedure !== toml.procedure) {
    throw new ReportError(
      `${lockRelative}: procedure ${lock.procedure} differs from ${file} (${toml.procedure})`
    );
  }
  return {
    id: toml.id,
    path: file,
    lockPath: lockRelative,
    procedure: toml.procedure,
    scenario: typeof toml.scenario === 'string' ? toml.scenario : '',
    configurations: configurationNames(toml, lock, file),
    lock,
    digest,
    sources: {
      world: toml.world && typeof toml.world === 'object' ? toml.world : null,
      releases: toml.releases && typeof toml.releases === 'object' ? toml.releases : null,
      evaluators: toml.evaluators && typeof toml.evaluators === 'object' ? toml.evaluators : null,
    },
  };
}

export function resolveRobot(inputs, env) {
  const callerRepository = (env.GITHUB_REPOSITORY ?? '').trim();
  const callerSha = (env.GITHUB_SHA ?? '').trim();
  if (!callerRepository || !SHA.test(callerSha)) {
    throw new ReportError(
      'GITHUB_REPOSITORY and a full GITHUB_SHA are required. The robot under test is the caller at the checked-out commit.'
    );
  }
  const repository = (inputs.repository ?? '').trim();
  const ref = (inputs.ref ?? '').trim();
  if (!repository || repository === callerRepository) {
    if (ref && ref !== callerSha) {
      throw new ReportError(
        `The robot under test is ${callerRepository} at the checked-out commit ${callerSha}, not ref ${ref}.`
      );
    }
    return { repository: callerRepository, ref: callerSha, callerRepository, callerSha };
  }
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository)) {
    throw new ReportError(`repository ${repository} must be OWNER/NAME`);
  }
  if (!SHA.test(ref)) {
    throw new ReportError(
      `repository ${repository} must be pinned by a full commit SHA in ref. A release tag can move.`
    );
  }
  return { repository, ref, callerRepository, callerSha };
}

export function planDefinitions(inputs, env, root = process.cwd()) {
  const robot = resolveRobot(inputs, env);
  const workflowRepository = typeof env.WORKFLOW_REPOSITORY === 'string' ? env.WORKFLOW_REPOSITORY : '';
  const matches = expandDefinitions(inputs.definitions ?? 'verifications/*.toml', root);
  const definitions = [];
  for (const match of matches) {
    const loaded = loadDefinition(match, root);
    if (!loaded) continue;
    const execution = isFixtureDefinition(loaded.path, robot.callerRepository, workflowRepository)
      ? 'fixture'
      : 'capability-verifier';
    definitions.push({
      ...loaded,
      execution,
      robot: { repository: robot.repository, ref: robot.ref },
      callerRepository: robot.callerRepository,
      artifactPrefix: `vra-${loaded.id}-`,
      workflow: execution === 'capability-verifier' ? verifierWorkflow(loaded.procedure) : '',
    });
  }
  if (definitions.length === 0) {
    throw new ReportError(
      `definitions glob ${inputs.definitions ?? 'verifications/*.toml'} matched no livingproof-verification-definition files`
    );
  }
  return { robot, definitions };
}

export function matrixFor(plan, execution, procedure) {
  return plan.definitions
    .filter((item) => item.execution === execution && (!procedure || item.procedure === procedure))
    .map((item) => ({
      definition: item.path,
      lock: item.lockPath,
      id: item.id,
      procedure: item.procedure,
      repository: item.robot.repository,
      ref: item.robot.ref,
      configurations: JSON.stringify(item.configurations),
      artifact_prefix: item.artifactPrefix,
    }));
}
