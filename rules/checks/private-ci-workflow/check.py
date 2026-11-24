"""Require the lean CI workflow for private repositories: pull requests only, calling the shared CI workflow without reviews."""

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
        print(f"FAIL: {target} is missing")
        return 1
    found = violations(parse(content))
    for message in found:
        print(f"FAIL: {target} {message}")
    if not found:
        print(f"PASS: {target} follows the shared structure ({point})")
    return 1 if found else 0


RUNNER = re.compile(r"(?:ubuntu|macos|windows)-\d+(?:\.\d+)*")
EVENTS = {"pull_request", "push", "workflow_dispatch"}
GOVERNANCE = "christianpflugradt/repository-catalog"
CHECK = ".github/workflows/ci-check-private.yml"
GATEWAY_URL = {"GATEWAY_URL": "${{ secrets.GATEWAY_URL }}"}
GATEWAY_KEY = {"GATEWAY_KEY": "${{ secrets.GATEWAY_KEY }}"}


def governed(job, path):
    """True when the job calls the shared workflow of the governance repository, pinned to a full commit SHA."""
    return re.fullmatch(rf"{re.escape(GOVERNANCE)}/{re.escape(path)}@[0-9a-f]{{40}}( #.*)?", str(job.get("uses", ""))) is not None


def check_violations(job):
    """The caller job only calls the shared workflow, which runs no reviews, and hands over at most the gateway secrets."""
    found = []
    if not governed(job, CHECK):
        found.append(f"job check must use the shared workflow {CHECK} pinned to a full commit SHA")
    if "steps" in job:
        found.append("job check must not define its own steps; the shared workflow runs the checks")
    inputs = as_dict(job.get("with"))
    if inputs.get("runner") is not None and not RUNNER.fullmatch(str(inputs["runner"])):
        found.append(f"input runner must name a versioned runner such as ubuntu-26.04, not {inputs['runner']!r}")
    if as_dict(job.get("secrets")) not in ({}, GATEWAY_URL, GATEWAY_URL | GATEWAY_KEY):
        found.append(
            "job check may pass only GATEWAY_URL and, with the URL, GATEWAY_KEY as the secrets of the same name, "
            "because the reviews stay out of private CI"
        )
    if "matrix" in as_dict(job.get("strategy")):
        found.append("job check must not use a matrix")
    return found


def violations(document):
    found = []
    if document.get("name") != "CI":
        found.append('name must be "CI"')
    events = triggers(document)
    if "pull_request" not in events:
        found.append("must run on pull_request")
    extra = sorted(set(events) - EVENTS)
    if extra:
        found.append(f"may only run on pull_request, push to main and workflow_dispatch, not on {', '.join(extra)}")
    if "push" in events and as_list(as_dict(events["push"]).get("branches")) != ["main"]:
        found.append("push must be limited to branches: [main]")
    if flag(as_dict(document.get("concurrency")).get("cancel-in-progress")) != "true":
        found.append("requires concurrency with cancel-in-progress: true")
    job = jobs(document).get("check")
    if job is None:
        found.append("job check is missing")
    else:
        found += check_violations(job)
    return found + common(document)


def main():
    return run(sys.argv, "ci.yml", violations)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:  # A crash is a technical failure, never a FAIL verdict.
        print(f"ERROR: unexpected {type(exc).__name__}", file=sys.stderr)
        sys.exit(2)
