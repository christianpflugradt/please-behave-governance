"""Offline tests for normalizing and gating dependency audit results."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("dependency_audit", Path(__file__).resolve().with_name("dependency_audit.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

CARGO = {
    "vulnerabilities": {
        "list": [
            {
                "advisory": {
                    "id": "RUSTSEC-2099-0001",
                    "title": "Example flaw",
                    "aliases": ["CVE-2099-1", "GHSA-aaaa-bbbb-cccc"],
                    "cvss": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                    "url": "https://rustsec.org/advisories/RUSTSEC-2099-0001",
                },
                "package": {"name": "left", "version": "1.0.0"},
            },
            {
                "advisory": {"id": "RUSTSEC-2099-0001", "title": "Example flaw", "aliases": [], "cvss": None},
                "package": {"name": "left", "version": "1.1.0"},
            },
            {"advisory": {"id": "RUSTSEC-2099-0002", "title": "Unrated", "cvss": None}, "package": {"name": "x", "version": "2"}},
        ]
    }
}
PIP = {
    "dependencies": [
        {
            "name": "pyyaml",
            "version": "5.3",
            "vulns": [{"id": "PYSEC-2099-1", "aliases": ["CVE-2099-9", "GHSA-xxxx"], "description": "Flaw\nmore"}],
        },
        {"name": "pyyaml-old", "version": "1", "vulns": [{"id": "PYSEC-2099-1", "aliases": []}]},
        {"name": "clean", "version": "2", "vulns": []},
        {"name": "skipped", "skip_reason": "not on PyPI"},
    ]
}
NPM = {
    "vulnerabilities": {
        "picomatch": {
            "via": [
                {
                    "source": 1,
                    "name": "picomatch",
                    "title": "Flaw",
                    "url": "https://github.com/advisories/GHSA-aaaa-bbbb-cccc",
                    "severity": "high",
                    "range": "<=4.0.7",
                }
            ]
        },
        "micromatch": {"via": ["picomatch"]},
        "old": {"via": [{"source": 7, "name": "old", "title": "Legacy", "severity": "moderate"}]},
    }
}


OWASP = {
    "dependencies": [
        {
            "fileName": "left-1.0.jar",
            "packages": [{"id": "pkg:maven/org.example/left@1.0"}],
            "vulnerabilities": [
                {"name": "CVE-2099-1", "severity": "HIGH", "description": "First line\nSecond line"},
                {"name": "CVE-2099-2", "cvssv3": {"baseSeverity": "MEDIUM"}},
                {"name": "sonatype-2099-3", "description": "Unrated"},
            ],
        },
        {
            "fileName": "right-2.0.jar",
            "vulnerabilities": [{"name": "CVE-2099-1", "severity": "HIGH"}],
        },
        {"fileName": "clean.jar"},
    ]
}


class NormalizationTests(unittest.TestCase):
    def test_cargo_findings_keep_aliases_severity_and_merge_packages(self):
        found = {f["id"]: f for f in mod.cargo_findings(json.dumps(CARGO))}
        self.assertEqual(found["RUSTSEC-2099-0001"]["severity"], "critical")
        self.assertEqual(found["RUSTSEC-2099-0001"]["aliases"], ["CVE-2099-1", "GHSA-aaaa-bbbb-cccc"])
        self.assertEqual(found["RUSTSEC-2099-0001"]["packages"], ["left 1.0.0", "left 1.1.0"])
        self.assertEqual(found["RUSTSEC-2099-0002"]["severity"], "unknown")

    def test_npm_findings_use_ghsa_ids_skip_propagated_entries_and_map_severity(self):
        found = {f["id"]: f for f in mod.npm_findings(json.dumps(NPM))}
        self.assertEqual(set(found), {"GHSA-aaaa-bbbb-cccc", "npm:7"})
        self.assertEqual(found["GHSA-aaaa-bbbb-cccc"]["severity"], "high")
        self.assertEqual(found["npm:7"]["severity"], "medium")

    def test_pip_findings_are_unknown_severity_and_merge_packages(self):
        found = mod.pip_findings(json.dumps(PIP))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], "unknown")
        self.assertEqual(found[0]["aliases"], ["CVE-2099-9", "GHSA-xxxx"])
        self.assertEqual(found[0]["packages"], ["pyyaml 5.3", "pyyaml-old 1"])
        self.assertEqual(found[0]["description"], "Flaw")

    def test_owasp_findings_keep_severity_fall_back_and_merge_packages(self):
        found = {f["id"]: f for f in mod.owasp_findings(json.dumps(OWASP))}
        self.assertEqual(set(found), {"CVE-2099-1", "CVE-2099-2", "sonatype-2099-3"})
        self.assertEqual(found["CVE-2099-1"]["severity"], "high")
        self.assertEqual(found["CVE-2099-1"]["packages"], ["pkg:maven/org.example/left@1.0", "right-2.0.jar"])
        self.assertEqual(found["CVE-2099-2"]["severity"], "medium")
        self.assertEqual(found["sonatype-2099-3"]["severity"], "unknown")

    def test_owasp_report_without_dependencies_is_not_an_empty_result(self):
        with self.assertRaises(KeyError):
            mod.owasp_findings(json.dumps({}))

    def test_npm_error_document_is_not_an_empty_result(self):
        with self.assertRaises(ValueError):
            mod.npm_findings(json.dumps({"error": {"code": "ENOTFOUND"}}))

    def test_cvss_severity_bands(self):
        low = "CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N"
        medium = "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:L/I:L/A:N"
        self.assertEqual(mod.cvss3_severity(low), "low")
        self.assertEqual(mod.cvss3_severity(medium), "medium")
        self.assertEqual(mod.cvss3_severity("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H"), "critical")
        self.assertIsNone(mod.cvss3_severity("CVSS:4.0/AV:N"))
        self.assertIsNone(mod.cvss3_severity(None))


class ScanAndGateTests(unittest.TestCase):
    def completed(self, stdout, code=1):
        return subprocess.CompletedProcess([], code, stdout=stdout.encode(), stderr=b"")

    def scan(self, outputs, npm=("."), cargo=("backend",)):
        calls = []

        def fake(command, cwd, **_):
            calls.append((command[0], cwd))
            return outputs[command[0]]

        with tempfile.TemporaryDirectory() as directory, patch.object(mod.subprocess, "run", side_effect=fake):
            path = os.path.join(directory, "report.json")
            code = mod.scan(path, list(npm), list(cargo))
            with open(path) as handle:
                return code, json.load(handle), calls

    def test_findings_do_not_fail_the_scan_but_tool_failures_do(self):
        code, written, _ = self.scan({"cargo": self.completed(json.dumps(CARGO)), "npm": self.completed("not json")})
        self.assertEqual(code, 2)
        self.assertEqual([item["status"] for item in written["scans"]], ["error", "complete"])
        self.assertEqual(len(written["scans"][1]["findings"]), 2)

    def test_complete_scans_with_findings_exit_zero_and_run_once_per_directory(self):
        outputs = {"cargo": self.completed(json.dumps(CARGO)), "npm": self.completed(json.dumps(NPM))}
        code, written, calls = self.scan(outputs, npm=(".", "renderer"))
        self.assertEqual(code, 0)
        self.assertEqual(calls, [("npm", "."), ("npm", "renderer"), ("cargo", "backend")])
        self.assertEqual([item["ecosystem"] for item in written["scans"]], ["npm", "npm renderer", "cargo backend"])

    def test_an_owasp_report_is_scanned_and_a_missing_one_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            owasp, report = os.path.join(directory, "owasp.json"), os.path.join(directory, "report.json")
            with open(owasp, "w") as handle:
                handle.write(json.dumps(OWASP))
            self.assertEqual(mod.scan(report, [], [], owasp_reports=[owasp]), 0)
            with open(report) as handle:
                self.assertEqual([(s["ecosystem"], len(s["findings"])) for s in json.load(handle)["scans"]], [("owasp", 3)])
            self.assertEqual(mod.scan(report, [], [], owasp_reports=[os.path.join(directory, "missing.json")]), 2)

    def test_an_explicit_empty_scan_records_an_empty_report(self):
        with tempfile.TemporaryDirectory() as directory:
            report = os.path.join(directory, "report.json")
            self.assertEqual(mod.main(["scan", "--report", report, "--none"]), 0)
            with open(report) as handle:
                self.assertEqual(json.load(handle), {"scans": []})
        for arguments in (["scan", "--report", "unused.json"], ["scan", "--report", "unused.json", "--none", "--npm", "."]):
            with self.assertRaises(SystemExit):
                mod.main(arguments)

    def test_unexpected_exit_status_is_a_scan_error(self):
        code, written, _ = self.scan({"npm": self.completed(json.dumps(NPM), code=3)}, cargo=())
        self.assertEqual((code, written["scans"][0]["status"]), (2, "error"))

    def test_threshold_and_unknown_severity(self):
        self.assertTrue(mod.reaches("medium", "medium"))
        self.assertFalse(mod.reaches("low", "medium"))
        self.assertTrue(mod.reaches("unknown", "critical"))

    def run_report(self, scans, threshold):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "report.json")
            summary = os.path.join(directory, "summary.md")
            with open(path, "w") as handle:
                json.dump({"scans": scans}, handle)
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": summary}):
                code = mod.report(path, threshold)
            with open(summary) as handle:
                return code, handle.read()

    def finding(self, severity):
        return {"id": "GHSA-x", "aliases": [], "severity": severity, "description": "a|b", "url": None, "packages": ["p 1"]}

    def test_report_gates_on_threshold_and_renders_summary(self):
        scans = [{"ecosystem": "npm", "status": "complete", "findings": [self.finding("medium")]}]
        code, text = self.run_report(scans, "medium")
        self.assertEqual(code, 1)
        self.assertIn("| medium | GHSA-x |", text)
        self.assertIn("a\\|b", text)
        self.assertEqual(self.run_report(scans, "high")[0], 0)

    def test_report_with_failed_scan_exits_two_even_without_findings(self):
        scans = [{"ecosystem": "cargo", "status": "error", "error": "boom", "findings": []}]
        code, text = self.run_report(scans, "low")
        self.assertEqual(code, 2)
        self.assertIn("scan failed", text)


if __name__ == "__main__":
    unittest.main()
