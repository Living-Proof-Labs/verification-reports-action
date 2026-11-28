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
    data = json.dumps(
        {
            "format": "example-recording/1",
            "trials": [
                {
                    "format": "example-recording/1",
                    "trial": {
                        "run_id": "recorded-run",
                        "configuration": "stock",
                        "index": 1,
                        "observation_sha256": "a" * 64,
                    },
                }
            ],
        }
    ).encode()
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
        "recording_format": "example-recording/1",
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
    from reports_site import write_published_checksums

    write_published_checksums(target)
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


@pytest.mark.parametrize(
    "mutation", ["valid", "absent", "identity", "ambiguous", "trial", "digest"]
)
def test_dynamic_report_binding(case, mutation, monkeypatch):
    import shutil

    import viewer_release
    from trusted_viewer import (
        apply_declarations,
        derive_declarations,
        load_viewers,
        prepare_history,
    )

    folder, pin, viewer = case
    root = folder / "reports"
    target = root / "robot/hallway/runs/new-run"
    target.mkdir(parents=True)
    for name in ("report.json", "index.html", "recording.json"):
        shutil.copyfile(folder / name, target / name)
    report = json.loads((target / "report.json").read_bytes())
    identity = {
        "id": "generic-example",
        "version": "1.0.0",
        "publisher": "Example",
        "kind": "advisory",
    }
    report["advisories"][0]["evaluator"] = dict(identity)
    if mutation == "absent":
        report["advisories"] = []
    if mutation == "identity":
        report["advisories"][0]["evaluator"]["publisher"] = "Other"
    if mutation == "ambiguous":
        report["source_bundle"]["results"].append(report["source_bundle"]["results"][0])
    if mutation == "trial":
        report["advisories"][0]["advisory"]["trials"] *= 2
    if mutation == "digest":
        (target / "recording.json").write_text("tampered")
    (target / "report.json").write_text(json.dumps(report))
    (folder / "viewer.html").write_bytes(viewer)
    spec = {k: pin[k] for k in ("viewer_sha256", "viewer_tag", "viewer_commit", "recording_format")}
    spec.update(evaluator=identity, viewer_file="viewer.html")
    registry = folder / "viewers.json"
    registry.write_text(json.dumps([spec]))
    monkeypatch.setattr(viewer_release, "fetch_release_viewer", lambda _: viewer)
    viewers = load_viewers(registry)
    if mutation in ("identity", "ambiguous", "trial", "digest"):
        with pytest.raises(SiteError):
            derive_declarations(root, viewers)
        assert not (target / "trusted-viewer").exists()
        return
    declarations = derive_declarations(root, viewers)
    assert bool(declarations) == (mutation == "valid")
    before = (target / "report.json").read_bytes()
    from reports_site import write_published_checksums

    write_published_checksums(target)
    apply_declarations(root, declarations)
    for _ in range(2):
        prepare_history(root, derive_declarations(root, viewers))
        apply_declarations(root, derive_declarations(root, viewers))
    assert (target / "report.json").read_bytes() == before
    assert ("iframe" in (target / "index.html").read_text()) == (mutation == "valid")


@pytest.mark.parametrize(
    "change",
    ["format", "empty", "duplicate", "run_id", "configuration", "index", "observation_sha256"],
)
@pytest.mark.parametrize("restored", [False, True])
def test_hash_matching_recording_requires_exact_trial(case, change, restored):
    folder, pin, viewer = case
    if restored:
        install(folder, pin, viewer)
    data = json.loads((folder / "recording.json").read_bytes())
    if change == "format":
        data["format"] = "other/1"
    elif change == "empty":
        data["trials"] = []
    elif change == "duplicate":
        data["trials"] *= 2
    else:
        data["trials"][0]["trial"][change] = 2 if change == "index" else "different"
    raw = json.dumps(data).encode()
    (folder / "recording.json").write_bytes(raw)
    report = json.loads((folder / "report.json").read_bytes())
    report["advisories"][0]["advisory"]["details_file"]["sha256"] = sha(raw)
    report_raw = json.dumps(report).encode()
    (folder / "report.json").write_bytes(report_raw)
    pin["report_sha256"] = sha(report_raw)
    before = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    with pytest.raises(SiteError, match="Recording"):
        if restored:
            revalidate(folder, pin)
        else:
            install(folder, pin, viewer)
    assert before == {
        p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()
    }


def test_first_decoration_refuses_modified_unrelated_evidence(case):
    import shutil

    from reports_site import write_published_checksums
    from trusted_viewer import apply_declarations

    folder, pin, viewer = case
    root = folder / "reports"
    target = root / "robot/hallway/runs/old"
    target.mkdir(parents=True)
    for name in ("report.json", "index.html", "recording.json"):
        shutil.copyfile(folder / name, target / name)
    (target / "unrelated.json").write_text("original")
    write_published_checksums(target)
    (target / "unrelated.json").write_text("modified")
    before = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    with pytest.raises(SiteError, match="digest mismatch"):
        apply_declarations(root, {"robot/hallway/runs/old": (pin, viewer)})
    assert before == {
        p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()
    }


def test_site_decorates_only_produced_or_previously_decorated_runs(tmp_path, monkeypatch):
    import site_action
    import trusted_viewer

    root = tmp_path / "output"
    old = root / "robot/hallway/runs/old"
    old.mkdir(parents=True)
    produced = root / "robot/hallway/runs/new"
    decorated = root / "robot/hallway/runs/decorated"
    (decorated / "trusted-viewer").mkdir(parents=True)
    seen = []
    monkeypatch.setattr(trusted_viewer, "load_viewers", lambda _: [])

    def derive(root, viewers, folders):
        seen.append(set(folders))
        return {}

    monkeypatch.setattr(trusted_viewer, "derive_declarations", derive)
    monkeypatch.setattr(trusted_viewer, "prepare_history", lambda *args: None)
    monkeypatch.setattr(trusted_viewer, "apply_declarations", lambda *args: None)
    monkeypatch.setattr(site_action.site_tool, "build", lambda *args, **kwargs: [produced])
    monkeypatch.setattr(site_action.site_fixture, "check_reports_tree", lambda _: [])
    monkeypatch.setattr(site_action.site_tool, "read_json_object", lambda _: {"runs": 3})
    site_action.run_site_action(
        {
            "INPUT_RUNS_DIR": "inputs",
            "INPUT_OUTPUT_DIR": "output",
            "GITHUB_WORKSPACE": str(tmp_path),
            "INPUT_RESTORE_HISTORY": "false",
        }
    )
    assert seen == [{decorated}, {decorated, produced}]
    assert all(old not in selected for selected in seen)


@pytest.mark.parametrize("index", [True, False, 1.0, 0, -1, "1"])
@pytest.mark.parametrize("where", ["report", "recording"])
@pytest.mark.parametrize("restored", [False, True])
def test_trial_index_type_parity(case, index, where, restored):
    folder, pin, viewer = case
    if restored:
        install(folder, pin, viewer)
    report = json.loads((folder / "report.json").read_bytes())
    if where == "report":
        report["advisories"][0]["advisory"]["trials"][0]["index"] = index
    else:
        data = json.loads((folder / "recording.json").read_bytes())
        data["trials"][0]["trial"]["index"] = index
        raw = json.dumps(data).encode()
        (folder / "recording.json").write_bytes(raw)
        report["advisories"][0]["advisory"]["details_file"]["sha256"] = sha(raw)
    raw = json.dumps(report).encode()
    (folder / "report.json").write_bytes(raw)
    pin["report_sha256"] = sha(raw)
    with pytest.raises(SiteError):
        if restored:
            revalidate(folder, pin)
        else:
            install(folder, pin, viewer)
