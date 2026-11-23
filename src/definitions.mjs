// SPDX-License-Identifier: Apache-2.0
import { createHash } from 'node:crypto';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';

export class ReportError extends Error {
  constructor(message) {
    super(message);
    this.name = 'ReportError';
  }
}

/** capability-verifier release the report workflow calls. Plan refuses a tag that peels elsewhere. */
export const VERIFIER_WORKFLOW_SHA = '0c00c388c693012392da0c1cd391046c752e9b3c';
export const VERIFIER_WORKFLOW_TAG = 'v2.11.4';
export const VERIFIER_OWNER = 'Living-Proof-Labs/capability-verifier';

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

export function isFixtureCaller(repository) {
  return (
    repository === 'Living-Proof-Labs/verification-reports-action' ||
    repository.endsWith('/verification-reports-action')
  );
}

export function isFixtureDefinition(definitionPath, repository) {
  const normalized = definitionPath.split(path.sep).join('/');
  return (
    isFixtureCaller(repository) &&
    normalized.includes('tests/fixtures/robot/verifications/') &&
    normalized.endsWith('.toml')
  );
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

/** Subset of TOML used by livingproof-verification-definition/0.1.0. */
export function parseToml(text, file) {
  const root = {};
  let table = root;
  for (const rawLine of text.split(/\r?\n/)) {
    const line = stripComment(rawLine).trim();
    if (!line) continue;
    const tableMatch = line.match(/^\[([A-Za-z0-9_.-]+)\]$/);
    if (tableMatch) {
      table = root;
      for (const part of tableMatch[1].split('.')) {
        if (!table[part] || typeof table[part] !== 'object' || Array.isArray(table[part])) {
          table[part] = {};
        }
        table = table[part];
      }
      continue;
    }
    const separator = line.indexOf('=');
    if (separator === -1) throw new ReportError(`${file}: cannot parse ${rawLine}`);
    const key = line.slice(0, separator).trim();
    table[key] = parseValue(line.slice(separator + 1));
  }
  return root;
}

export function expandDefinitions(pattern, root) {
  const normalized = pattern.trim();
  if (!normalized) throw new ReportError('definitions must be a non-empty glob');
  const slash = normalized.lastIndexOf('/');
  const directory = slash === -1 ? '.' : normalized.slice(0, slash);
  const base = slash === -1 ? normalized : normalized.slice(slash + 1);
  if (directory.includes('*') || base.includes('**')) {
    throw new ReportError(
      `definitions glob ${pattern} is not supported. Use one directory and a single * in the file name, for example verifications/*.toml.`
    );
  }
  const absolute = path.resolve(root, directory);
  let names;
  try {
    names = readdirSync(absolute);
  } catch (error) {
    throw new ReportError(
      `definitions glob ${pattern} did not match a directory (${absolute}): ${error.message}`
    );
  }
  const expression = new RegExp(
    `^${base.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replaceAll('*', '.*')}$`
  );
  return names
    .filter((name) => expression.test(name) && name.endsWith('.toml'))
    .sort()
    .map((name) => path.join(directory, name).split(path.sep).join('/'));
}

function configurationNames(toml, lock, file) {
  const fromToml = toml.robot?.configurations;
  if (!Array.isArray(fromToml) || fromToml.length === 0 || fromToml.some((name) => typeof name !== 'string')) {
    throw new ReportError(`${file}: [robot].configurations must be a non-empty list of names`);
  }
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
  const absolute = path.resolve(root, file);
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
  const lockAbsolute = path.resolve(root, lockRelative);
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
  const matches = expandDefinitions(inputs.definitions ?? 'verifications/*.toml', root);
  const definitions = [];
  for (const match of matches) {
    const loaded = loadDefinition(match, root);
    if (!loaded) continue;
    const execution = isFixtureDefinition(loaded.path, robot.callerRepository)
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
