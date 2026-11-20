"""Offline tests for the normalizers of the ecosystem audit actions and their recording."""

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
import audit_report
import cargo_audit
import npm_audit
import owasp_audit
import python_audit

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
        {"fileName": "right-2.0.jar", "vulnerabilities": [{"name": "CVE-2099-1", "severity": "HIGH"}]},
        {"fileName": "clean.jar"},
    ]
}


class NormalizationTests(unittest.TestCase):
    def test_cargo_findings_keep_aliases_severity_and_merge_packages(self):
        found = {f["id"]: f for f in cargo_audit.cargo_findings(json.dumps(CARGO))}
        self.assertEqual(found["RUSTSEC-2099-0001"]["severity"], "critical")
        self.assertEqual(found["RUSTSEC-2099-0001"]["aliases"], ["CVE-2099-1", "GHSA-aaaa-bbbb-cccc"])
        self.assertEqual(found["RUSTSEC-2099-0001"]["packages"], ["left 1.0.0", "left 1.1.0"])
        self.assertEqual(found["RUSTSEC-2099-0002"]["severity"], "unknown")

    def test_npm_findings_use_ghsa_ids_skip_propagated_entries_and_map_severity(self):
        found = {f["id"]: f for f in npm_audit.npm_findings(json.dumps(NPM))}
        self.assertEqual(set(found), {"GHSA-aaaa-bbbb-cccc", "npm:7"})
        self.assertEqual(found["GHSA-aaaa-bbbb-cccc"]["severity"], "high")
        self.assertEqual(found["npm:7"]["severity"], "medium")

    def test_pip_findings_are_unknown_severity_and_merge_packages(self):
        found = python_audit.pip_findings(json.dumps(PIP))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], "unknown")
        self.assertEqual(found[0]["aliases"], ["CVE-2099-9", "GHSA-xxxx"])
        self.assertEqual(found[0]["packages"], ["pyyaml 5.3", "pyyaml-old 1"])
        self.assertEqual(found[0]["description"], "Flaw")

    def test_owasp_findings_keep_severity_fall_back_and_merge_packages(self):
        found = {f["id"]: f for f in owasp_audit.owasp_findings(json.dumps(OWASP))}
        self.assertEqual(set(found), {"CVE-2099-1", "CVE-2099-2", "sonatype-2099-3"})
        self.assertEqual(found["CVE-2099-1"]["severity"], "high")
        self.assertEqual(found["CVE-2099-1"]["packages"], ["pkg:maven/org.example/left@1.0", "right-2.0.jar"])
        self.assertEqual(found["CVE-2099-2"]["severity"], "medium")
        self.assertEqual(found["sonatype-2099-3"]["severity"], "unknown")

    def test_owasp_report_without_dependencies_is_not_an_empty_result(self):
        with self.assertRaises(KeyError):
            owasp_audit.owasp_findings(json.dumps({}))

    def test_npm_error_document_is_not_an_empty_result(self):
        with self.assertRaises(ValueError):
            npm_audit.npm_findings(json.dumps({"error": {"code": "ENOTFOUND"}}))

    def test_cvss_severity_bands(self):
        low = "CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N"
        medium = "CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:L/I:L/A:N"
        self.assertEqual(cargo_audit.cvss3_severity(low), "low")
        self.assertEqual(cargo_audit.cvss3_severity(medium), "medium")
        self.assertEqual(cargo_audit.cvss3_severity("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H"), "critical")
        self.assertIsNone(cargo_audit.cvss3_severity("CVSS:4.0/AV:N"))
        self.assertIsNone(cargo_audit.cvss3_severity(None))


class ActionTests(unittest.TestCase):
    """Run each action's entry point against faked tools and read what it recorded."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.dict(os.environ, {"RUNNER_TEMP": self.temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def recorded(self, name):
        with open(os.path.join(self.temp.name, "dependency-audit", name)) as handle:
            return json.load(handle)

    def tool(self, outputs, code=1):
        calls = []

        def fake(command, cwd, **_):
            calls.append((command[0], cwd))
            return subprocess.CompletedProcess(command, code, stdout=outputs[command[0]], stderr=b"")

        return calls, patch.object(audit_report.subprocess, "run", side_effect=fake)

    def test_each_directory_is_one_scan_and_findings_do_not_fail_the_step(self):
        calls, fake = self.tool({"npm": json.dumps(NPM).encode(), "cargo": json.dumps(CARGO).encode()})
        with fake, contextlib.redirect_stdout(io.StringIO()):
            codes = [npm_audit.main([]), npm_audit.main(["--directory", "renderer"]), cargo_audit.main(["--directory", "backend"])]
        self.assertEqual(codes, [0, 0, 0])
        self.assertEqual(calls, [("npm", "."), ("npm", "renderer"), ("cargo", "backend")])
        self.assertEqual(len(self.recorded("npm.json")["findings"]), 2)
        self.assertEqual(self.recorded("npm-renderer.json")["name"], "npm renderer")
        self.assertEqual(self.recorded("cargo-backend.json")["status"], "complete")

    def test_an_unparsable_or_unexpected_tool_result_is_recorded_as_error(self):
        _, fake = self.tool({"npm": b"not json"})
        with fake, contextlib.redirect_stdout(io.StringIO()):
            npm_audit.main([])
        self.assertEqual(self.recorded("npm.json")["status"], "error")
        _, fake = self.tool({"cargo": json.dumps(CARGO).encode()}, code=3)
        with fake, contextlib.redirect_stdout(io.StringIO()):
            cargo_audit.main([])
        self.assertEqual(self.recorded("cargo.json")["status"], "error")

    def test_python_audits_the_exported_lock_without_resolving(self):
        calls, fake = self.tool({"uv": b"pyyaml==5.3\n", "pip-audit": json.dumps(PIP).encode()}, code=0)
        with fake, contextlib.redirect_stdout(io.StringIO()):
            python_audit.main(["--directory", "tool"])
        self.assertEqual(calls, [("uv", "tool"), ("pip-audit", ".")])
        self.assertEqual(self.recorded("python-tool.json")["status"], "complete")

    def test_python_with_an_outdated_lock_is_an_error(self):
        _, fake = self.tool({"uv": b""}, code=1)
        with fake, contextlib.redirect_stdout(io.StringIO()):
            python_audit.main([])
        self.assertEqual(self.recorded("python.json")["status"], "error")

    def test_an_owasp_report_is_read_and_a_missing_one_is_an_error(self):
        report = os.path.join(self.temp.name, "owasp.json")
        with open(report, "w") as handle:
            json.dump(OWASP, handle)
        with contextlib.redirect_stdout(io.StringIO()):
            owasp_audit.main(["--report", report])
        self.assertEqual((self.recorded("owasp.json")["status"], len(self.recorded("owasp.json")["findings"])), ("complete", 3))
        os.remove(os.path.join(self.temp.name, "dependency-audit", "owasp.json"))
        with contextlib.redirect_stdout(io.StringIO()):
            owasp_audit.main(["--report", os.path.join(self.temp.name, "missing.json")])
        self.assertEqual(self.recorded("owasp.json")["status"], "error")


if __name__ == "__main__":
    unittest.main()
