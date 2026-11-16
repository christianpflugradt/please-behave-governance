"""Violation detection of the private-ci-workflow check on parsed workflows."""

import copy
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parent / "check.py"
spec = importlib.util.spec_from_file_location("private_ci_workflow", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SHA = "0123456789abcdef0123456789abcdef01234567"
WORKFLOW = {
    "name": "CI",
    "on": {"pull_request": None},
    "permissions": {"contents": "read"},
    "concurrency": {"group": "ci-${{ github.ref }}", "cancel-in-progress": True},
    "jobs": {
        "check": {
            "runs-on": "ubuntu-24.04",
            "timeout-minutes": 5,
            "steps": [
                {"uses": f"actions/checkout@{SHA}", "with": {"persist-credentials": False}},
                {"uses": f"jdx/mise-action@{SHA}"},
                {
                    "uses": f"please-behave/action@{SHA}",
                    "with": {"install": "false", "sync": "true", "execution-point": "check", "checks-only": "true"},
                },
            ],
        }
    },
}


def changed(mutate):
    document = copy.deepcopy(WORKFLOW)
    mutate(document)
    return mod.violations(document, 5)


class PrivateCiWorkflowTests(unittest.TestCase):
    def test_lean_workflow_passes(self):
        self.assertEqual(mod.violations(WORKFLOW, 5), [])

    def test_push_trigger_fails(self):
        self.assertTrue(changed(lambda d: d["on"].update(push={"branches": ["main"]})))

    def test_unversioned_or_non_ubuntu_runner_fails(self):
        for runner in ("ubuntu-latest", "macos-15", "windows-2025"):
            self.assertTrue(changed(lambda d, r=runner: d["jobs"]["check"].update({"runs-on": r})), runner)

    def test_timeout_above_ceiling_or_missing_fails(self):
        self.assertTrue(changed(lambda d: d["jobs"]["check"].update({"timeout-minutes": 6})))
        self.assertTrue(changed(lambda d: d["jobs"]["check"].pop("timeout-minutes")))

    def test_matrix_and_missing_concurrency_fail(self):
        self.assertTrue(changed(lambda d: d["jobs"]["check"].update(strategy={"matrix": {"os": ["ubuntu-24.04"]}})))
        self.assertTrue(changed(lambda d: d.pop("concurrency")))

    def test_reviews_step_must_be_checks_only(self):
        self.assertTrue(changed(lambda d: d["jobs"]["check"]["steps"][2]["with"].update({"checks-only": "false"})))

    def test_ceiling_parameter_must_be_positive_integer(self):
        for value in ("0", "-1", "five", ""):
            with self.assertRaises(ValueError):
                mod.max_minutes(value)
        self.assertEqual(mod.max_minutes("5"), 5)


if __name__ == "__main__":
    unittest.main()
