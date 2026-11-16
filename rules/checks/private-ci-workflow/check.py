"""Require the lean CI workflow for private repositories: pull requests only, bounded minutes, Ubuntu runners that Renovate maintains."""

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
        print(f"FAIL: {target} is missing")
        return 1
    found = violations(parse(content))
    for message in found:
        print(f"FAIL: {target} {message}")
    if not found:
        print(f"PASS: {target} follows the shared structure ({point})")
    return 1 if found else 0


RUNNER = re.compile(r"ubuntu-\d{2}\.\d{2}")
EVENTS = {"pull_request", "workflow_dispatch"}


def max_minutes(value):
    """Return the positive timeout ceiling from the rule parameter."""
    if not value.isdecimal() or int(value) < 1:
        raise ValueError("max_job_minutes must be a positive integer")
    return int(value)


def job_limits(job_id, job, ceiling):
    """Bound what one job can bill: its timeout, a versioned Ubuntu runner and no matrix."""
    found = []
    timeout = job.get("timeout-minutes")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout > ceiling:
        if "uses" not in job:
            found.append(f"job {job_id} must set timeout-minutes to at most {ceiling}")
    if "uses" not in job and not (isinstance(job.get("runs-on"), str) and RUNNER.fullmatch(job["runs-on"])):
        found.append(f"job {job_id} must run on a versioned Ubuntu runner such as ubuntu-24.04, not {job.get('runs-on')!r}")
    if "matrix" in as_dict(job.get("strategy")):
        found.append(f"job {job_id} must not use a matrix")
    return found


def violations(document, ceiling):
    found = []
    if document.get("name") != "CI":
        found.append('name must be "CI"')
    events = triggers(document)
    if "pull_request" not in events:
        found.append("must run on pull_request")
    extra = sorted(set(events) - EVENTS)
    if extra:
        found.append(f"may only run on pull_request and workflow_dispatch, not on {', '.join(extra)}")
    if flag(as_dict(document.get("concurrency")).get("cancel-in-progress")) != "true":
        found.append("requires concurrency with cancel-in-progress: true")
    job = jobs(document).get("check")
    if job is None:
        found.append("job check is missing")
    else:
        found += expect_steps(
            "check",
            job,
            [
                (
                    "actions/checkout with persist-credentials: false",
                    lambda s: uses(s, "actions/checkout") and flag(with_value(s, "persist-credentials")) == "false",
                ),
                ("jdx/mise-action without install_args", lambda s: uses(s, "jdx/mise-action") and with_value(s, "install_args") is None),
                (
                    "please-behave/action with execution-point: check, install: false, sync: true and checks-only: true",
                    lambda s: (
                        please_behave(s, "check")
                        and flag(with_value(s, "install")) == "false"
                        and flag(with_value(s, "sync")) == "true"
                        and flag(with_value(s, "checks-only")) == "true"
                    ),
                ),
            ],
        )
    for job_id, each in jobs(document).items():
        found += job_limits(job_id, each, ceiling)
    return found + common(document)


def main():
    if len(sys.argv) != 3:
        raise ValueError("expected arguments: context file, max_job_minutes")
    ceiling = max_minutes(sys.argv[2])
    return run(sys.argv[:2], "ci.yml", lambda document: violations(document, ceiling))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:  # A crash is a technical failure, never a FAIL verdict.
        print(f"ERROR: unexpected {type(exc).__name__}", file=sys.stderr)
        sys.exit(2)
