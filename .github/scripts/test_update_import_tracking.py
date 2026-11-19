"""Offline regression tests for tracking publication using a synthetic Git catalog."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("tracking", Path(__file__).with_name("update-import-tracking.py"))
tracking = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tracking)


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        environment = {key: value for key, value in os.environ.items() if not key.startswith(("GIT_", "PLEASE_"))}
        environment.update(
            GIT_AUTHOR_NAME="Test",
            GIT_AUTHOR_EMAIL="test@example.invalid",
            GIT_COMMITTER_NAME="Test",
            GIT_COMMITTER_EMAIL="test@example.invalid",
        )
        self.environment = patch.dict(os.environ, environment, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.git("init", "--template=", "-b", "main")
        self.write("rulesets/sample/ruleset.toml", 'rules = ["rules/checks/example"]\n')
        self.write("rules/checks/example/rule.toml", 'id = "example"\n')
        self.write("rules/checks/example/check.py", 'print("original")\n')
        self.write("providers/sample/provider.toml", 'command = "python3"\n')
        self.write("providers/sample/adapter.py", 'print("provider")\n')
        self.write("mise-tasks/sample/task", "#!/bin/sh\necho task\n")
        self.write("actions/sample/plan/action.yml", "name: Plan\n")
        self.write("actions/sample/lib/plan.mjs", "export {};\n")
        self.initial = self.commit()
        for package in tracking.packages(self.root, self.initial):
            self.track(package, self.initial)

    def git(self, *args):
        return tracking.git(self.root, *args).decode().strip()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-m", "fixture")
        return self.git("rev-parse", "HEAD")

    def track(self, package, commit):
        self.git("update-ref", tracking.PREFIX + package, commit)

    def updates(self):
        return tracking.plan(self.root, self.git("rev-parse", "HEAD"))

    def test_pin_only_merge_and_unrelated_changes_do_not_publish(self):
        self.write(".please/config.toml", f'ref = "{self.initial}"\n')
        self.write("README.md", "documentation\n")
        self.write("rules/checks/unrelated/check.py", "unrelated\n")
        self.commit()
        self.assertEqual(self.updates(), [])

    def test_rule_change_updates_only_its_ruleset_and_stops_after_pin_merge(self):
        self.write("rules/checks/example/check.py", 'print("changed")\n')
        changed = self.commit()
        self.assertEqual(self.updates(), [f"{changed}:refs/heads/updates/rulesets/sample"])
        self.track("rulesets/sample", changed)
        self.write(".please/config.toml", f'ref = "{changed}"\n')
        self.commit()
        self.assertEqual(self.updates(), [])

    def test_provider_change_updates_only_provider(self):
        self.write("providers/sample/adapter.py", 'print("changed")\n')
        changed = self.commit()
        self.assertEqual(self.updates(), [f"{changed}:refs/heads/updates/providers/sample"])

    def test_task_group_change_updates_only_that_group(self):
        self.write("mise-tasks/sample/task", "#!/bin/sh\necho changed\n")
        self.write("mise-tasks/sample/helper", "helper\n")
        changed = self.commit()
        self.assertEqual(self.updates(), [f"{changed}:refs/heads/updates/mise-tasks/sample"])
        self.track("mise-tasks/sample", changed)
        self.write("mise-tasks/other/task", "#!/bin/sh\n")
        added = self.commit()
        self.assertEqual(self.updates(), [f"{added}:refs/heads/updates/mise-tasks/other"])

    def test_action_group_change_updates_only_that_group(self):
        self.write("actions/sample/lib/plan.mjs", "export const changed = true;\n")
        changed = self.commit()
        self.assertEqual(self.updates(), [f"{changed}:refs/heads/updates/actions/sample"])

    def test_resource_addition_deletion_and_mode_change_count(self):
        for operation in ("add", "delete", "mode"):
            with self.subTest(operation=operation):
                if operation == "add":
                    self.write("rules/checks/example/data.txt", "resource\n")
                elif operation == "delete":
                    (self.root / "rules/checks/example/data.txt").unlink()
                else:
                    self.git("update-index", "--chmod=+x", "rules/checks/example/check.py")
                    (self.root / "rules/checks/example/check.py").chmod(0o755)
                changed = self.commit()
                self.assertEqual(self.updates(), [f"{changed}:refs/heads/updates/rulesets/sample"])
                self.track("rulesets/sample", changed)

    def test_rule_membership_change_counts_even_with_unchanged_rule_bytes(self):
        self.write("rulesets/sample/ruleset.toml", "rules = []\n")
        changed = self.commit()
        self.assertEqual(self.updates(), [f"{changed}:refs/heads/updates/rulesets/sample"])

    def test_missing_rule_and_removed_package_fail_closed(self):
        (self.root / "rules/checks/example/rule.toml").unlink()
        self.commit()
        with self.assertRaises(subprocess.CalledProcessError):
            self.updates()
        (self.root / "rulesets/sample/ruleset.toml").unlink()
        self.commit()
        with self.assertRaisesRegex(ValueError, "retiring packages"):
            self.updates()

    def test_tracking_branch_from_before_the_definition_rename_is_compared(self):
        self.git("mv", "rulesets/sample/ruleset.toml", "rulesets/sample/config.toml")
        legacy = self.commit()
        self.track("rulesets/sample", legacy)
        self.git("mv", "rulesets/sample/config.toml", "rulesets/sample/ruleset.toml")
        renamed = self.commit()
        self.assertEqual(self.updates(), [f"{renamed}:refs/heads/updates/rulesets/sample"])

    def test_initialization_and_late_ci_completion(self):
        self.git("update-ref", "-d", tracking.PREFIX + "providers/sample")
        self.assertEqual(self.updates(), [f"{self.initial}:refs/heads/updates/providers/sample"])
        self.track("providers/sample", self.initial)
        self.write("rules/checks/example/check.py", "changed\n")
        changed = self.commit()
        self.track("rulesets/sample", changed)
        self.assertEqual(tracking.plan(self.root, self.initial), [])

    def test_divergent_tracking_branch_is_not_overwritten(self):
        self.git("checkout", "--orphan", "diverged")
        self.git("commit", "-m", "unrelated history")
        self.track("rulesets/sample", self.git("rev-parse", "HEAD"))
        with self.assertRaisesRegex(ValueError, "diverges"):
            tracking.plan(self.root, self.initial)

    def test_publication_to_local_remote_is_idempotent_after_pin_merge(self):
        with tempfile.TemporaryDirectory() as remote:
            subprocess.run(["git", "init", "--bare", "--template=", remote], check=True, capture_output=True)
            self.git("remote", "add", "origin", remote)
            script = Path(__file__).with_name("update-import-tracking.py").resolve()

            def publish():
                subprocess.run([sys.executable, "-B", str(script), "--publish"], cwd=self.root, check=True, capture_output=True)

            publish()
            self.write("rules/checks/example/check.py", "changed\n")
            changed = self.commit()
            publish()
            self.write(".please/config.toml", f'ref = "{changed}"\n')
            self.commit()
            publish()
            self.assertEqual(self.git("rev-parse", tracking.PREFIX + "rulesets/sample"), changed)
            self.assertEqual(self.git("rev-parse", tracking.PREFIX + "providers/sample"), self.initial)


if __name__ == "__main__":
    unittest.main()
