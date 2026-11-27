// SPDX-License-Identifier: Apache-2.0
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

/**
 * Passed when evaluators.yml has no declaration lines. It is a comment, so it
 * is not a line the loader would run, and it is not empty, so capability-verifier
 * keeps its built-in run-integrity job skipped. The collect job does not call
 * the loader for this value.
 */
export const EVALUATORS_SENTINEL = '#';

/** YAML list items. Blank lines and comments are not items. The item text is not checked. */
export function parseEvaluatorList(text) {
  const items = [];
  for (const raw of String(text).split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    if (line === '[]' || line === '---' || line === '-') continue;
    if (!line.startsWith('- ')) {
      throw new Error(
        `evaluators.yml must be a YAML list of lines. expected "- <line>", found ${JSON.stringify(raw)}.`
      );
    }
    let item = line.slice(2).trim();
    if (
      item.length >= 2 &&
      ((item.startsWith('"') && item.endsWith('"')) || (item.startsWith("'") && item.endsWith("'")))
    ) {
      item = item.slice(1, -1);
    }
    if (!item) continue;
    items.push(item);
  }
  return items;
}

export function evaluatorsDeclaration(text) {
  const lines = parseEvaluatorList(text);
  if (lines.length === 0) return { lines, input: EVALUATORS_SENTINEL, run: false };
  return { lines, input: lines.join('\n'), run: true };
}

export function readEvaluatorsFile(file) {
  let text = '';
  try {
    text = readFileSync(file, 'utf8');
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
  }
  return evaluatorsDeclaration(text);
}

function asYamlList(text) {
  const substantive = String(text)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#'));
  if (substantive.length === 0) return '';
  if (substantive.some((line) => line.startsWith('- '))) return text;
  return `${substantive.map((line) => `- ${line}`).join('\n')}\n`;
}

/**
 * Caller `evaluators` input. Empty means no evaluators. A single relative path
 * is a file in the caller repository. Anything else is the list itself.
 * This repository's evaluators.yml is not a fallback.
 */
export function resolveCallerEvaluators(value, root) {
  const text = typeof value === 'string' ? value : '';
  if (!text.trim() || text.trim() === '[]') return evaluatorsDeclaration('');
  const single = !/[\r\n]/.test(text);
  const trimmed = text.trim();
  if (single && !trimmed.includes('@') && !trimmed.startsWith('-')) {
    if (path.isAbsolute(trimmed) || trimmed.split(/[/\\]/).includes('..') || trimmed.includes('\\')) {
      throw new Error(
        `evaluators path ${JSON.stringify(trimmed)} must be a relative path in the caller repository.`
      );
    }
    const file = path.resolve(root, trimmed);
    const base = path.resolve(root);
    if (file !== base && !file.startsWith(`${base}${path.sep}`)) {
      throw new Error(
        `evaluators path ${JSON.stringify(trimmed)} must stay inside the caller repository.`
      );
    }
    if (!existsSync(file)) {
      throw new Error(`evaluators file ${trimmed} was not found in the caller repository.`);
    }
    return readEvaluatorsFile(file);
  }
  return evaluatorsDeclaration(asYamlList(text));
}
