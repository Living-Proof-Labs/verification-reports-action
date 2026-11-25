# SPDX-License-Identifier: Apache-2.0
import copy
import functools
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from smoke import check

SHA = "a" * 40


class PublicationTest(unittest.TestCase):
    def test_readme_opens_with_start_here_manual_link(self):
        head = Path("README.md").read_text().splitlines()[:6]
        self.assertTrue(
            any(
                line.startswith("**Start here: [")
                and "](https://docs.livingprooflabs.com/verification-reports-action/)" in line
                for line in head
            ),
            "README must open with a start-here link to the published manual",
        )

    def test_workflow_structure_and_permissions(self):
        workflow = yaml.load(Path(".github/workflows/docs.yml").read_text(), Loader=yaml.BaseLoader)
        release = yaml.load(
            Path(".github/workflows/release.yml").read_text(), Loader=yaml.BaseLoader
        )
        jobs = workflow["jobs"]
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(
            jobs["deploy"]["permissions"],
            {"contents": "read", "pages": "write", "id-token": "write"},
        )
        self.assertEqual(jobs["deploy"]["environment"]["name"], "github-pages")
        self.assertEqual(jobs["smoke"]["permissions"], {"contents": "read"})
        self.assertEqual(jobs["smoke"]["steps"][0]["with"]["ref"], "${{ inputs.release_commit }}")
        for job in ("build", "smoke"):
            self.assertEqual(
                jobs[job].get("permissions", workflow["permissions"]), {"contents": "read"}
            )
        self.assertEqual(jobs["production"]["permissions"], {"contents": "read", "actions": "read"})
        self.assertEqual(release["jobs"]["deploy-docs"]["permissions"]["actions"], "read")
        self.assertFalse(
            any("upload-pages-artifact" in s.get("uses", "") for s in jobs["build"]["steps"])
        )
        self.assertIn("--version", jobs["smoke"]["steps"][-1]["run"])
        self.assertEqual(workflow["on"]["merge_group"]["types"], ["checks_requested"])
        self.assertEqual(jobs["build"]["name"], "Documentation verification")
        self.assertEqual(release["jobs"]["deploy-docs"]["uses"], "./.github/workflows/docs.yml")
        self.assertEqual(release["jobs"]["deploy-docs"]["needs"], "release")

    def test_candidate_failure_is_not_a_skipped_required_check(self):
        workflow = yaml.load(Path(".github/workflows/docs.yml").read_text(), Loader=yaml.BaseLoader)
        build = workflow["jobs"]["build"]
        self.assertEqual(build["if"], "${{ !cancelled() && inputs.release_version == '' }}")
        guard = build["steps"][0]
        self.assertEqual(guard["if"], "github.event_name == 'pull_request'")
        for outcome, version, expected in (
            ("success", "1.2.3-pr.5.1.1", True),
            ("failure", "", False),
            ("skipped", "", False),
            ("success", "", False),
        ):
            result = subprocess.run(
                ["bash", "-e", "-c", guard["run"]],
                env={
                    **os.environ,
                    "CANDIDATE_RESULT": outcome,
                    "MANUAL_VERSION": version,
                    "CANDIDATE_COMMIT": SHA,
                },
            )
            self.assertEqual(result.returncode == 0, expected)

    def test_release_guard(self):
        workflow = yaml.load(Path(".github/workflows/docs.yml").read_text(), Loader=yaml.BaseLoader)
        guard = workflow["jobs"]["deploy"]["steps"][0]
        self.assertEqual(guard["env"]["GH_TOKEN"], "${{ github.token }}")
        release = {
            "tag_name": "v1.2.3",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-11-25",
        }
        base = {
            "branches/main": {"protected": True, "commit": {"sha": SHA}},
            "git/ref/tags/v1.2.3": {
                "ref": "refs/tags/v1.2.3",
                "object": {"type": "commit", "sha": SHA},
            },
            "releases/tags/v1.2.3": release,
        }

        cases = [("valid", {}, {}, True)]
        for label, env in (
            ("branch", {"GITHUB_REF": "refs/heads/feature"}),
            ("unprotected context", {"GITHUB_REF_PROTECTED": "false"}),
            ("missing version", {"RELEASE_VERSION": ""}),
            ("wrong tag", {"RELEASE_TAG": "v2.0.0"}),
            ("wrong commit", {"RELEASE_COMMIT": "b" * 40}),
        ):
            cases.append((label, env, {}, False))
        for label, path, value in (
            ("superseded", "branches/main", {"protected": True, "commit": {"sha": "b" * 40}}),
            ("unprotected", "branches/main", {"protected": False, "commit": {"sha": SHA}}),
            (
                "wrong tag target",
                "git/ref/tags/v1.2.3",
                {"ref": "refs/tags/v1.2.3", "object": {"type": "commit", "sha": "b" * 40}},
            ),
            ("missing release", "releases/tags/v1.2.3", None),
            ("draft", "releases/tags/v1.2.3", {**release, "draft": True}),
            ("prerelease", "releases/tags/v1.2.3", {**release, "prerelease": True}),
            ("unpublished", "releases/tags/v1.2.3", {**release, "published_at": None}),
        ):
            cases.append((label, {}, {path: value}, False))
        for label, overrides, changes, succeeds in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                responses = copy.deepcopy(base)
                responses.update(changes)
                data = Path(directory) / "responses.json"
                data.write_text(json.dumps(responses))
                gh = Path(directory) / "gh"
                gh.write_text(
                    "#!/usr/bin/env python3\n"
                    "import json, os, sys\n"
                    "x=json.load(open(os.environ['RESPONSES'])).get("
                    "sys.argv[2].removeprefix('repos/example/repo/'))\n"
                    "if x is None: sys.exit(1)\n"
                    "print(json.dumps(x))\n"
                )
                gh.chmod(0o755)
                result = subprocess.run(
                    ["bash", "-c", guard["run"]],
                    capture_output=True,
                    env={
                        **os.environ,
                        "PATH": directory + ":" + os.environ["PATH"],
                        "RESPONSES": str(data),
                        "GITHUB_REF": "refs/heads/main",
                        "GITHUB_REF_PROTECTED": "true",
                        "GITHUB_SHA": SHA,
                        "GITHUB_REPOSITORY": "example/repo",
                        "RELEASE_VERSION": "1.2.3",
                        "RELEASE_TAG": "v1.2.3",
                        "RELEASE_COMMIT": SHA,
                        **overrides,
                    },
                )
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)

    def test_served_manual(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "manual"
            (root / "assets").mkdir(parents=True)
            brand = b"/* test brand */"
            (root / "assets/brand.css").write_bytes(brand)
            identity = {
                "source_commit": SHA,
                "source_dirty": False,
                "kind": "release",
                "version": "1.2.3",
                "release_tag": "v1.2.3",
                "brand": {"sha256": hashlib.sha256(brand).hexdigest()},
            }
            (root / "build.json").write_text(json.dumps(identity))
            for page, text in [
                ("index", "verification-reports-action"),
                ("reference", "docs:build"),
                ("version", SHA),
            ]:
                (root / f"{page}.html").write_text(
                    '<link rel="stylesheet" href="assets/brand.css">' + text
                )
            (root / "reports").mkdir()
            (root / "reports/index.html").write_text("verification-reports-action fixture")
            handler = functools.partial(SimpleHTTPRequestHandler, directory=directory)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/manual/"
                check(url, SHA, "1.2.3")
                index_page = root / "index.html"
                original_index = index_page.read_text()
                (root / "assets/empty.css").write_bytes(b"")
                index_page.write_text(
                    original_index + '<link rel="stylesheet" href="assets/empty.css">'
                )
                with self.assertRaisesRegex(ValueError, "Empty asset"):
                    check(url, SHA, "1.2.3")
                index_page.write_text(original_index)
                (root / "reports/index.html").unlink()
                with self.assertRaisesRegex(ValueError, "published reports section"):
                    check(url, SHA, "1.2.3")
                (root / "reports/index.html").write_text("verification-reports-action fixture")
                with self.assertRaisesRegex(ValueError, "version identity"):
                    check(url, SHA, "2.0.0")
                with self.assertRaisesRegex(ValueError, "stale or dirty"):
                    check(url, "b" * 40)
                identity["source_dirty"] = True
                (root / "build.json").write_text(json.dumps(identity))
                with self.assertRaisesRegex(ValueError, "stale or dirty"):
                    check(url, SHA)
                identity["source_dirty"] = False
                (root / "build.json").write_text(json.dumps(identity))
                (root / "assets/brand.css").write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    check(url, SHA)
                (root / "assets/brand.css").unlink()
                with self.assertRaises(OSError):
                    check(url, SHA)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
