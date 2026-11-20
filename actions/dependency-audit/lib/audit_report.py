"""The shared contract of the dependency audit actions: record one scan as a report file, judge all recorded reports.

Every audit action records one report file per scan in one directory; `report` reads that directory, renders the
GitHub step summary and fails when a scan did not complete or a finding reaches the threshold. This module knows no
ecosystem. Findings of unknown severity always reach the threshold.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys

# Imported action resources must remain immutable.
sys.dont_write_bytecode = True

SCHEMA = 1
SEVERITIES = ("low", "medium", "high", "critical")
FINDING_KEYS = ("id", "aliases", "severity", "description", "url", "packages")


def report_directory():
    return os.path.join(os.environ["RUNNER_TEMP"], "dependency-audit")


def label(tool, directory):
    return tool if directory == "." else f"{tool} {directory}"


def run_tool(command, directory, accepted=(0, 1)):
    """Run an audit tool and return its output; audit tools exit 1 when they found something, any other status is an error."""
    result = subprocess.run(command, cwd=directory, capture_output=True, check=False)
    if result.returncode not in accepted:
        raise RuntimeError(f"{command[0]} exited with {result.returncode}")
    if not result.stdout.strip():
        reason = result.stderr.decode(errors="replace").strip().splitlines()[:1]
        raise RuntimeError(f"{command[0]} produced no output (exit {result.returncode}){': ' + reason[0] if reason else ''}")
    return result.stdout


def document(name, status, findings, error=None):
    result = {"schema": SCHEMA, "name": name, "status": status, "findings": findings}
    if error:
        result["error"] = error
    return result


def write(path, content, mode):
    with open(path, mode, encoding="utf-8") as handle:
        json.dump(content, handle, indent=2)


def record(directory, name, produce):
    """Record one scan. A placeholder that says the scan did not complete is written first, so that a scan which dies
    is never missing and never clean; the real result replaces it. A scan that fails technically is recorded and
    judged by `report`, so the step itself stays green and later audit steps still run."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") + ".json")
    try:
        write(path, document(name, "error", [], "the scan did not complete"), "x")
    except FileExistsError:
        raise ValueError(f"the scan {name} is already recorded; call each audit action once per target") from None
    try:
        result = document(name, "complete", produce())
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        result = document(name, "error", [], f"{type(error).__name__}: {error}")
        print(f"::error title=Dependency audit::{name} could not be scanned: {result['error']}")
    write(path, result, "w")
    findings = len(result["findings"])
    print(f"{name}: {result['status']}, {findings} findings recorded.")
    return 0


def validate(content, path):
    """A report of another schema or shape is a technical error, never a clean result."""
    if not isinstance(content, dict) or content.get("schema") != SCHEMA:
        raise ValueError(f"{path} is not a dependency audit report of schema {SCHEMA}")
    if not isinstance(content.get("name"), str) or content.get("status") not in ("complete", "error"):
        raise ValueError(f"{path} has no name or a status other than complete or error")
    findings = content.get("findings")
    if not isinstance(findings, list):
        raise ValueError(f"{path} has no list of findings")
    for finding in findings:
        if not isinstance(finding, dict) or any(key not in finding for key in FINDING_KEYS):
            raise ValueError(f"{path} has a finding without all of: {', '.join(FINDING_KEYS)}")
        if finding["severity"] not in SEVERITIES + ("unknown",):
            raise ValueError(f"{path} has a finding with the unsupported severity {finding['severity']!r}")


def load(directory):
    paths = sorted(glob.glob(os.path.join(directory, "*.json")))
    if not paths:
        raise ValueError("no audit report was recorded; call at least one audit action before report")
    reports = []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            content = json.load(handle)
        validate(content, path)
        reports.append(content)
    return reports


def reaches(severity, threshold):
    return severity == "unknown" or SEVERITIES.index(severity) >= SEVERITIES.index(threshold)


def ordering(finding):
    severity = finding["severity"]
    return (severity != "unknown", -SEVERITIES.index(severity) if severity in SEVERITIES else 0, finding["id"])


def render(reports, threshold):
    lines = ["# Dependency audit", "", f"Fails from severity `{threshold}`; findings of unknown severity always fail.", ""]
    for item in reports:
        if item["status"] != "complete":
            lines.append(f"## {item['name']}: scan failed\n\n{item.get('error', 'unknown error')}\n")
            continue
        findings = sorted(item["findings"], key=ordering)
        lines.append(f"## {item['name']}: {len(findings)} findings\n")
        if findings:
            lines += ["| Severity | Advisory | Aliases | Packages | Description |", "| --- | --- | --- | --- | --- |"]
        for finding in findings:
            advisory = f"[{finding['id']}]({finding['url']})" if finding.get("url") else finding["id"]
            lines.append(
                "| {} | {} | {} | {} | {} |".format(
                    finding["severity"],
                    advisory,
                    ", ".join(finding["aliases"]) or "-",
                    "; ".join(finding["packages"]) or "-",
                    (finding.get("description") or "-").replace("|", "\\|"),
                )
            )
        lines.append("")
    return "\n".join(lines)


def report(directory, threshold):
    reports = load(directory)
    text = render(reports, threshold)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)
    if any(item["status"] != "complete" for item in reports):
        return 2
    blocking = [f["id"] for item in reports for f in item["findings"] if reaches(f["severity"], threshold)]
    if blocking:
        print(f"FAIL: {len(blocking)} findings at or above {threshold}: {', '.join(sorted(set(blocking)))}")
        return 1
    print(f"PASS: no findings at or above {threshold}")
    return 0


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("report")
    evaluate.add_argument("--fail-on", required=True, choices=SEVERITIES)
    evaluate.add_argument("--directory", default=None, help="directory of the recorded reports; defaults to the runner's")
    args = parser.parse_args(argv)
    return report(args.directory or report_directory(), args.fail_on)


def guarded(entry, argv):
    """Run an entry point; a crash is a technical failure, never a verdict about findings."""
    try:
        return entry(argv)
    except SystemExit:
        raise
    except Exception as error:
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(guarded(main, sys.argv[1:]))
