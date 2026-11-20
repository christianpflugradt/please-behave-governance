"""Audit the lockfile of one Cargo directory and record the result."""

import argparse
import json
import math
import sys

import audit_report

# Imported action resources must remain immutable.
sys.dont_write_bytecode = True


def severity_of(score):
    for limit, name in ((9.0, "critical"), (7.0, "high"), (4.0, "medium")):
        if score >= limit:
            return name
    return "low"


def roundup(value):
    whole = round(value * 100000)
    return whole / 100000 if whole % 10000 == 0 else (math.floor(whole / 10000) + 1) / 10


def cvss3_severity(vector):
    """Severity from a CVSS v3 base vector, or None when the vector is not a complete v3 vector."""
    try:
        parts = dict(item.split(":") for item in str(vector).split("/")[1:])
        if not str(vector).startswith(("CVSS:3.0/", "CVSS:3.1/")):
            return None
        scope_changed = parts["S"] == "C"
        weight = {"H": 0.56, "L": 0.22, "N": 0.0}
        privileges = {"N": 0.85, "L": 0.68 if scope_changed else 0.62, "H": 0.5 if scope_changed else 0.27}
        base = 1 - (1 - weight[parts["C"]]) * (1 - weight[parts["I"]]) * (1 - weight[parts["A"]])
        impact = 7.52 * (base - 0.029) - 3.25 * (base - 0.02) ** 15 if scope_changed else 6.42 * base
        exploit = (
            8.22
            * {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}[parts["AV"]]
            * {"L": 0.77, "H": 0.44}[parts["AC"]]
            * privileges[parts["PR"]]
            * {"N": 0.85, "R": 0.62}[parts["UI"]]
        )
    except (KeyError, ValueError):
        return None
    if impact <= 0:
        return "low"
    return severity_of(roundup(min((impact + exploit) * (1.08 if scope_changed else 1), 10)))


def cargo_findings(output):
    """Normalize `cargo audit --json`; warnings such as unmaintained crates are not vulnerabilities."""
    report = json.loads(output)
    found = {}
    for item in report["vulnerabilities"]["list"]:
        advisory, package = item["advisory"], item["package"]
        entry = found.setdefault(
            advisory["id"],
            {
                "id": advisory["id"],
                "aliases": sorted(advisory.get("aliases") or []),
                "severity": cvss3_severity(advisory.get("cvss")) or "unknown",
                "description": advisory.get("title") or None,
                "url": advisory.get("url") or None,
                "packages": [],
            },
        )
        entry["packages"].append(f"{package['name']} {package['version']}")
    return list(found.values())


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default=".", help="directory with a Cargo.lock")
    args = parser.parse_args(argv)
    produce = lambda: cargo_findings(audit_report.run_tool(["cargo", "audit", "--json"], args.directory))
    return audit_report.record(audit_report.report_directory(), audit_report.label("cargo", args.directory), produce)


if __name__ == "__main__":
    sys.exit(audit_report.guarded(main, sys.argv[1:]))
