"""Require the shared structure of dependency-audit.yml; scanners, reports and cache handling stay with consumers."""

import json
import re
import stat
import subprocess
from pathlib import Path
import sys

# Imported rule resources must remain immutable.
sys.dont_write_bytecode = True

SNAPSHOTS = {"pre-commit": "worktree", "check": "worktree", "pre-push": "head"}


def execution_point(context_file):
    with open(context_file, encoding="utf-8") as handle:
        point = json.load(handle).get("execution_point")
    if not isinstance(point, str) or not point:
        raise ValueError("run context has no execution point")
    return point


def select_snapshot(point):
    if point not in SNAPSHOTS:
        raise ValueError(f"unsupported execution point: {point}")
    return SNAPSHOTS[point]


def read_file(selected, name):
    """Return the bytes of a root file in the selected snapshot, or None when it is absent."""
    if selected == "head":
        listed = subprocess.run(["git", "ls-tree", "-z", "HEAD", "--", f":(literal){name}"], capture_output=True, check=False)
        if listed.returncode != 0:
            raise RuntimeError(listed.stderr.decode(errors="replace").strip())
        if not listed.stdout:
            return None
        if listed.stdout.split(b" ", 1)[0] not in (b"100644", b"100755"):
            raise RuntimeError(f"{name} is not a regular file in HEAD")
        shown = subprocess.run(["git", "show", f"HEAD:{name}"], capture_output=True, check=False)
        if shown.returncode != 0:
            raise RuntimeError(shown.stderr.decode(errors="replace").strip())
        return shown.stdout
    path = Path.cwd().resolve() / name
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(mode):
        raise RuntimeError(f"{name} is not a regular file")
    return path.read_bytes()


PINNED = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def parse(content):
    """Return the workflow as a dict; invalid YAML or a non-mapping document is a technical failure."""
    result = subprocess.run(["yq", "--output-format=json", "."], input=content, capture_output=True)
    if result.returncode:
        raise ValueError(f"yq cannot parse the workflow: {result.stderr.decode(errors='replace').strip()}")
    document = json.loads(result.stdout)
    if not isinstance(document, dict):
        raise ValueError("the workflow is not a YAML mapping")
    return document


def flag(value):
    """Compare booleans and their quoted spellings alike."""
    return str(value).lower()


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def as_dict(value):
    return value if isinstance(value, dict) else {}


def triggers(document):
    """Map each trigger event to its configuration, whichever form `on` is written in."""
    declared = document.get("on")
    if isinstance(declared, dict):
        return declared
    return {event: None for event in as_list(declared) if isinstance(event, str)}


def jobs(document):
    return {name: as_dict(job) for name, job in as_dict(document.get("jobs")).items()}


def steps(job):
    return [as_dict(step) for step in as_list(job.get("steps"))]


def uses(step, action):
    """True when the step uses the action, whatever its version reference."""
    return str(step.get("uses", "")).split("@", 1)[0] == action


def with_value(step, key):
    return as_dict(step.get("with")).get(key)


def runs(step, command):
    return str(step.get("run", "")).strip() == command


def please_behave(step, point):
    return uses(step, "please-behave/action") and with_value(step, "execution-point") == point


def expect_steps(job_id, job, expected):
    """Report each expected (description, test) step missing after its predecessor; extra steps may interleave."""
    found, position = [], 0
    listed = steps(job)
    for description, test in expected:
        for index in range(position, len(listed)):
            if test(listed[index]):
                position = index + 1
                break
        else:
            found.append(f"job {job_id} lacks the step: {description}")
    return found


def common(document):
    """Rules every shared workflow follows: read-only default permissions, timeouts and pinned actions."""
    found = []
    if document.get("permissions") != {"contents": "read"}:
        found.append("top-level permissions must be exactly contents: read")
    for job_id, job in jobs(document).items():
        reusable = "uses" in job
        if "timeout-minutes" not in job and not reusable:
            found.append(f"job {job_id} sets no timeout-minutes")
        references = [job["uses"]] if reusable else []
        references += [step["uses"] for step in steps(job) if "uses" in step]
        for reference in references:
            reference = str(reference)
            if not reference.startswith("./") and not PINNED.match(reference):
                found.append(f"job {job_id} uses {reference}, which is not pinned to a full commit SHA")
    return found


def run(argv, name, violations):
    """Evaluate one workflow file of the snapshot selected by the execution point; return the exit status."""
    if len(argv) != 2:
        raise ValueError("expected argument: context file")
    point = execution_point(argv[1])
    target = f".github/workflows/{name}"
    content = read_file(select_snapshot(point), target)
    if content is None:
        print(f"FAIL: {target} does not exist ({point})")
        return 1
    found = violations(parse(content))
    for message in found:
        print(f"FAIL: {target} {message}")
    if not found:
        print(f"PASS: {target} follows the shared structure ({point})")
    return 1 if found else 0


SEVERITIES = ("low", "medium", "high", "critical")
THRESHOLD = "AUDIT_FAIL_ON"
LATEST_HOUR = 4  # 04:xx UTC is before 07:00 in Europe/Berlin in both summer and winter time.


def is_audit(job_id):
    return job_id == "audit" or job_id.startswith("audit-")


def grants_write(job):
    return "write" in as_dict(job.get("permissions")).values()


def schedule_violations(events):
    """The audit runs once a day at a fixed early time, at a minute off the busy top of the hour."""
    entries = as_list(as_dict(events).get("schedule"))
    if len(entries) != 1:
        return ["must have exactly one schedule entry"]
    fields = str(as_dict(entries[0]).get("cron", "")).split()
    if len(fields) != 5 or fields[2:] != ["*", "*", "*"]:
        return ["schedule must be a daily cron: day of month, month and day of week are *"]
    minute, hour = fields[0], fields[1]
    if not (minute.isdigit() and hour.isdigit()):
        return ["schedule must use a fixed hour and minute"]
    found = []
    if not 1 <= int(minute) <= 59:
        found.append("schedule minute must be 1 to 59, not the top of the hour")
    if int(hour) > LATEST_HOUR:
        found.append(f"schedule hour must be 0 to {LATEST_HOUR} UTC, before 07:00 in Europe/Berlin")
    return found


def violations(document):
    found = []
    if document.get("name") != "Dependency Audit":
        found.append('name must be "Dependency Audit"')
    events = triggers(document)
    found += schedule_violations(events)
    if "workflow_dispatch" not in events:
        found.append("must run on workflow_dispatch")
    if {"push", "pull_request", "pull_request_target"} & set(events):
        found.append("must not run on push or pull requests")
    threshold = as_dict(document.get("env")).get(THRESHOLD)
    if threshold not in SEVERITIES:
        found.append(f"top-level env {THRESHOLD} must be one of: {', '.join(SEVERITIES)}")
    listed = jobs(document)
    if not any(is_audit(job_id) for job_id in listed):
        found.append("a job audit or audit-<name> is missing")
    for job_id, job in listed.items():
        if grants_write(job):
            found.append(f"job {job_id} must not grant write permissions")
        for step in steps(job):
            if uses(step, "actions/checkout") and flag(with_value(step, "persist-credentials")) != "false":
                found.append(f"job {job_id} must check out with persist-credentials: false")
    return found + common(document)


def main():
    return run(sys.argv, "dependency-audit.yml", violations)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:  # A crash is a technical failure, never a FAIL verdict.
        print(f"ERROR: unexpected {type(exc).__name__}", file=sys.stderr)
        sys.exit(2)
