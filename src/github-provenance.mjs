// SPDX-License-Identifier: Apache-2.0
import { createHash } from 'node:crypto';
import { ReportError, sha256 } from './definitions.mjs';
import { subjectDigest } from './provenance.mjs';

const SHA = /^[0-9a-f]{40}$/;
const DOWNLOAD_LIMIT = 128 * 1024 * 1024;

export function publishedCommitFromRelease(release) {
  const target = typeof release?.target_commitish === 'string' ? release.target_commitish : '';
  if (SHA.test(target)) return target;
  const body = typeof release?.body === 'string' ? release.body : '';
  const found = [...body.matchAll(/(?<![0-9a-f])([0-9a-f]{40})(?![0-9a-f])/g)].map((match) => match[1]);
  return found.length > 0 ? found[found.length - 1] : null;
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
  const index = body.indexOf(`${image}:`);
  if (index === -1) return null;
  const slice = body.slice(index, index + image.length + 90);
  const match = slice.match(/@sha256:([0-9a-f]{64})/);
  return match ? `sha256:${match[1]}` : null;
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

export function readAttestation(payload) {
  const list = Array.isArray(payload?.attestations) ? payload.attestations : [];
  if (list.length === 0) return { published: false };
  for (const entry of list) {
    const encoded = entry?.bundle?.dsseEnvelope?.payload;
    if (typeof encoded !== 'string' || !encoded) continue;
    let statement;
    try {
      statement = JSON.parse(Buffer.from(encoded, 'base64').toString('utf8'));
    } catch {
      continue;
    }
    const hex = statement?.subject?.[0]?.digest?.sha256;
    const subjectDigest = typeof hex === 'string' && /^[0-9a-f]{64}$/.test(hex) ? `sha256:${hex}` : null;
    const repository = statement?.predicate?.buildDefinition?.externalParameters?.workflow?.repository;
    return {
      published: true,
      subjectDigest,
      repository: typeof repository === 'string' ? repository : null,
      gitCommit: findGitCommit(statement),
    };
  }
  return { published: true, subjectDigest: null, repository: null, gitCommit: null };
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
  const refUrl = `${api}/repos/${pin.repository}/git/ref/tags/${encodeURIComponent(pin.tag)}`;
  let { response, body } = await getJson(fetchImpl, refUrl, token);
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new ReportError(
      `${pin.name}: could not resolve tag ${pin.tag}. expected a git tag, found HTTP ${response.status}.`
    );
  }
  let object = body?.object;
  for (let depth = 0; depth < 3 && object?.type === 'tag'; depth += 1) {
    const tagUrl = `${api}/repos/${pin.repository}/git/tags/${object.sha}`;
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
  const encoded = filePath
    .split('/')
    .map((segment) => encodeURIComponent(segment))
    .join('/');
  const url = `${api}/repos/${pin.repository}/contents/${encoded}?ref=${commit}`;
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
  const manifest = await fetchImpl(`https://ghcr.io/v2/${name}/manifests/${version}`, {
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
  await manifest.arrayBuffer().catch(() => null);
  return manifest.headers.get('docker-content-digest');
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

async function hashTarball(pin, fetchImpl) {
  const url = `https://codeload.github.com/${pin.repository}/tar.gz/refs/tags/${encodeURIComponent(pin.tag)}`;
  const response = await fetchImpl(url);
  if (!response.ok) {
    throw new ReportError(
      `${pin.name}: could not read the source archive. expected ${pin.repository}@${pin.tag}, found HTTP ${response.status}.`
    );
  }
  return `sha256:${await hashStream(response, pin.name)}`;
}

async function loadAttestation(pin, digest, api, token, fetchImpl) {
  const url = `${api}/repos/${pin.repository}/attestations/${digest}`;
  const { response, body } = await getJson(fetchImpl, url, token);
  if (response.status === 404) return { published: false, status: 404 };
  if (response.status !== 200) {
    throw new ReportError(
      `${pin.name}: attestation request failed. expected HTTP 200 or HTTP 404, found HTTP ${response.status}.`
    );
  }
  const attestation = readAttestation(body);
  if (attestation.published !== true) {
    throw new ReportError(
      `${pin.name}: attestation request failed. expected HTTP 200 with an attestation or HTTP 404, found HTTP 200 with no attestation.`
    );
  }
  return { ...attestation, status: 200 };
}

export async function resolvePin(pin, { token = '', api = 'https://api.github.com', fetch: fetchImpl }) {
  const releaseUrl = `${api}/repos/${pin.repository}/releases/tags/${encodeURIComponent(pin.tag)}`;
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
  } else if ((pin.paths ?? []).length === 0) {
    sourceDigest = await hashTarball(pin, fetchImpl);
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
