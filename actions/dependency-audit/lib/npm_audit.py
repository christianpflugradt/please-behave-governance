"""Audit the lockfile of one npm directory and record the result."""

import argparse
import json
import sys

import audit_report

# Imported action resources must remain immutable.
sys.dont_write_bytecode = True

NPM_SEVERITIES = {"info": "low", "low": "low", "moderate": "medium", "high": "high", "critical": "critical"}


def npm_findings(output):
    """Normalize `npm audit --json`; entries that only propagate through dependencies add no advisory."""
    report = json.loads(output)
    if "error" in report:
        raise ValueError("npm audit reported an error")
    found = {}
    for name, vulnerability in report["vulnerabilities"].items():
        for via in vulnerability["via"]:
            if not isinstance(via, dict):
                continue
            url = via.get("url") or ""
            advisory_id = url.rsplit("/", 1)[-1] if "/advisories/GHSA-" in url else f"npm:{via['source']}"
            entry = found.setdefault(
                advisory_id,
                {
                    "id": advisory_id,
                    "aliases": [],
                    "severity": NPM_SEVERITIES.get(via.get("severity"), "unknown"),
                    "description": via.get("title") or None,
                    "url": url or None,
                    "packages": [],
                },
            )
            package = f"{via.get('name', name)} {via.get('range', '')}".strip()
            if package not in entry["packages"]:
                entry["packages"].append(package)
    return list(found.values())


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default=".", help="directory with a package-lock.json")
    args = parser.parse_args(argv)
    produce = lambda: npm_findings(audit_report.run_tool(["npm", "audit", "--json"], args.directory))
    return audit_report.record(audit_report.report_directory(), audit_report.label("npm", args.directory), produce)


if __name__ == "__main__":
    sys.exit(audit_report.guarded(main, sys.argv[1:]))
