"""Violation detection of the statistics-workflow check on parsed workflows."""

import copy
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parent / "check.py"
spec = importlib.util.spec_from_file_location("statistics_workflow", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SHA = "0123456789abcdef0123456789abcdef01234567"
G = "christianpflugradt/please-behave-governance"
WORKFLOW = {
    "name": "Statistics",
    "on": {"push": {"branches": ["main"]}},
    "permissions": {"contents": "read"},
    "jobs": {
        "collect": {
            "runs-on": "ubuntu-24.04",
            "timeout-minutes": 30,
            "steps": [{"uses": "actions/checkout@v7", "with": {"persist-credentials": False}}],
        },
        "publish": {
            "needs": ["collect"],
            "if": "github.ref == 'refs/heads/main'",
            "uses": f"{G}/.github/workflows/statistics-publish.yml@{SHA} # updates/workflows/statistics",
            "with": {"project": "example"},
            "secrets": {"STATISTICS_TOKEN": "${{ secrets.STATISTICS_TOKEN }}"},
        },
    },
}


def changed(mutate):
    document = copy.deepcopy(WORKFLOW)
    mutate(document)
    return mod.violations(document)


class StatisticsWorkflowTests(unittest.TestCase):
    def test_standard_workflow_passes(self):
        self.assertEqual(mod.violations(WORKFLOW), [])

    def test_several_collect_jobs_must_all_be_awaited(self):
        def two(d):
            d["jobs"]["collect-backend"] = copy.deepcopy(d["jobs"].pop("collect"))
            d["jobs"]["collect-renderer"] = copy.deepcopy(d["jobs"]["collect-backend"])
            d["jobs"]["publish"]["needs"] = ["collect-backend", "collect-renderer"]

        self.assertEqual(changed(two), [])
        self.assertTrue(changed(lambda d: (two(d), d["jobs"]["publish"].update(needs=["collect-backend"]))))

    def test_triggers_and_name(self):
        self.assertTrue(changed(lambda d: d.update(name="Stats")))
        self.assertTrue(changed(lambda d: d["on"].update(workflow_dispatch={})))
        self.assertTrue(changed(lambda d: d["on"]["push"].update(branches=["main", "dev"])))

    def test_publish_calls_the_pinned_shared_workflow(self):
        self.assertTrue(changed(lambda d: d["jobs"]["publish"].update(uses=f"{G}/.github/workflows/statistics-publish.yml@main")))
        self.assertTrue(changed(lambda d: d["jobs"]["publish"].update(uses=f"other/repo/.github/workflows/statistics-publish.yml@{SHA}")))
        self.assertTrue(changed(lambda d: d["jobs"]["publish"].pop("uses")))

    def test_publish_passes_project_and_the_token_explicitly(self):
        self.assertTrue(changed(lambda d: d["jobs"]["publish"].pop("with")))
        self.assertTrue(changed(lambda d: d["jobs"]["publish"].pop("secrets")))
        self.assertTrue(changed(lambda d: d["jobs"]["publish"].update(secrets="inherit")))
        self.assertTrue(changed(lambda d: d["jobs"]["publish"]["secrets"].update(OTHER="x")))

    def test_publish_has_no_own_steps_and_runs_only_on_main(self):
        self.assertTrue(changed(lambda d: d["jobs"]["publish"].update(steps=[{"run": "gh api x"}])))
        self.assertTrue(changed(lambda d: d["jobs"]["publish"].pop("if")))

    def test_only_publish_uses_the_secret(self):
        self.assertTrue(changed(lambda d: d["jobs"]["collect"]["steps"].append({"env": {"T": "${{ secrets.STATISTICS_TOKEN }}"}})))

    def test_permissions_checkout_and_timeouts(self):
        self.assertTrue(changed(lambda d: d.update(permissions={"contents": "write"})))
        self.assertTrue(changed(lambda d: d["jobs"]["collect"].update(permissions={"contents": "write"})))
        self.assertTrue(changed(lambda d: d["jobs"]["collect"].pop("timeout-minutes")))
        self.assertTrue(changed(lambda d: d["jobs"]["collect"]["steps"][0]["with"].update(**{"persist-credentials": True})))


if __name__ == "__main__":
    unittest.main()
