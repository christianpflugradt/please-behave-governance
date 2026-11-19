"""Offline tests for the mise release task; gh and sleep are mocked."""

import importlib.machinery
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

# The task is an extensionless executable, so it needs an explicit loader.
TASK = Path(__file__).resolve().parents[2] / "mise-tasks/release/release"
loader = importlib.machinery.SourceFileLoader("release_task", str(TASK))
spec = importlib.util.spec_from_loader("release_task", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)


class FakeGh:
    """Scripted gh: CI listings are consumed in order; the last one repeats."""

    def __init__(self, ci_runs, release_runs=None):
        self.ci_runs = list(ci_runs)
        self.release_runs = list(release_runs or [[]])
        self.calls = []

    def __call__(self, *command, capture=True):
        self.calls.append(command)
        if command[:3] == ("gh", "run", "list"):
            queue = self.ci_runs if mod.CI_WORKFLOW in command else self.release_runs
            return json.dumps(queue.pop(0) if len(queue) > 1 else queue[0])
        return ""

    def named(self, *prefix):
        return [call for call in self.calls if call[: len(prefix)] == prefix]


def ci(*statuses):
    return [{"databaseId": 100 + i, "status": status} for i, status in enumerate(statuses)]


class ReleaseTaskTest(unittest.TestCase):
    def release(self, gh, follow, **inputs):
        with patch.object(mod, "run", gh), patch.object(mod.time, "sleep") as sleep, patch("builtins.print"):
            mod.release(follow, **inputs)
        return sleep

    def test_dispatches_without_following_when_ci_is_idle(self):
        gh = FakeGh([ci("completed")])
        self.release(gh, follow=False)
        self.assertEqual(gh.named("gh", "workflow", "run"), [("gh", "workflow", "run", "release.yml", "--ref", "main")])
        self.assertEqual(gh.named("gh", "run", "watch"), [])

    def test_force_and_prerelease_become_dispatch_inputs(self):
        gh = FakeGh([ci("completed")])
        self.release(gh, follow=False, force=True, prerelease=True)
        self.assertEqual(
            gh.named("gh", "workflow", "run"),
            [("gh", "workflow", "run", "release.yml", "--ref", "main", "-f", "force=true", "-f", "mode=prerelease")],
        )

    def test_release_flag_selects_the_official_mode(self):
        gh = FakeGh([ci("completed")])
        self.release(gh, follow=False, official=True)
        self.assertEqual(
            gh.named("gh", "workflow", "run"), [("gh", "workflow", "run", "release.yml", "--ref", "main", "-f", "mode=release")]
        )

    def test_follow_passes_inputs_to_the_dispatch(self):
        gh = FakeGh([ci("completed")], [[{"databaseId": 5}], [{"databaseId": 5}, {"databaseId": 9}]])
        self.release(gh, follow=True, force=True)
        self.assertEqual(gh.named("gh", "workflow", "run"), [("gh", "workflow", "run", "release.yml", "--ref", "main", "-f", "force=true")])

    def test_waits_while_ci_is_queued_or_in_progress(self):
        gh = FakeGh([ci("queued"), ci("in_progress"), ci("completed")])
        sleep = self.release(gh, follow=False)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(len(gh.named("gh", "workflow", "run")), 1)
        last_list = max(i for i, c in enumerate(gh.calls) if c[:3] == ("gh", "run", "list"))
        self.assertLess(last_list, gh.calls.index(gh.named("gh", "workflow", "run")[0]))

    def test_follow_watches_the_new_run_only(self):
        gh = FakeGh([ci("completed")], [[{"databaseId": 5}], [{"databaseId": 5}, {"databaseId": 9}]])
        self.release(gh, follow=True)
        self.assertEqual(gh.named("gh", "run", "watch"), [("gh", "run", "watch", "9", "--exit-status")])

    def test_follow_fails_when_run_never_appears(self):
        gh = FakeGh([ci("completed")], [[]])
        with self.assertRaises(ValueError):
            self.release(gh, follow=True)

    def test_run_reports_failed_command(self):
        with patch.object(mod.subprocess, "run") as sub:
            sub.return_value.returncode = 1
            sub.return_value.stderr = "boom"
            with self.assertRaisesRegex(ValueError, "boom"):
                mod.run("gh", "x")


if __name__ == "__main__":
    unittest.main()
