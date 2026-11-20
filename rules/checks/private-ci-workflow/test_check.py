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
G = "christianpflugradt/please-behave-governance"
WORKFLOW = {
    "name": "CI",
    "on": {"pull_request": None},
    "permissions": {"contents": "read"},
    "concurrency": {"group": "ci-${{ github.ref }}", "cancel-in-progress": True},
    "jobs": {"check": {"uses": f"{G}/.github/workflows/ci-check-private.yml@{SHA} # updates/workflows/ci"}},
}


def changed(mutate):
    document = copy.deepcopy(WORKFLOW)
    mutate(document)
    return mod.violations(document)


class PrivateCiWorkflowTests(unittest.TestCase):
    def test_lean_workflow_passes(self):
        self.assertEqual(mod.violations(WORKFLOW), [])

    def test_push_trigger_fails(self):
        self.assertTrue(changed(lambda d: d["on"].update(push={"branches": ["main"]})))

    def test_missing_concurrency_fails(self):
        self.assertTrue(changed(lambda d: d.pop("concurrency")))

    def test_check_calls_the_pinned_shared_workflow(self):
        self.assertTrue(changed(lambda d: d["jobs"]["check"].update(uses=f"{G}/.github/workflows/ci-check-private.yml@main")))
        self.assertTrue(changed(lambda d: d["jobs"]["check"].update(uses=f"{G}/.github/workflows/ci-check.yml@{SHA}")))
        self.assertTrue(changed(lambda d: d["jobs"]["check"].update(steps=[{"run": "true"}])))

    def test_reviews_stay_out(self):
        self.assertTrue(changed(lambda d: d["jobs"]["check"].update(secrets={"GEMINI_API_KEY": "${{ secrets.GEMINI_API_KEY }}"})))

    def test_runner_input_must_be_versioned(self):
        self.assertEqual(changed(lambda d: d["jobs"]["check"].update({"with": {"runner": "ubuntu-26.04"}})), [])
        self.assertTrue(changed(lambda d: d["jobs"]["check"].update({"with": {"runner": "ubuntu-latest"}})))

    def test_matrix_fails(self):
        self.assertTrue(changed(lambda d: d["jobs"]["check"].update(strategy={"matrix": {"os": ["a"]}})))


if __name__ == "__main__":
    unittest.main()
