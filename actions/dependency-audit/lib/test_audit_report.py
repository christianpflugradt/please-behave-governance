"""Offline tests for recording scans and judging the recorded reports."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_report as mod


def finding(severity, identifier="GHSA-x"):
    return {"id": identifier, "aliases": [], "severity": severity, "description": "a|b", "url": None, "packages": ["p 1"]}


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = self.directory.name

    def read(self, name):
        with open(os.path.join(self.path, name)) as handle:
            return json.load(handle)

    def test_a_scan_is_recorded_with_its_findings_and_a_safe_file_name(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mod.record(self.path, "npm renderer/app", lambda: [finding("high")]), 0)
        content = self.read("npm-renderer-app.json")
        self.assertEqual((content["schema"], content["name"], content["status"]), (1, "npm renderer/app", "complete"))
        self.assertEqual(len(content["findings"]), 1)

    def test_a_failed_scan_is_recorded_as_error_and_the_step_stays_green(self):
        def broken():
            raise RuntimeError("npm exited with 3")

        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mod.record(self.path, "npm", broken), 0)
        content = self.read("npm.json")
        self.assertEqual((content["status"], content["findings"]), ("error", []))
        self.assertIn("npm exited with 3", content["error"])
        self.assertIn("::error", out.getvalue())

    def test_a_scan_that_dies_leaves_the_placeholder_behind(self):
        def dies():
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            mod.record(self.path, "npm", dies)
        self.assertEqual(self.read("npm.json")["status"], "error")

    def test_the_same_scan_cannot_be_recorded_twice(self):
        with contextlib.redirect_stdout(io.StringIO()):
            mod.record(self.path, "npm", list)
            with self.assertRaises(ValueError):
                mod.record(self.path, "npm", list)

    def test_run_tool_accepts_findings_status_and_rejects_others(self):
        def completed(code):
            return subprocess.CompletedProcess([], code, stdout=b"out", stderr=b"")

        with patch.object(mod.subprocess, "run", return_value=completed(1)):
            self.assertEqual(mod.run_tool(["tool"], "."), b"out")
        with patch.object(mod.subprocess, "run", return_value=completed(3)), self.assertRaises(RuntimeError):
            mod.run_tool(["tool"], ".")
        with patch.object(mod.subprocess, "run", return_value=completed(1)), self.assertRaises(RuntimeError):
            mod.run_tool(["tool"], ".", accepted=(0,))

    def test_a_tool_without_output_is_an_error_that_names_its_complaint(self):
        silent = subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"no version is set\nmore")
        with patch.object(mod.subprocess, "run", return_value=silent), self.assertRaisesRegex(RuntimeError, "no output.*no version is set"):
            mod.run_tool(["tool"], ".")


class ReportTests(unittest.TestCase):
    def run_report(self, documents, threshold, raw=()):
        with tempfile.TemporaryDirectory() as directory:
            for index, content in enumerate(documents):
                with open(os.path.join(directory, f"{index}.json"), "w") as handle:
                    json.dump(content, handle)
            for index, text in enumerate(raw):
                with open(os.path.join(directory, f"raw{index}.json"), "w") as handle:
                    handle.write(text)
            summary = os.path.join(directory, "summary.md")
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary}):
                code = mod.report(directory, threshold)
            with open(summary) as handle:
                return code, handle.read()

    def test_threshold_and_unknown_severity(self):
        self.assertTrue(mod.reaches("medium", "medium"))
        self.assertFalse(mod.reaches("low", "medium"))
        self.assertTrue(mod.reaches("unknown", "critical"))

    def test_report_gates_on_threshold_and_renders_summary(self):
        documents = [mod.document("npm", "complete", [finding("medium")])]
        code, text = self.run_report(documents, "medium")
        self.assertEqual(code, 1)
        self.assertIn("## npm: 1 findings", text)
        self.assertIn("| medium | GHSA-x |", text)
        self.assertIn("a\\|b", text)
        self.assertEqual(self.run_report(documents, "high")[0], 0)

    def test_findings_of_unknown_severity_always_fail(self):
        self.assertEqual(self.run_report([mod.document("python", "complete", [finding("unknown")])], "critical")[0], 1)

    def test_all_reports_are_judged_together(self):
        documents = [mod.document("npm", "complete", []), mod.document("cargo", "complete", [finding("high")])]
        code, text = self.run_report(documents, "high")
        self.assertEqual(code, 1)
        self.assertIn("## npm: 0 findings", text)
        self.assertIn("## cargo: 1 findings", text)

    def test_a_scan_that_did_not_complete_is_a_technical_error_even_without_findings(self):
        code, text = self.run_report([mod.document("cargo", "error", [], "boom")], "low")
        self.assertEqual(code, 2)
        self.assertIn("scan failed", text)

    def test_no_report_at_all_is_an_error_and_never_a_pass(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            mod.report(directory, "low")
        with self.assertRaises(ValueError):
            mod.report(os.path.join(tempfile.gettempdir(), "absent-dependency-audit-reports"), "low")

    def test_reports_of_another_schema_or_shape_are_errors(self):
        good = mod.document("npm", "complete", [])
        for bad in (
            {**good, "schema": 2},
            {**good, "status": "ok"},
            {**good, "findings": None},
            {**good, "findings": [{"id": "x"}]},
            {**good, "findings": [finding("severe")]},
            [],
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.run_report([bad], "low")
        with self.assertRaises(ValueError):
            self.run_report([], "low", raw=["not json"])

    def test_the_command_line_requires_a_valid_threshold(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            mod.main(["report", "--fail-on", "severe"])

    def test_a_crash_is_a_technical_error(self):
        def crash(_):
            raise OSError("disk")

        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(mod.guarded(crash, []), 2)


if __name__ == "__main__":
    unittest.main()
