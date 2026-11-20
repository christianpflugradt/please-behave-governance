"""Violation detection of the release-workflow check on parsed workflows and repository files."""

import copy
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent / "check.py"
spec = importlib.util.spec_from_file_location("release_workflow", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SHA = "0123456789abcdef0123456789abcdef01234567"
G = "christianpflugradt/please-behave-governance"
WORKFLOW = {
    "name": "Release",
    "on": {"workflow_dispatch": {"inputs": {"force": {"type": "boolean"}}}},
    "permissions": {"contents": "read"},
    "concurrency": {"group": "release-main", "cancel-in-progress": False},
    "jobs": {
        "plan": {
            "if": "github.ref == 'refs/heads/main'",
            "permissions": {"contents": "write"},
            "uses": f"{G}/.github/workflows/release-plan.yml@{SHA} # updates/workflows/release",
            "with": {"force": "${{ inputs.force }}"},
        },
        "release-readiness": {
            "needs": "plan",
            "permissions": {"contents": "read", "actions": "read"},
            "uses": f"{G}/.github/workflows/release-readiness.yml@{SHA}",
            "with": {"source-sha": "${{ needs.plan.outputs.source_sha }}"},
        },
        "release-candidate": {
            "needs": "plan",
            "runs-on": "ubuntu-24.04",
            "timeout-minutes": 20,
            "steps": [
                {"uses": f"{G}/actions/release/governance@{SHA}"},
                {"uses": f"{G}/actions/release/evidence@{SHA}", "with": {"operation": "record", "version": "1.0.0"}},
            ],
        },
        "publish": {
            "needs": ["plan", "release-readiness", "release-candidate"],
            "if": "needs.release-readiness.result == 'success' && needs.release-candidate.result == 'success'",
            "runs-on": "ubuntu-24.04",
            "timeout-minutes": 10,
            "permissions": {"contents": "write"},
            "steps": [
                {"uses": f"{G}/actions/release/evidence@{SHA}", "with": {"operation": "verify", "version": "1.0.0"}},
                {"uses": f"{G}/actions/release/publish-release@{SHA}", "with": {"version": "1.0.0"}},
            ],
        },
    },
}


def changed(mutate):
    document = copy.deepcopy(WORKFLOW)
    mutate(document)
    return mod.violations(document)


class ReleaseWorkflowTests(unittest.TestCase):
    def test_standard_workflow_passes(self):
        self.assertEqual(mod.violations(WORKFLOW), [])

    def test_image_publication_passes_with_packages_write(self):
        def images(d):
            d["jobs"]["publish"]["permissions"] = {"contents": "write", "packages": "write"}
            d["jobs"]["publish"]["steps"][1]["uses"] = f"{G}/actions/release/publish-image@{SHA}"

        self.assertEqual(changed(images), [])

    def test_matrix_candidate_and_schedule_pass(self):
        def matrix(d):
            d["on"]["schedule"] = [{"cron": "23 3 * * 1"}]
            d["jobs"]["release-candidate"]["strategy"] = {"matrix": {"java": ["17", "21"]}}

        self.assertEqual(changed(matrix), [])

    def test_triggers_and_concurrency(self):
        self.assertTrue(changed(lambda d: d["on"].pop("workflow_dispatch")))
        self.assertTrue(changed(lambda d: d["on"].update(push={"branches": ["main"]})))
        self.assertTrue(changed(lambda d: d["concurrency"].update(**{"cancel-in-progress": True})))
        self.assertTrue(changed(lambda d: d["concurrency"].update(group="release")))
        self.assertTrue(changed(lambda d: d.update(name="Publish")))

    def test_jobs_are_exactly_the_standard_four(self):
        self.assertTrue(changed(lambda d: d["jobs"].pop("release-candidate")))
        self.assertTrue(changed(lambda d: d["jobs"].update(**{"release-smoke": {"runs-on": "x", "timeout-minutes": 1}})))

    def test_plan_and_readiness_use_the_pinned_shared_workflows(self):
        self.assertTrue(changed(lambda d: d["jobs"]["plan"].update(uses=f"{G}/.github/workflows/release-plan.yml@main")))
        self.assertTrue(changed(lambda d: d["jobs"]["plan"].pop("uses")))
        self.assertTrue(
            changed(lambda d: d["jobs"]["release-readiness"].update(uses=f"other/repo/.github/workflows/release-readiness.yml@{SHA}"))
        )
        self.assertTrue(changed(lambda d: d["jobs"]["plan"].update(permissions={"contents": "read"})))
        self.assertTrue(changed(lambda d: d["jobs"]["plan"].update(**{"if": "always()"})))
        self.assertTrue(changed(lambda d: d["jobs"]["release-readiness"]["with"].update(**{"source-sha": "abc"})))

    def test_readiness_and_candidate_run_in_parallel(self):
        self.assertTrue(changed(lambda d: d["jobs"]["release-candidate"].update(needs=["plan", "release-readiness"])))
        self.assertTrue(changed(lambda d: d["jobs"]["release-readiness"].update(needs=["plan", "release-candidate"])))

    def test_candidate_runs_governance_before_recording_evidence(self):
        self.assertTrue(changed(lambda d: d["jobs"]["release-candidate"]["steps"].pop(0)))
        self.assertTrue(changed(lambda d: d["jobs"]["release-candidate"]["steps"].reverse()))
        self.assertTrue(changed(lambda d: d["jobs"]["release-candidate"]["steps"][1]["with"].update(operation="verify")))

    def test_publish_needs_everything_verifies_and_publishes_once(self):
        self.assertTrue(changed(lambda d: d["jobs"]["publish"].update(needs=["plan", "release-candidate"])))
        self.assertTrue(changed(lambda d: d["jobs"]["publish"].update(**{"if": "needs.release-candidate.result == 'success'"})))
        self.assertTrue(changed(lambda d: d["jobs"]["publish"]["steps"].pop(0)))
        self.assertTrue(changed(lambda d: d["jobs"]["publish"]["steps"].reverse()))
        self.assertTrue(changed(lambda d: d["jobs"]["publish"]["steps"].pop()))
        self.assertTrue(changed(lambda d: d["jobs"]["publish"]["steps"].append(copy.deepcopy(d["jobs"]["publish"]["steps"][1]))))
        self.assertTrue(changed(lambda d: d["jobs"]["publish"].update(permissions={"contents": "write", "actions": "write"})))
        self.assertTrue(changed(lambda d: d["jobs"]["publish"].update(permissions={"contents": "read"})))

    def test_no_own_semantic_release_and_pins(self):
        self.assertTrue(changed(lambda d: d["jobs"]["publish"]["steps"].append({"run": "npx semantic-release"})))
        self.assertTrue(changed(lambda d: d["jobs"]["publish"]["steps"][1].update(uses=f"{G}/actions/release/publish-release@main")))

    def test_common_permissions_and_timeouts(self):
        self.assertTrue(changed(lambda d: d.update(permissions={"contents": "write"})))
        self.assertTrue(changed(lambda d: d["jobs"]["publish"].pop("timeout-minutes")))

    def test_repository_files(self):
        valid = '{"root": "dist", "targets": {"linux": ["app.tar.gz"]}}'
        for files, expected in (
            ({".github/release-artifacts.json": valid}, 0),
            ({}, 1),
            ({".github/release-artifacts.json": "[]"}, 1),
            ({".github/release-artifacts.json": '{"root": "dist", "targets": {}}'}, 1),
            ({".github/release-artifacts.json": valid, "release.config.mjs": ""}, 1),
            ({".github/release-artifacts.json": valid, ".releaserc.json": "{}"}, 1),
            ({".github/release-artifacts.json": valid, "package.json": '{"release": {}}'}, 1),
            ({".github/release-artifacts.json": valid, "package.json": '{"name": "x"}'}, 0),
        ):
            with self.subTest(files=sorted(files)), tempfile.TemporaryDirectory() as temporary:
                previous = os.getcwd()
                os.chdir(temporary)
                try:
                    for name, content in files.items():
                        path = Path(name)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(content)
                    os.system("git init -q .")
                    found = mod.repository_violations("worktree")
                finally:
                    os.chdir(previous)
                self.assertEqual(1 if found else 0, expected, found)


if __name__ == "__main__":
    unittest.main()
