"""Violation detection of the dependency-audit-workflow check on parsed workflows."""

import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent / "check.py"
spec = importlib.util.spec_from_file_location("dependency_audit_workflow", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SHA = "0123456789abcdef0123456789abcdef01234567"
WORKFLOW = {
    "name": "Dependency Audit",
    "on": {"schedule": [{"cron": "17 3 * * *"}], "workflow_dispatch": None},
    "permissions": {"contents": "read"},
    "env": {"AUDIT_FAIL_ON": "medium"},
    "jobs": {
        "audit": {
            "runs-on": "ubuntu-24.04",
            "timeout-minutes": 30,
            "steps": [{"uses": f"actions/checkout@{SHA}", "with": {"persist-credentials": False}}],
        }
    },
}


def changed(mutate):
    document = copy.deepcopy(WORKFLOW)
    mutate(document)
    return mod.violations(document)


class DependencyAuditWorkflowTests(unittest.TestCase):
    def test_standard_workflow_passes(self):
        self.assertEqual(mod.violations(WORKFLOW), [])

    def test_schedule_must_be_one_daily_early_fixed_time_off_the_hour(self):
        for cron in (
            "0 3 * * *",
            "17 5 * * *",
            "17 * * * *",
            "*/15 3 * * *",
            "17 3 * * 1,3",
            "17 3 1 * *",
            "17 3 * 6 1",
            "17 3 * * MON",
            "17 3 *",
        ):
            self.assertTrue(changed(lambda d, c=cron: d["on"].update(schedule=[{"cron": c}])), cron)
        self.assertTrue(changed(lambda d: d["on"].update(schedule=[{"cron": "17 3 * * *"}, {"cron": "41 2 * * *"}])))
        self.assertTrue(changed(lambda d: d["on"].pop("schedule")))

    def test_weekly_schedule_passes(self):
        self.assertEqual(changed(lambda d: d["on"].update(schedule=[{"cron": "41 2 * * 1"}])), [])

    def test_dispatch_is_required_and_change_triggers_fail(self):
        self.assertTrue(changed(lambda d: d["on"].pop("workflow_dispatch")))
        for event in ("push", "pull_request", "pull_request_target"):
            self.assertTrue(changed(lambda d, e=event: d["on"].update({e: None})), event)

    def test_threshold_must_be_a_known_literal(self):
        for value in ("critical", "low", "high"):
            self.assertEqual(changed(lambda d, v=value: d["env"].update(AUDIT_FAIL_ON=v)), [])
        for value in ("none", "Medium", "${{ vars.LEVEL }}"):
            self.assertTrue(changed(lambda d, v=value: d["env"].update(AUDIT_FAIL_ON=v)), value)
        self.assertTrue(changed(lambda d: d.pop("env")))

    def test_audit_job_naming(self):
        self.assertEqual(changed(lambda d: d["jobs"].update({"audit-npm": d["jobs"].pop("audit")})), [])
        self.assertTrue(changed(lambda d: d["jobs"].update({"scan": d["jobs"].pop("audit")})))

    def test_write_permissions_and_checkout_credentials_fail(self):
        self.assertTrue(changed(lambda d: d["jobs"]["audit"].update(permissions={"issues": "write"})))
        self.assertTrue(changed(lambda d: d["jobs"]["audit"]["steps"][0].pop("with")))
        self.assertTrue(changed(lambda d: d.update(permissions={"contents": "write"})))

    def test_name_timeout_and_pinning_fail(self):
        self.assertTrue(changed(lambda d: d.update(name="Audit")))
        self.assertTrue(changed(lambda d: d["jobs"]["audit"].pop("timeout-minutes")))
        self.assertTrue(changed(lambda d: d["jobs"]["audit"]["steps"][0].update(uses="actions/checkout@v7")))


class ManifestTests(unittest.TestCase):
    PATTERNS = ["package.json", "requirements*.txt", "Cargo.toml"]

    def test_manifests_are_found_in_every_directory_by_file_name(self):
        paths = ["README.md", "app/api/package.json", "requirements-dev.txt", "docs/requirements.md", "Cargo.lock", "x/cargo.toml"]
        self.assertEqual(mod.manifest_violations(paths, self.PATTERNS), ["app/api/package.json", "requirements-dev.txt"])

    def test_no_manifest_is_clean(self):
        self.assertEqual(mod.manifest_violations(["src/main.py", "notes.txt"], self.PATTERNS), [])

    def test_patterns_ignore_comments_and_blank_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifests.txt"
            path.write_text("# comment\n\npackage.json\n  go.mod  \n", encoding="utf-8")
            self.assertEqual(mod.read_patterns(path), ["package.json", "go.mod"])


if __name__ == "__main__":
    unittest.main()
