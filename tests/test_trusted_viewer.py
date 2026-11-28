# SPDX-License-Identifier: Apache-2.0
import copy
import hashlib
import json

import pytest

from reports_common import SiteError
from reports_render import checked_report_html
from trusted_viewer import install, revalidate


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def case(tmp_path):
    data = b'{"trials":[]}'
    viewer = b"<!doctype html><script>/* reviewed caller code */</script>"
    advisory = {
        "evaluator": {"id": "generic-example"},
        "advisory": {
            "details_file": {"uri": "recording.json", "sha256": sha(data)},
            "trials": [{"index": 1, "observation_sha256": "a" * 64}],
        },
    }
    report = {
        "source_bundle": {
            "results": [
                {
                    "id": "trial-one",
                    "system": {"id": "robot/stock"},
                    "provenance": {"run_id": "recorded-run"},
                    "evidence": [{"sha256": "a" * 64}],
                }
            ]
        },
        "advisories": [advisory],
        "grade": "FAIL",
        "assessments": [{"evaluator": "named-check"}],
    }
    raw = json.dumps(report).encode()
    (tmp_path / "report.json").write_bytes(raw)
    (tmp_path / "recording.json").write_bytes(data)
    (tmp_path / "index.html").write_text(
        "<html><body><p>FAIL</p><a href='recording.json'>Recorded data</a></body></html>"
    )
    pin = {
        "viewer_sha256": sha(viewer),
        "report_sha256": sha(raw),
        "viewer_commit": "b" * 40,
        "viewer_tag": "v1.0.0",
        "result_id": "trial-one",
        "run": "recorded-run",
        "system": "robot/stock",
        "configuration": "stock",
        "trial": 1,
        "evaluator_id": "generic-example",
    }
    return tmp_path, pin, viewer


def test_install_restore_preserves_report_data_and_grade(case):
    folder, pin, viewer = case
    original = (folder / "index.html").read_text()
    raw = (folder / "report.json").read_bytes()
    data = (folder / "recording.json").read_bytes()
    install(folder, pin, viewer)
    assert revalidate(folder, pin) == original
    assert (folder / "report.json").read_bytes() == raw
    assert (folder / "recording.json").read_bytes() == data
    assert "Recorded data" in (folder / "index.html").read_text()
    with pytest.raises(SiteError):
        checked_report_html((folder / "index.html").read_text(), "untrusted input")


@pytest.mark.parametrize(
    "key,value",
    [
        ("viewer_sha256", "0" * 64),
        ("report_sha256", "0" * 64),
        ("trial", 2),
        ("run", "other"),
        ("configuration", "other"),
    ],
)
def test_invalid_pin_refused(case, key, value):
    folder, pin, viewer = case
    altered = copy.deepcopy(pin)
    altered[key] = value
    with pytest.raises(SiteError):
        install(folder, altered, viewer)
    assert not (folder / "trusted-viewer").exists()


@pytest.mark.parametrize("target", ["viewer.html", "data/recording.json", "sanitized-source.txt"])
def test_restored_tampering_refused(case, target):
    folder, pin, viewer = case
    install(folder, pin, viewer)
    (folder / "trusted-viewer" / target).write_text("tampered")
    with pytest.raises(SiteError):
        revalidate(folder, pin)


def test_incoming_iframe_never_accepted(case):
    folder, pin, viewer = case
    (folder / "index.html").write_text('<iframe src="evil.html"></iframe>')
    with pytest.raises(SiteError):
        install(folder, pin, viewer)


def test_wrong_report_cited_bytes_refused(case):
    folder, pin, viewer = case
    (folder / "recording.json").write_text("{}")
    with pytest.raises(SiteError):
        install(folder, pin, viewer)


def test_extra_executable_and_changed_iframe_refused(case):
    folder, pin, viewer = case
    install(folder, pin, viewer)
    extra = folder / "trusted-viewer/extra.js"
    extra.write_text("alert(1)")
    with pytest.raises(SiteError):
        revalidate(folder, pin)
    extra.unlink()
    p = folder / "index.html"
    p.write_text(
        p.read_text().replace(
            "allow-scripts allow-same-origin", "allow-scripts allow-top-navigation"
        )
    )
    with pytest.raises(SiteError):
        revalidate(folder, pin)


def test_production_restore_and_reapply(case, tmp_path):
    import shutil

    from trusted_viewer import apply_declarations, prepare_history

    root = tmp_path / "site"
    relative = "robot/hallway/runs/123"
    target = root / relative
    target.mkdir(parents=True)
    folder, pin, viewer = case
    for name in ("report.json", "index.html", "recording.json"):
        shutil.copyfile(folder / name, target / name)
    original = (target / "index.html").read_bytes()
    declarations = {relative: (pin, viewer)}
    apply_declarations(root, declarations)
    published = (target / "index.html").read_bytes()
    sums = (target / "SHA256SUMS").read_bytes()
    with pytest.raises(SiteError, match="no current caller"):
        prepare_history(root, {})
    prepare_history(root, declarations)
    assert (target / "index.html").read_bytes() == original
    assert not (target / "trusted-viewer").exists()
    apply_declarations(root, declarations)
    assert (target / "index.html").read_bytes() == published
    assert (target / "SHA256SUMS").read_bytes() == sums


def test_no_declaration_no_change(case):
    from trusted_viewer import apply_declarations, prepare_history

    folder, _, _ = case
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    prepare_history(folder, {})
    apply_declarations(folder, {})
    assert before == {p.name: p.read_bytes() for p in folder.iterdir()}


def test_declared_asset_and_paths_are_caller_local(case):
    from trusted_viewer import load_declarations

    folder, pin, viewer = case
    (folder / "viewer.html").write_bytes(viewer)
    entry = {"path": "robot/hallway/runs/123", "pin": pin, "viewer_file": "viewer.html"}
    registry = folder / "registry.json"
    registry.write_text(json.dumps([entry]))
    assert len(load_declarations(registry)) == 1
    for key, value in [("path", "../escape/runs/123"), ("viewer_file", "../viewer.html")]:
        altered = dict(entry, **{key: value})
        registry.write_text(json.dumps([altered]))
        with pytest.raises(SiteError):
            load_declarations(registry)


def test_published_migration_requires_exact_manifest_and_bytes(case):
    import shutil

    from migrate_retained_viewer import migrate
    from reports_site import write_published_checksums

    folder, pin, viewer = case
    root = folder / "history"
    relative = "robot/hallway/runs/123"
    target = root / relative
    target.mkdir(parents=True)
    for name in ("report.json", "index.html", "recording.json"):
        shutil.copyfile(folder / name, target / name)
    (folder / "viewer.html").write_bytes(viewer)
    registry = folder / "registry.json"
    registry.write_text(json.dumps([{"path": relative, "pin": pin, "viewer_file": "viewer.html"}]))
    write_published_checksums(target)
    expected = sha((target / "SHA256SUMS").read_bytes())
    with pytest.raises(SiteError, match="manifest differs"):
        migrate(root, registry, {relative: "0" * 64})
    (target / "recording.json").write_text("tampered")
    with pytest.raises(SiteError, match="file digest"):
        migrate(root, registry, {relative: expected})
    (target / "recording.json").write_bytes((folder / "recording.json").read_bytes())
    # A second historical run is outside this presentation migration's scope.
    other = root / "robot/hallway/runs/122"
    shutil.copytree(target, other)
    untouched = {p.relative_to(other): p.read_bytes() for p in other.rglob("*") if p.is_file()}
    original_report = (target / "report.json").read_bytes()
    original_data = (target / "recording.json").read_bytes()
    migrate(root, registry, {relative: expected})
    assert "iframe" in (target / "index.html").read_text()
    from trusted_viewer import apply_declarations, load_declarations, prepare_history

    declarations = load_declarations(registry)
    for _ in range(2):
        prepare_history(root, declarations)
        apply_declarations(root, declarations)
    assert (target / "report.json").read_bytes() == original_report
    assert (target / "recording.json").read_bytes() == original_data
    assert untouched == {
        p.relative_to(other): p.read_bytes() for p in other.rglob("*") if p.is_file()
    }


@pytest.mark.parametrize("change", ["modified", "extra", "missing"])
def test_restored_unrelated_evidence_refused_without_writes(case, change):
    import shutil

    from reports_site import write_published_checksums
    from trusted_viewer import apply_declarations, prepare_history

    folder, pin, viewer = case
    root = folder / "history"
    relative = "robot/hallway/runs/123"
    target = root / relative
    target.mkdir(parents=True)
    for name in ("report.json", "index.html", "recording.json"):
        shutil.copyfile(folder / name, target / name)
    (target / "evidence").mkdir()
    evidence = target / "evidence/unrelated.json"
    evidence.write_bytes(b'{"original":true}')
    write_published_checksums(target)
    declarations = {relative: (pin, viewer)}
    apply_declarations(root, declarations)
    original_manifest = (target / "SHA256SUMS").read_bytes()
    if change == "modified":
        evidence.write_bytes(b'{"modified":true}')
    elif change == "extra":
        (target / "evidence/extra.json").write_bytes(b"{}")
    else:
        evidence.unlink()
    before = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    with pytest.raises(SiteError):
        prepare_history(root, declarations)
    assert (target / "SHA256SUMS").read_bytes() == original_manifest
    assert before == {
        p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()
    }
