"""Read an OWASP Dependency-Check JSON report that an earlier step produced and record the result."""

import argparse
import json
import sys

import audit_report

# Imported action resources must remain immutable.
sys.dont_write_bytecode = True


def owasp_findings(text):
    """Normalize a Dependency-Check JSON report; suppressed vulnerabilities are not part of `vulnerabilities`."""
    found = {}
    for dependency in json.loads(text)["dependencies"]:
        packages = dependency.get("packages") or []
        label = packages[0]["id"] if packages else dependency["fileName"]
        for vulnerability in dependency.get("vulnerabilities") or []:
            advisory_id = vulnerability["name"]
            severity = str(vulnerability.get("severity") or (vulnerability.get("cvssv3") or {}).get("baseSeverity") or "").lower()
            entry = found.setdefault(
                advisory_id,
                {
                    "id": advisory_id,
                    "aliases": [],
                    "severity": severity if severity in audit_report.SEVERITIES else "unknown",
                    "description": (vulnerability.get("description") or "").split("\n")[0][:200] or None,
                    "url": f"https://nvd.nist.gov/vuln/detail/{advisory_id}" if advisory_id.startswith("CVE-") else None,
                    "packages": [],
                },
            )
            if label not in entry["packages"]:
                entry["packages"].append(label)
    return list(found.values())


def read(path):
    with open(path, encoding="utf-8") as handle:
        return owasp_findings(handle.read())


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, help="OWASP Dependency-Check JSON report")
    args = parser.parse_args(argv)
    return audit_report.record(audit_report.report_directory(), "owasp", lambda: read(args.report))


if __name__ == "__main__":
    sys.exit(audit_report.guarded(main, sys.argv[1:]))
