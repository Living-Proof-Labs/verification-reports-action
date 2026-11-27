// SPDX-License-Identifier: Apache-2.0
import { randomBytes } from 'node:crypto';
import { appendFileSync } from 'node:fs';

export function writeGithubOutput(env, pairs) {
  const file = env.GITHUB_OUTPUT;
  if (!file) return;
  const lines = [];
  for (const [key, value] of Object.entries(pairs)) {
    const text = String(value);
    if (!text.includes('\n')) {
      lines.push(`${key}=${text}`);
      continue;
    }
    let delimiter = `ghadelim_${randomBytes(8).toString('hex')}`;
    while (text.includes(delimiter)) delimiter = `ghadelim_${randomBytes(8).toString('hex')}`;
    lines.push(`${key}<<${delimiter}`);
    lines.push(text.endsWith('\n') ? text.slice(0, -1) : text);
    lines.push(delimiter);
  }
  appendFileSync(file, `${lines.join('\n')}\n`, 'utf8');
}
