// SPDX-License-Identifier: Apache-2.0
import { createHash } from 'node:crypto';
import { ReportError, githubApiUrl, sha256 } from './definitions.mjs';
import { normaliseRepository, sameRepository, subjectDigest } from './provenance.mjs';
import { decodeMaybeSnappy } from './snappy.mjs';

export const MAX_BUNDLE_BYTES = 16 * 1024 * 1024;
export const BUNDLE_TIMEOUT_MS = 30_000;

// GitHub's attestations API returns bundle_url on this host. The download is
// https only, sends no Authorization header, refuses redirects, aborts after
// BUNDLE_TIMEOUT_MS, and stops after MAX_BUNDLE_BYTES. redirect: 'error'
// means a 3xx cannot leave this host.
export const BUNDLE_HOSTS = ['tmaproduction.blob.core.windows.net'];

export function assertBundleUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`bundle_url is not a URL. found ${value}`);
  }
  if (url.protocol !== 'https:') {
    throw new Error(`bundle_url must be https. found ${url.protocol.replace(':', '')}`);
  }
  if (!BUNDLE_HOSTS.includes(url.hostname)) {
    throw new Error(
      `bundle_url host is not allowed. expected ${BUNDLE_HOSTS.join(' or ')}, found ${url.hostname}`
    );
  }
  return url;
}

async function readBoundedBody(response, limit = MAX_BUNDLE_BYTES) {
  const header = response.headers?.get?.('content-length');
  if (header != null && header !== '') {
    if (!/^\d{1,10}$/.test(header) || Number(header) > limit) {
      throw new Error(`bundle_url content-length ${header} exceeds ${limit} bytes`);
    }
  }
  if (!response.body || typeof response.body.getReader !== 'function') {
    throw new Error('bundle_url body is not a stream');
  }
  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const size = value?.byteLength ?? value?.length ?? 0;
      total += size;
      if (total > limit) {
        await reader.cancel();
        throw new Error(`bundle_url exceeded ${limit} bytes`);
      }
      chunks.push(Buffer.from(value));
    }
  } finally {
    reader.releaseLock?.();
  }
  return Buffer.concat(chunks, total);
}

const SHA = /^[0-9a-f]{40}$/;
const DOWNLOAD_LIMIT = 128 * 1024 * 1024;

/** A full SHA in target_commitish, or null. Release-note changelog SHAs are not commits. */
export function publishedCommitFromRelease(release) {
  const target = typeof release?.target_commitish === 'string' ? release.target_commitish.trim() : '';
  return SHA.test(target) ? target : null;
}

export function parseSha256Sums(text) {
  const sums = {};
  for (const line of String(text).split(/\r?\n/)) {
    if (!line) continue;
    const match = line.match(/^([0-9a-f]{64})  (.+)$/);
    if (!match) continue;
    sums[match[2]] = match[1];
  }
  return sums;
}

export function imageDigestFromBody(body, image) {
  if (typeof body !== 'string' || !image) return null;
  const escaped = image.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const withVersion = new RegExp(`${escaped}:[^\\s@]*@sha256:([0-9a-f]{64})`, 'i');
  const bare = new RegExp(`${escaped}@sha256:([0-9a-f]{64})`, 'i');
  const match = body.match(withVersion) || body.match(bare);
  return match ? `sha256:${match[1].toLowerCase()}` : null;
}

export const SLSA_PROVENANCE_V1 = 'https://slsa.dev/provenance/v1';

function statementRecord(statement) {
  const subjects = [];
  for (const subject of statement?.subject ?? []) {
    const hex = subject?.digest?.sha256;
    if (typeof hex === 'string' && /^[0-9a-f]{64}$/i.test(hex)) subjects.push(`sha256:${hex.toLowerCase()}`);
  }
  const workflow = statement?.predicate?.buildDefinition?.externalParameters?.workflow ?? {};
  return {
    predicateType: typeof statement?.predicateType === 'string' ? statement.predicateType : '',
    subjects,
    repository: normaliseRepository(workflow.repository),
    workflowPath: typeof workflow.path === 'string' ? workflow.path : '',
    gitCommit: findGitCommit(statement),
  };
}

function bundleStatement(bundle) {
  const encoded = bundle?.dsseEnvelope?.payload;
  if (typeof encoded !== 'string' || !encoded) return null;
  try {
    return statementRecord(JSON.parse(Buffer.from(encoded, 'base64').toString('utf8')));
  } catch {
    return null;
  }
}

function causedMessage(error) {
  const message = error?.message || String(error);
  const cause = error?.cause?.message;
  if (typeof cause === 'string' && cause && !message.includes(cause)) return `${message}: ${cause}`;
  return message;
}

async function fetchBundle(url, fetchImpl, timeoutMs = BUNDLE_TIMEOUT_MS) {
  assertBundleUrl(url);
  let response;
  try {
    response = await fetchImpl(url, { redirect: 'error', signal: AbortSignal.timeout(timeoutMs) });
  } catch (error) {
    const detail = causedMessage(error);
    if (error?.name === 'TimeoutError' || error?.name === 'AbortError' || /timeout|aborted/i.test(detail)) {
      throw new Error(`bundle_url timed out. ${url} exceeded ${timeoutMs} ms. ${detail}`);
    }
    if (/redirect/i.test(detail)) {
      throw new Error(`bundle_url redirected. ${url} refused a redirect. ${detail}`);
    }
    throw new Error(`bundle_url could not be fetched. ${url} failed. ${detail}`);
  }
  if (response.status >= 300 && response.status < 400) {
    const location = response.headers?.get?.('location');
    const target = location ? ` to ${location}` : '';
    throw new Error(`bundle_url redirected. ${url} returned HTTP ${response.status}${target}.`);
  }
  if (!response.ok) {
    throw new Error(`bundle_url returned HTTP ${response.status}`);
  }
  return readBoundedBody(response);
}

async function entryStatement(entry, fetchImpl, timeoutMs) {
  let bundle = entry?.bundle;
  if (!bundle && typeof entry?.bundle_url === 'string' && entry.bundle_url) {
    const bytes = await fetchBundle(entry.bundle_url, fetchImpl, timeoutMs);
    bundle = JSON.parse(decodeMaybeSnappy(bytes).toString('utf8'));
  }
  return bundle ? bundleStatement(bundle) : null;
}

export async function readAttestationEntries(payload, digest, fetchImpl = globalThis.fetch) {
  const list = Array.isArray(payload?.attestations) ? payload.attestations : [];
  const records = [];
  for (const entry of list) {
    const record = await entryStatement(entry, fetchImpl);
    if (record) records.push(record);
  }
  return selectProvenance(records, digest);
}

function rejectionReasons(record, digest, repository) {
  const reasons = [];
  if (record.predicateType !== SLSA_PROVENANCE_V1) {
    reasons.push(`predicate ${record.predicateType || 'none'}`);
  }
  if (!record.subjects.includes(digest)) {
    reasons.push(`subject ${record.subjects.join(' ') || 'none'}`);
  }
  if (repository && !sameRepository(record.repository, repository)) {
    reasons.push(`repository ${record.repository || 'none'}`);
  }
  return reasons;
}

export function selectProvenance(records, digest, repository = '') {
  const wanted = typeof digest === 'string' ? digest.toLowerCase() : '';
  const reasons = [];
  let match = null;
  records.forEach((record, index) => {
    if (match) return;
    const rejected = rejectionReasons(record, wanted, repository);
    if (rejected.length === 0) {
      match = record;
      return;
    }
    reasons.push(`entry ${index}: ${rejected.join(', ')}`);
  });
  if (!match) {
    const slsa = records.filter((record) => record.predicateType === SLSA_PROVENANCE_V1);
    const source = repository || 'the source repository';
    return {
      published: records.length > 0,
      match: null,
      reasons,
      error: `attestation did not match. expected subject ${wanted} from ${source}. ${reasons.join('; ')}.`,
      found: slsa.flatMap((record) => record.subjects),
      types: [...new Set(records.map((record) => record.predicateType).filter(Boolean))],
    };
  }
  return {
    published: true,
    match,
    reasons,
    subjectDigest: wanted,
    repository: match.repository,
    gitCommit: match.gitCommit,
    workflowPath: match.workflowPath,
    predicateType: match.predicateType,
    found: match.subjects,
    types: [SLSA_PROVENANCE_V1],
  };
}

function findGitCommit(value) {
  if (!value || typeof value !== 'object') return null;
  if (typeof value.gitCommit === 'string' && SHA.test(value.gitCommit)) return value.gitCommit;
  for (const child of Object.values(value)) {
    const found = findGitCommit(child);
    if (found) return found;
  }
  return null;
}

export function readAttestation(payload, digest) {
  const list = Array.isArray(payload?.attestations) ? payload.attestations : [];
  if (list.length === 0) return { published: false };
  const records = list.map((entry) => (entry?.bundle ? bundleStatement(entry.bundle) : null)).filter(Boolean);
  if (records.length === 0) return { published: true, subjectDigest: null, repository: null, gitCommit: null };
  const selected = selectProvenance(records, digest ?? records.find((record) => record.subjects[0])?.subjects[0]);
  if (!selected.match) {
    return {
      published: true,
      subjectDigest: null,
      repository: null,
      gitCommit: null,
      predicateType: selected.types[0] ?? '',
      workflowPath: '',
    };
  }
  return {
    published: true,
    subjectDigest: selected.subjectDigest,
    repository: selected.repository,
    gitCommit: selected.gitCommit,
    predicateType: selected.predicateType,
    workflowPath: selected.workflowPath,
  };
}

function githubHeaders(token) {
  const headers = {
    Accept: 'application/vnd.github+json',
    'User-Agent': 'livingproof-verification-reports',
    'X-GitHub-Api-Version': '2022-11-28',
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function hashStream(response, label) {
  if (!response.body) {
    const bytes = Buffer.from(await response.arrayBuffer());
    if (bytes.length > DOWNLOAD_LIMIT) {
      throw new ReportError(`${label}: download exceeded ${DOWNLOAD_LIMIT} bytes.`);
    }
    return sha256(bytes);
  }
  const reader = response.body.getReader();
  const hash = createHash('sha256');
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > DOWNLOAD_LIMIT) {
      throw new ReportError(`${label}: download exceeded ${DOWNLOAD_LIMIT} bytes.`);
    }
    hash.update(value);
  }
  return hash.digest('hex');
}

async function getJson(fetchImpl, url, token) {
  const response = await fetchImpl(url, { headers: githubHeaders(token) });
  return { response, body: response.status === 204 ? null : await response.json().catch(() => null) };
}

async function peelTag(pin, api, token, fetchImpl) {
  const refUrl = githubApiUrl(api, ['repos', pin.repository, 'git/ref/tags', pin.tag], {
    label: `${pin.name}: tag ref`,
  });
  let { response, body } = await getJson(fetchImpl, refUrl, token);
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new ReportError(
      `${pin.name}: could not resolve tag ${pin.tag}. expected a git tag, found HTTP ${response.status}.`
    );
  }
  let object = body?.object;
  for (let depth = 0; depth < 3 && object?.type === 'tag'; depth += 1) {
    const tagUrl = githubApiUrl(api, ['repos', pin.repository, 'git/tags', object.sha], {
      label: `${pin.name}: tag object`,
    });
    ({ response, body } = await getJson(fetchImpl, tagUrl, token));
    if (!response.ok) {
      throw new ReportError(
        `${pin.name}: could not peel tag ${pin.tag}. expected a commit, found HTTP ${response.status}.`
      );
    }
    object = body?.object;
  }
  return object?.type === 'commit' && SHA.test(object.sha) ? object.sha : null;
}

async function hashFile(pin, commit, filePath, api, token, fetchImpl) {
  const url = githubApiUrl(api, ['repos', pin.repository, 'contents', filePath], {
    query: { ref: commit },
    label: `${pin.name}: file ${JSON.stringify(filePath)}`,
  });
  const { response, body } = await getJson(fetchImpl, url, token);
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new ReportError(
      `${pin.name}: could not read ${filePath}. expected the file at ${commit}, found HTTP ${response.status}.`
    );
  }
  if (body?.encoding === 'base64' && typeof body.content === 'string' && body.content.length > 0) {
    return sha256(Buffer.from(body.content.replace(/\n/g, ''), 'base64'));
  }
  if (typeof body?.download_url === 'string') {
    const download = await fetchImpl(body.download_url);
    if (!download.ok) {
      throw new ReportError(
        `${pin.name}: could not read ${filePath}. expected the file at ${commit}, found HTTP ${download.status}.`
      );
    }
    return hashStream(download, `${pin.name}: ${filePath}`);
  }
  return null;
}

async function registryDigest(pin, fetchImpl) {
  const name = pin.image.slice('ghcr.io/'.length);
  const version = pin.tag.replace(/^v/, '');
  const tokenResponse = await fetchImpl(
    `https://ghcr.io/token?service=ghcr.io&scope=${encodeURIComponent(`repository:${name}:pull`)}`
  );
  if (!tokenResponse.ok) {
    throw new ReportError(
      `${pin.name}: could not read ${pin.image}:${version}. expected a registry token, found HTTP ${tokenResponse.status}.`
    );
  }
  const tokenBody = await tokenResponse.json();
  const manifestUrl = githubApiUrl('https://ghcr.io/v2', [name, 'manifests', version], {
    label: `${pin.name}: image manifest`,
  });
  const manifest = await fetchImpl(manifestUrl, {
    headers: {
      Authorization: `Bearer ${tokenBody.token}`,
      Accept:
        'application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json',
    },
  });
  if (!manifest.ok) {
    throw new ReportError(
      `${pin.name}: could not read ${pin.image}:${version}. expected an image manifest, found HTTP ${manifest.status}.`
    );
  }
  const bytes = Buffer.from(await manifest.arrayBuffer());
  const hashed = `sha256:${sha256(bytes)}`;
  const header = (manifest.headers.get('docker-content-digest') || '').toLowerCase();
  if (header !== hashed) {
    throw new ReportError(
      `${pin.name}: image digest mismatch for ${pin.image}. expected ${header || 'a docker-content-digest header'}, found ${hashed}.`
    );
  }
  return hashed;
}

async function loadSums(pin, release, token, fetchImpl) {
  const asset = (release.assets ?? []).find((entry) => entry?.name === 'SHA256SUMS');
  if (!asset?.browser_download_url) return {};
  const response = await fetchImpl(asset.browser_download_url);
  if (!response.ok) {
    throw new ReportError(
      `${pin.name}: could not read SHA256SUMS. expected the release asset, found HTTP ${response.status}.`
    );
  }
  return parseSha256Sums(await response.text());
}

async function hashTarball(pin, commit, fetchImpl) {
  const url = githubApiUrl('https://codeload.github.com', [pin.repository, 'tar.gz', commit], {
    label: `${pin.name}: source archive`,
  });
  const response = await fetchImpl(url);
  if (!response.ok) {
    throw new ReportError(
      `${pin.name}: could not read the source archive. expected ${pin.repository}@${pin.tag}, found HTTP ${response.status}.`
    );
  }
  return `sha256:${await hashStream(response, pin.name)}`;
}

export async function openAttestations(list, digest, fetchImpl, repository = '', options = {}) {
  const timeoutMs = options.timeoutMs ?? BUNDLE_TIMEOUT_MS;
  const records = [];
  for (const entry of list) {
    const record = await entryStatement(entry, fetchImpl, timeoutMs);
    if (record) records.push(record);
  }
  return selectProvenance(records, digest, repository);
}

async function loadAttestation(pin, digest, api, token, fetchImpl) {
  const url = githubApiUrl(api, ['repos', pin.repository, 'attestations', digest], {
    label: `${pin.name}: attestation`,
  });
  const { response, body } = await getJson(fetchImpl, url, token);
  if (response.status === 404) return { published: false, status: 404 };
  if (response.status !== 200) {
    throw new ReportError(
      `${pin.name}: attestation request failed. expected HTTP 200 or HTTP 404, found HTTP ${response.status}.`
    );
  }
  const list = Array.isArray(body?.attestations) ? body.attestations : [];
  if (list.length === 0) {
    throw new ReportError(
      `${pin.name}: attestation request failed. expected HTTP 200 with an attestation or HTTP 404, found HTTP 200 with no attestation.`
    );
  }
  let selected;
  try {
    selected = await openAttestations(list, digest, fetchImpl, pin.repository);
  } catch (error) {
    throw new ReportError(`${pin.name}: attestation bundle could not be read. ${error.message}`);
  }
  if (!selected.match) {
    throw new ReportError(`${pin.name}: ${selected.error}`);
  }
  return {
    published: true,
    status: 200,
    subjectDigest: selected.subjectDigest,
    repository: normaliseRepository(selected.repository),
    gitCommit: selected.gitCommit,
    workflowPath: selected.workflowPath,
    predicateType: selected.predicateType,
  };
}

export async function resolvePin(pin, { token = '', api = 'https://api.github.com', fetch: fetchImpl }) {
  const releaseUrl = githubApiUrl(api, ['repos', pin.repository, 'releases/tags', pin.tag], {
    label: `${pin.name}: release`,
  });
  const { response, body: release } = await getJson(fetchImpl, releaseUrl, token);
  if (response.status === 404) {
    return {
      releaseFound: false,
      tagCommit: null,
      publishedCommit: null,
      files: {},
      imageDigest: null,
      publishedDigest: null,
      sourceDigest: null,
      attestation: { published: false, status: 404 },
    };
  }
  if (!response.ok) {
    throw new ReportError(
      `${pin.name}: could not read the release. expected a GitHub Release for ${pin.repository}@${pin.tag}, found HTTP ${response.status}.`
    );
  }
  const tagCommit = await peelTag(pin, api, token, fetchImpl);
  const sums = await loadSums(pin, release, token, fetchImpl);
  const files = {};
  for (const filePath of pin.paths ?? []) {
    files[filePath] = {
      published: sums[filePath] ?? null,
      found: tagCommit ? await hashFile(pin, tagCommit, filePath, api, token, fetchImpl) : null,
    };
  }
  let imageDigest = null;
  let publishedDigest = null;
  let sourceDigest = null;
  if (pin.kind === 'image') {
    publishedDigest = imageDigestFromBody(release.body, pin.image);
    imageDigest = await registryDigest(pin, fetchImpl);
  } else if ((pin.paths ?? []).length === 0 && tagCommit) {
    sourceDigest = await hashTarball(pin, tagCommit, fetchImpl);
    const publishedSource = sums['source.tar.gz'] ?? sums.SOURCE ?? null;
    if (publishedSource) publishedDigest = `sha256:${publishedSource}`;
  }
  const subject = subjectDigest(pin, { files, imageDigest, sourceDigest });
  const attestation = subject
    ? await loadAttestation(pin, subject, api, token, fetchImpl)
    : { published: false };
  return {
    releaseFound: true,
    tagCommit,
    publishedCommit: publishedCommitFromRelease(release),
    files,
    imageDigest,
    publishedDigest,
    sourceDigest,
    attestation,
  };
}

export function githubProvenanceClient(env = process.env, fetchImpl = globalThis.fetch) {
  const token = env.GITHUB_TOKEN || env.GH_TOKEN || '';
  const api = (env.GITHUB_API_URL || 'https://api.github.com').replace(/\/$/, '');
  return {
    async resolve(pin) {
      try {
        return await resolvePin(pin, { token, api, fetch: fetchImpl });
      } catch (error) {
        if (error instanceof ReportError) throw error;
        throw new ReportError(`${pin.name}: ${error.message}`);
      }
    },
  };
}
