# SPDX-License-Identifier: Apache-2.0
import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("reset,expected", [("true", 1), ("false", 0)])
def test_retained_history_reset_guard_runs_before_restoration(reset, expected):
    text = Path(".github/workflows/docs.yml").read_text()
    start = text.index("      - name: Validate retained mode")
    end = text.index("      - name: Choose the Verify run", start)
    step = text[start:end]
    script = step.split("        run: |\n", 1)[1]
    result = subprocess.run(
        ["bash", "-e", "-c", script],
        env=dict(os.environ, HISTORY="config/retained/history.json", RESET=reset),
    )
    assert result.returncode == expected
    assert start < text.index("      - name: Restore published report history")


def test_workflow_retains_release_gates_and_only_proven_history_mode():
    text = Path(".github/workflows/docs.yml").read_text()
    assert text.count("- name: Require published release at current protected main") == 2
    assert text.count('test "$GITHUB_REF_PROTECTED" = true') == 2
    assert "- name: Choose the Verify run\n        if: inputs.retained_history == ''" in text
    assert (
        "- name: Download the real G1 smoke report\n        if: inputs.retained_history == ''"
        in text
    )
    assert "retained_source" not in text
    assert not Path("tools/retained_report.py").exists()
    assert "gh workflow run" not in text
    assert "uses: ./.github/workflows/report.yml" not in text
