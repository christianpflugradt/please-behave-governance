"""Audit the uv lockfile of one Python project directory and record the result."""

import argparse
import json
import os
import sys
import tempfile

import audit_report

# Imported action resources must remain immutable.
sys.dont_write_bytecode = True


def pip_findings(output):
    """Normalize `pip-audit --format json`; pip-audit reports no severity, so every finding is unknown."""
    found = {}
    for dependency in json.loads(output)["dependencies"]:
        for vulnerability in dependency.get("vulns") or []:
            advisory_id = vulnerability["id"]
            entry = found.setdefault(
                advisory_id,
                {
                    "id": advisory_id,
                    "aliases": sorted(vulnerability.get("aliases") or []),
                    "severity": "unknown",
                    "description": (vulnerability.get("description") or "").split("\n")[0][:200] or None,
                    "url": f"https://osv.dev/vulnerability/{advisory_id}",
                    "packages": [],
                },
            )
            entry["packages"].append(f"{dependency['name']} {dependency['version']}")
    return list(found.values())


def locked_findings(directory):
    """Audit exactly the locked versions: export the lock as pinned requirements and audit them without resolving anything."""
    exported = audit_report.run_tool(
        ["uv", "export", "--locked", "--no-hashes", "--no-emit-project", "--format", "requirements-txt"], directory, accepted=(0,)
    )
    with tempfile.TemporaryDirectory() as scratch:
        requirements = os.path.join(scratch, "requirements.txt")
        with open(requirements, "wb") as handle:
            handle.write(exported)
        command = [
            "pip-audit",
            "--requirement",
            requirements,
            "--no-deps",
            "--disable-pip",
            "--format",
            "json",
            "--progress-spinner",
            "off",
        ]
        return pip_findings(audit_report.run_tool(command, "."))


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default=".", help="directory with a pyproject.toml and a uv.lock")
    args = parser.parse_args(argv)
    return audit_report.record(
        audit_report.report_directory(), audit_report.label("python", args.directory), lambda: locked_findings(args.directory)
    )


if __name__ == "__main__":
    sys.exit(audit_report.guarded(main, sys.argv[1:]))
