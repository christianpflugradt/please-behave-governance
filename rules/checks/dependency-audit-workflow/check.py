"""Require the shared structure of dependency-audit.yml, or no known dependency manifest when the repository declares none."""

import fnmatch
import json
import os
import re
import stat
import subprocess
from pathlib import Path, PurePosixPath
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
    """Rules every shared workflow follows: read-only default permissions and timeouts."""
    found = []
    if document.get("permissions") != {"contents": "read"}:
        found.append("top-level permissions must be exactly contents: read")
    for job_id, job in jobs(document).items():
        reusable = "uses" in job
        if "timeout-minutes" not in job and not reusable:
            found.append(f"job {job_id} sets no timeout-minutes")
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


GOVERNANCE_ACTION = re.compile(r"christianpflugradt/repository-catalog/actions/dependency-audit/([a-z0-9-]+)@([0-9a-f]{40})( #.*)?")


def is_audit(job_id):
    return job_id == "audit" or job_id.startswith("audit-")


def audit_action(step):
    """The name and the commit of the shared audit action a step calls, or None for any other step."""
    match = GOVERNANCE_ACTION.fullmatch(str(step.get("uses", "")))
    return (match.group(1), match.group(2)) if match else None


def audit_steps_violations(job_id, job):
    """The audit job calls at least one audit action and ends with the report, all pinned to one commit of the governance repository."""
    listed = steps(job)
    calls = [audit_action(step) for step in listed]
    if any(
        str(step.get("uses", "")).startswith("christianpflugradt/repository-catalog/actions/dependency-audit") and call is None
        for step, call in zip(listed, calls)
    ):
        return [f"job {job_id} must pin every dependency audit action to a full commit SHA"]
    found = []
    if len({call[1] for call in calls if call}) > 1:
        found.append(f"job {job_id} must call all dependency audit actions at the same commit")
    if not any(call and call[0] != "report" for call in calls):
        found.append(f"job {job_id} must call at least one audit action of actions/dependency-audit before the report")
    if not listed or not calls[-1] or calls[-1][0] != "report":
        found.append(f"job {job_id} must end with the report action of actions/dependency-audit")
    else:
        report = listed[-1]
        if "!cancelled()" not in str(report.get("if", "")).replace(" ", ""):
            found.append(f"job {job_id} must run the report with if: ${{{{ !cancelled() }}}}, so that it judges failed scans too")
        if with_value(report, "fail-on") != f"${{{{ env.{THRESHOLD} }}}}":
            found.append(f"the report of job {job_id} must take fail-on from ${{{{ env.{THRESHOLD} }}}}")
    if sum(1 for call in calls if call and call[0] == "report") > 1:
        found.append(f"job {job_id} must call the report action once")
    return found


def grants_write(job):
    return "write" in as_dict(job.get("permissions")).values()


def schedule_violations(events):
    """The audit runs daily or weekly at a fixed early time, at a minute off the busy top of the hour."""
    entries = as_list(as_dict(events).get("schedule"))
    if len(entries) != 1:
        return ["must have exactly one schedule entry"]
    fields = str(as_dict(entries[0]).get("cron", "")).split()
    if len(fields) != 5 or fields[2:4] != ["*", "*"] or not (fields[4] == "*" or re.fullmatch(r"[0-7]", fields[4])):
        return ["schedule must run daily or weekly: day of month and month are *, day of week is * or one weekday number"]
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
        if is_audit(job_id):
            found += audit_steps_violations(job_id, job)
        for step in steps(job):
            if uses(step, "actions/checkout") and flag(with_value(step, "persist-credentials")) != "false":
                found.append(f"job {job_id} must check out with persist-credentials: false")
    return found + common(document)


def tracked_paths(selected):
    """Paths of the selected snapshot in every directory."""
    if selected == "head":
        raw = subprocess.run(["git", "ls-tree", "-rz", "--name-only", "HEAD"], capture_output=True, check=False)
    else:
        raw = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], capture_output=True, check=False)
    if raw.returncode:
        raise RuntimeError(f"git failed (exit {raw.returncode})")
    paths = {os.fsdecode(path) for path in raw.stdout.split(b"\0") if path}
    return sorted(path for path in paths if selected == "head" or os.path.lexists(path))


def read_patterns(path):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def manifest_violations(paths, patterns):
    """Dependency manifests found anywhere in the project; matching is on the file name and case-sensitive."""
    return [path for path in paths if any(fnmatch.fnmatchcase(PurePosixPath(path).name, pattern) for pattern in patterns)]


def declared_none(context_file, patterns_file):
    point = execution_point(context_file)
    found = manifest_violations(tracked_paths(select_snapshot(point)), read_patterns(patterns_file))
    for path in found:
        print(
            f"FAIL: dependencies is none, but {ascii(path)} declares dependencies; remove the declaration and add .github/workflows/dependency-audit.yml"
        )
    if not found:
        print(f"PASS: no known dependency manifest ({point})")
    return 1 if found else 0


def main():
    if len(sys.argv) != 4:
        raise ValueError("expected arguments: context file, dependencies, manifests file")
    if sys.argv[2] == "audited":
        return run(sys.argv[:2], "dependency-audit.yml", violations)
    if sys.argv[2] == "none":
        return declared_none(sys.argv[1], sys.argv[3])
    raise ValueError("dependencies must be audited or none")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:  # A crash is a technical failure, never a FAIL verdict.
        print(f"ERROR: unexpected {type(exc).__name__}", file=sys.stderr)
        sys.exit(2)
