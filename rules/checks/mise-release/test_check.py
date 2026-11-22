"""Local and shared release task detection of the mise release check."""

import importlib.util
from pathlib import Path
import tomllib
import unittest


spec = importlib.util.spec_from_file_location("mise_release", Path(__file__).resolve().parent / "check.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SHA = "a" * 40
INCLUDE = f"git::https://github.com/christianpflugradt/repository-catalog.git//mise-tasks/release?ref={SHA}"


def violations(text):
    return mod.violations(tomllib.loads(text))


class ReleaseTaskTests(unittest.TestCase):
    def test_local_task_passes(self):
        self.assertEqual(violations('[tasks.release]\nrun = "echo release"\n'), [])

    def test_missing_task_fails(self):
        self.assertEqual(len(violations("[tools]\n")), 1)

    def test_pinned_shared_include_passes(self):
        self.assertEqual(violations(f'[task_config]\nincludes = [".mise/tasks", "{INCLUDE}"]\n'), [])

    def test_unpinned_or_foreign_includes_fail(self):
        for entry in (
            INCLUDE.replace(SHA, "v1.0.0"),
            INCLUDE.replace("//mise-tasks/release", "//mise-tasks/format"),
            INCLUDE.replace("christianpflugradt", "someone-else"),
            INCLUDE + "&depth=1",
        ):
            with self.subTest(entry=entry):
                self.assertEqual(len(violations(f'[task_config]\nincludes = ["{entry}"]\n')), 1)


if __name__ == "__main__":
    unittest.main()
