"""Baseline detection, snapshots and technical failures of the renovate-baseline check."""

import copy
import json
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def write_context(directory, point, **fields):
    path = Path(directory) / f"context-{point}.json"
    path.write_text(json.dumps(dict(execution_point=point, **fields)))
    return str(path)


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "rules/checks/renovate-baseline/check.py"
spec = importlib.util.spec_from_file_location("renovate_baseline", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

BASELINE = json.loads((ROOT / "renovate.json").read_text())


def changed(mutate):
    config = copy.deepcopy(BASELINE)
    mutate(config)
    return config


def rule(config, key, value):
    return next(r for r in config["packageRules"] if value in r.get(key, []))


class BaselineTests(unittest.TestCase):
    def test_repository_file_and_extensions_pass(self):
        self.assertEqual(mod.violations(BASELINE), [])
        extended = changed(lambda c: c.update(automerge=False, rebaseWhen="behind-base-branch"))
        extended["packageRules"].append({"matchPackageNames": ["eclipse-temurin"], "allowedVersions": "/^21/"})
        self.assertEqual(mod.violations(extended), [])

    def test_equivalent_forms_pass(self):
        def mutate(config):
            config["minimumReleaseAge"] = "1 week"
            rule(config, "matchUpdateTypes", "major")["minimumReleaseAge"] = "2 weeks"
            actions = {"matchFileNames": [".github/**"], "minimumReleaseAge": "14 days"}
            config["packageRules"].append(actions)

        self.assertEqual(mod.violations(changed(mutate)), [])

    def test_deviations_are_reported(self):
        def drop_rule(key, value):
            return lambda c: c["packageRules"].remove(rule(c, key, value))

        cases = {
            "empty": ({}, 6),
            "no governor": (changed(lambda c: c.update(extends=["config:best-practices"])), 1),
            "platform automerge": (changed(lambda c: c.update(platformAutomerge=True)), 1),
            "short default age": (changed(lambda c: c.update(minimumReleaseAge="6 days")), 1),
            "unsupported age": (changed(lambda c: c.update(minimumReleaseAge="a week")), 1),
            "no major rule": (changed(drop_rule("matchUpdateTypes", "major")), 1),
            "short major age": (changed(lambda c: rule(c, "matchUpdateTypes", "major").update(minimumReleaseAge="13 days")), 1),
            "major delays cli": (
                changed(lambda c: rule(c, "matchUpdateTypes", "major")["matchPackageNames"].remove("!please-behave/cli")),
                1,
            ),
            "major delays action": (
                changed(lambda c: rule(c, "matchUpdateTypes", "major")["matchPackageNames"].remove("!please-behave/action")),
                1,
            ),
            "no actions rule": (changed(drop_rule("matchManagers", "github-actions")), 1),
            "actions delay action": (
                changed(lambda c: rule(c, "matchManagers", "github-actions")["matchPackageNames"].remove("!please-behave/action")),
                1,
            ),
            "actions delay cli": (changed(lambda c: rule(c, "matchManagers", "github-actions").pop("matchPackageNames")), 1),
            "no automerge": (changed(lambda c: rule(c, "matchUpdateTypes", "minor").update(automerge=False)), 1),
            "major automerge": (changed(lambda c: rule(c, "matchUpdateTypes", "minor")["matchUpdateTypes"].append("major")), 1),
            "patch missing": (changed(lambda c: rule(c, "matchUpdateTypes", "minor")["matchUpdateTypes"].remove("patch")), 1),
            "not an object": ([], 1),
            "malformed values": ({"extends": "x", "packageRules": ["x", {"matchUpdateTypes": "major"}]}, 6),
        }
        for name, (config, count) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(len(mod.violations(config)), count)


class CommandTests(unittest.TestCase):
    def run_check(self, point, content):
        with tempfile.TemporaryDirectory(prefix="renovate-baseline-") as temporary:
            repo = Path(temporary)
            for args in (
                ["init", "--quiet"],
                ["config", "user.name", "T"],
                ["config", "user.email", "t@example.invalid"],
                ["config", "commit.gpgsign", "false"],
            ):
                subprocess.run(["git", *args], cwd=repo, check=True)
            if content is not None:
                (repo / "renovate.json").write_bytes(content if isinstance(content, bytes) else content.encode())
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "--quiet", "--allow-empty", "-m", "test: record"], cwd=repo, check=True)
            context = write_context(temporary, point)
            return subprocess.run([sys.executable, "-B", str(SCRIPT), context], cwd=repo, capture_output=True, text=True)

    def test_verdicts(self):
        text = json.dumps(BASELINE)
        self.assertEqual(self.run_check("pre-commit", text).returncode, 0)
        self.assertEqual(self.run_check("check", text).returncode, 0)
        self.assertEqual(self.run_check("pre-push", text).returncode, 0)
        missing = self.run_check("check", None)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("missing", missing.stdout)
        self.assertEqual(self.run_check("pre-push", "{}").returncode, 1)

    def test_invalid_json_is_a_verdict(self):
        result = self.run_check("check", "{ not json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not valid JSON", result.stdout)

    def test_unsupported_point_and_binary_file_are_errors(self):
        self.assertEqual(self.run_check("commit-msg", json.dumps(BASELINE)).returncode, 2)
        self.assertEqual(self.run_check("check", b"\xff").returncode, 2)


if __name__ == "__main__":
    unittest.main()
