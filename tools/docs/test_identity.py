# SPDX-License-Identifier: Apache-2.0
import unittest

from identity import validate

SHA = "a" * 40


class IdentityTest(unittest.TestCase):
    def test_preview_uniqueness_and_refusals(self):
        versions = set()
        for run_id, run, attempt in [(10, 1, 1), (10, 1, 2), (11, 2, 1)]:
            env = dict(
                DOCS_KIND="candidate",
                MANUAL_VERSION=f"1.2.3-pr.4.{run}.{attempt}",
                PR_NUMBER="4",
                GITHUB_RUN_NUMBER=str(run),
                GITHUB_RUN_ID=str(run_id),
                GITHUB_RUN_ATTEMPT=str(attempt),
                CANDIDATE_COMMIT=SHA,
            )
            versions.add(validate(SHA, False, env)["version"])
            with self.assertRaises(ValueError):
                validate(SHA, False, {**env, "GITHUB_RUN_ATTEMPT": "9"})
            with self.assertRaises(ValueError):
                validate(SHA, True, env)
        self.assertEqual(len(versions), 3)

    def test_released_identity(self):
        env = dict(
            DOCS_KIND="release",
            MANUAL_VERSION="1.2.3",
            RELEASE_VERSION="1.2.3",
            RELEASE_TAG="v1.2.3",
            RELEASE_COMMIT=SHA,
        )
        self.assertEqual(validate(SHA, False, env, SHA)["release_tag"], "v1.2.3")
        for dirty, target in [(True, SHA), (False, "b" * 40)]:
            with self.assertRaises(ValueError):
                validate(SHA, dirty, env, target)
        self.assertEqual(validate(SHA, False, {})["version"], "development")
        with self.assertRaises(ValueError):
            validate(SHA, False, {"MANUAL_VERSION": "1.2.3"})
