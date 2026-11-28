# SPDX-License-Identifier: Apache-2.0
import copy
import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from retained_report import REPOSITORY, extract, validate


def sample():
    pin = dict(run=123, artifact=456, commit="a" * 40, sha256="b" * 64)
    run = dict(
        id=123,
        head_sha=pin["commit"],
        event="push",
        head_branch="main",
        status="completed",
        conclusion="success",
        workflow_id=7,
        head_repository={"full_name": REPOSITORY},
    )
    artifact = dict(
        id=456,
        expired=False,
        name="g1-verification-report",
        workflow_run={"id": 123},
        digest="sha256:" + pin["sha256"],
    )
    return pin, run, artifact


def test_exact_source_validation():
    pin, run, artifact = sample()
    validate(pin, run, artifact, {"id": 7})
    for key, value in [
        ("event", "pull_request"),
        ("conclusion", "failure"),
        ("head_sha", "c" * 40),
        ("workflow_id", 8),
        ("head_branch", "other"),
    ]:
        altered = copy.deepcopy(run)
        altered[key] = value
        with pytest.raises(ValueError):
            validate(pin, altered, artifact, {"id": 7})
    for key, value in [
        ("id", 999),
        ("expired", True),
        ("digest", "sha256:" + "0" * 64),
        ("workflow_run", {"id": 999}),
    ]:
        altered = copy.deepcopy(artifact)
        altered[key] = value
        with pytest.raises(ValueError):
            validate(pin, run, altered, {"id": 7})


@pytest.mark.parametrize("name", ["../escape", "/absolute", "bad\\file"])
def test_archive_paths_refused(tmp_path, name):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, b"x")
    raw = buffer.getvalue()
    with pytest.raises(ValueError):
        extract(raw, {"sha256": hashlib.sha256(raw).hexdigest()}, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_workflow_retains_release_gates_and_never_dispatches_simulation():
    text = Path(".github/workflows/docs.yml").read_text()
    assert text.count("- name: Require published release at current protected main") == 2
    assert text.count('test "$GITHUB_REF_PROTECTED" = true') == 2
    assert "- name: Choose the Verify run\n        if: inputs.retained_source == ''" in text
    assert (
        "- name: Download the real G1 smoke report\n        if: inputs.retained_source == ''"
        in text
    )
    assert (
        "- name: Download pinned retained Verify report\n        if: inputs.retained_source != ''"
        in text
    )
    assert 'run: python3 tools/retained_report.py "$RETAINED_SOURCE"' in text
    assert "gh workflow run" not in text
    assert "uses: ./.github/workflows/report.yml" not in text
    assert "workflow_dispatch" not in Path("tools/retained_report.py").read_text()
