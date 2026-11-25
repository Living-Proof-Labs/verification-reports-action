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


class PublicationGuardTest(unittest.TestCase):
    def test_workflow_structure(self):
        workflow = yaml.load(Path(".github/workflows/docs.yml").read_text(), Loader=yaml.BaseLoader)
        jobs = workflow["jobs"]
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(workflow["on"]["merge_group"]["types"], ["checks_requested"])
        self.assertEqual(jobs["build"]["name"], "Documentation verification")
        self.assertEqual(
            jobs["deploy"]["permissions"],
            {"contents": "read", "pages": "write", "id-token": "write"},
        )
        self.assertEqual(jobs["smoke"]["permissions"], {"contents": "read"})
        for job in ("build", "production", "smoke"):
            self.assertEqual(
                jobs[job].get("permissions", workflow["permissions"]), {"contents": "read"}
            )
        self.assertFalse(
            any("upload-pages-artifact" in s.get("uses", "") for s in jobs["build"]["steps"])
        )

    def test_candidate_failure_is_not_a_skipped_required_check(self):
        workflow = yaml.load(Path(".github/workflows/docs.yml").read_text(), Loader=yaml.BaseLoader)
        build = workflow["jobs"]["build"]
        self.assertEqual(build["if"], "${{ !cancelled() }}")
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

    def test_deploy_guard(self):
        workflow = yaml.load(Path(".github/workflows/docs.yml").read_text(), Loader=yaml.BaseLoader)
        guard = workflow["jobs"]["deploy"]["steps"][0]
        self.assertEqual(guard["env"]["GH_TOKEN"], "${{ github.token }}")
        base = {"branches/main": {"protected": True, "commit": {"sha": SHA}}}
        cases = [("valid", {}, {}, True)]
        for label, env in (
            ("PR", {"GITHUB_EVENT_NAME": "pull_request"}),
            ("queue", {"GITHUB_EVENT_NAME": "merge_group"}),
            ("branch", {"GITHUB_REF": "refs/heads/feature"}),
            ("unprotected context", {"GITHUB_REF_PROTECTED": "false"}),
        ):
            cases.append((label, env, {}, False))
        for label, path, value in (
            ("superseded", "branches/main", {"protected": True, "commit": {"sha": "b" * 40}}),
            ("unprotected", "branches/main", {"protected": False, "commit": {"sha": SHA}}),
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
                    'x=json.load(open(os.environ["RESPONSES"])).get('
                    'sys.argv[2].removeprefix("repos/example/repo/"))\n'
                    "if x is None: sys.exit(1)\n"
                    "p='true' if x.get('protected') else 'false'\n"
                    "print(f\"{x['commit']['sha']}\\t{p}\")\n"
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
                        "GITHUB_EVENT_NAME": "push",
                        "GITHUB_REF_PROTECTED": "true",
                        "GITHUB_SHA": SHA,
                        "GITHUB_REPOSITORY": "example/repo",
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
                ("provenance", "Pinned inputs"),
                ("version", SHA),
            ]:
                (root / f"{page}.html").write_text(
                    '<link rel="stylesheet" href="assets/brand.css">' + text
                )
            handler = functools.partial(SimpleHTTPRequestHandler, directory=directory)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/manual/"
                check(url, SHA, "1.2.3")
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
