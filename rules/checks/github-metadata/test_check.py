"""Metadata limits, request headers and technical failures with mocked HTTP."""

import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock
import urllib.error

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("github_metadata", ROOT / "rules/checks/github-metadata/check.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

GOOD = {"description": "x" * mod.MIN_DESCRIPTION, "topics": ["a", "b", "c"]}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def respond(document):
    return mock.patch("urllib.request.urlopen", return_value=Response(json.dumps(document).encode()))


class ViolationTests(unittest.TestCase):
    def test_limits(self):
        cases = {
            "good": (GOOD, 0),
            "description one short": (dict(GOOD, description="x" * (mod.MIN_DESCRIPTION - 1)), 1),
            "description padded with spaces": (dict(GOOD, description=" " * 40), 1),
            "description null": (dict(GOOD, description=None), 1),
            "too few topics": (dict(GOOD, topics=["a", "b"]), 1),
            "most topics": (dict(GOOD, topics=list("abcdefghij")), 0),
            "too many topics": (dict(GOOD, topics=list("abcdefghijk")), 1),
            "nothing": ({}, 2),
        }
        for name, (document, count) in cases.items():
            with self.subTest(name):
                self.assertEqual(len(mod.violations(document)), count)


class MainTests(unittest.TestCase):
    def run_main(self, env, document=GOOD):
        with mock.patch.dict("os.environ", env, clear=True), respond(document) as opened:
            return mod.main(), opened

    def test_pass_without_token(self):
        code, opened = self.run_main({"GITHUB_REPOSITORY": "owner/name"})
        self.assertEqual(code, 0)
        request = opened.call_args[0][0]
        self.assertEqual(request.full_url, "https://api.github.com/repos/owner/name")
        self.assertFalse(request.has_header("Authorization"))

    def test_token_is_sent_when_set(self):
        _, opened = self.run_main({"GITHUB_REPOSITORY": "owner/name", "GITHUB_TOKEN": "synthetic"})
        self.assertEqual(opened.call_args[0][0].get_header("Authorization"), "Bearer synthetic")

    def test_fail_returns_one(self):
        code, _ = self.run_main({"GITHUB_REPOSITORY": "owner/name"}, {"description": "short", "topics": []})
        self.assertEqual(code, 1)

    def test_missing_or_malformed_repository_is_an_error(self):
        for env in ({}, {"GITHUB_REPOSITORY": "owner"}, {"GITHUB_REPOSITORY": "../x/y z"}):
            with self.subTest(env), mock.patch.dict("os.environ", env, clear=True), self.assertRaises(RuntimeError):
                mod.main()

    def test_http_and_network_failures_are_errors(self):
        failures = [urllib.error.HTTPError("u", 404, "nf", {}, None), urllib.error.URLError("down"), TimeoutError()]
        for failure in failures:
            with (
                self.subTest(type(failure).__name__),
                mock.patch("urllib.request.urlopen", side_effect=failure),
                self.assertRaises(RuntimeError),
            ):
                mod.fetch("owner/name", None)

    def test_unexpected_document_is_an_error(self):
        with respond([]), self.assertRaises(RuntimeError):
            mod.fetch("owner/name", None)


if __name__ == "__main__":
    unittest.main()
