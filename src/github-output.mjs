// SPDX-License-Identifier: Apache-2.0
import { appendFileSync } from 'node:fs';

export function writeGithubOutput(env, pairs) {
  const file = env.GITHUB_OUTPUT;
  if (!file) return;
  const lines = [];
  for (const [key, value] of Object.entries(pairs)) {
    const text = String(value);
    if (text.includes('\n')) {
      throw new Error(`output ${key} contains a newline`);
    }
    lines.push(`${key}=${text}`);
  }
  appendFileSync(file, `${lines.join('\n')}\n`, 'utf8');
}
