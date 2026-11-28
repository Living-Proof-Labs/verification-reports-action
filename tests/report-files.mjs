// SPDX-License-Identifier: Apache-2.0
// Test helper: the minimal report files a collect or provenance test feeds the action.
// The action itself renders no report (#75); these stand in for capability-report output.
export function reportFiles(summary) {
  const line = `${summary.capability}: overall ${summary.grade}.`;
  const document = {
    report_version: '1.0.0',
    summaries: [summary],
  };
  return {
    'report.json': `${JSON.stringify(document, null, 2)}\n`,
    'report.md': `# Verification report\n\n${line}\n`,
    'report.txt': `${line}\n`,
    'report.html': `<!DOCTYPE html>\n<html><body><p>${line}</p></body></html>\n`,
  };
}
