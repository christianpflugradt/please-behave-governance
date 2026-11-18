"""Workflow action pins: which references count as pinned."""

import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("check", Path(__file__).with_name("check.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SHA = "0123456789abcdef0123456789abcdef01234567"


def workflow(*uses, reusable=None):
    job = {"steps": [{"uses": reference} for reference in uses]}
    if reusable:
        job = {"uses": reusable}
    return {"jobs": {"build": job}}


class PinTests(unittest.TestCase):
    def test_full_sha_and_local_references_pass(self):
        document = workflow(f"actions/checkout@{SHA}", "./.github/actions/local")
        self.assertEqual(mod.violations("ci.yml", document), [])

    def test_tags_and_branches_fail(self):
        for reference in ["actions/checkout@v4", "actions/checkout@main", f"actions/checkout@{SHA[:39]}"]:
            self.assertEqual(len(mod.violations("ci.yml", workflow(reference))), 1, reference)

    def test_reusable_workflows_are_checked(self):
        self.assertTrue(mod.violations("ci.yml", workflow(reusable="org/repo/.github/workflows/w.yml@v1")))

    def test_workflows_without_jobs_pass(self):
        self.assertEqual(mod.violations("ci.yml", {}), [])
