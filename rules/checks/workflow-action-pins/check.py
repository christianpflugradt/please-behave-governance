"""Require every remote action and reusable workflow in the repository's workflows to be pinned to a full commit SHA."""

import json
import os
import re
import subprocess
import sys


# Imported rule resources must remain immutable.
sys.dont_write_bytecode = True

SNAPSHOTS = {"pre-commit": "worktree", "check": "worktree", "pre-push": "head"}
WORKFLOWS = ".github/workflows/"
PINNED = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


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


def command(*args, data=None):
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed (exit {result.returncode}): {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def workflow_paths(selected):
    """Workflow files directly below .github/workflows in the selected snapshot, the only ones GitHub runs."""
    if selected == "worktree":
        raw = command("git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", WORKFLOWS)
    else:
        raw = command("git", "ls-tree", "-rz", "--name-only", "HEAD", "--", WORKFLOWS)
    paths = {os.fsdecode(path) for path in raw.split(b"\0") if path}
    if selected == "worktree":
        paths = {path for path in paths if os.path.lexists(path)}
    return sorted(p for p in paths if p.endswith((".yml", ".yaml")) and "/" not in p[len(WORKFLOWS) :])


def read(selected, path):
    if selected == "head":
        return command("git", "show", f"HEAD:{path}")
    if os.path.islink(path) or not os.path.isfile(path):
        raise RuntimeError(f"{path} is not a regular file")
    with open(path, "rb") as handle:
        return handle.read()


def parse(content):
    document = json.loads(command("yq", "--output-format=json", ".", data=content) or b"null")
    if document is not None and not isinstance(document, dict):
        raise ValueError("a workflow must be a mapping")
    return document or {}


def references(document):
    """Yield (job id, reference) for every reusable workflow and every step action."""
    jobs = document.get("jobs")
    for job_id, job in jobs.items() if isinstance(jobs, dict) else []:
        if not isinstance(job, dict):
            continue
        if "uses" in job:
            yield job_id, str(job["uses"])
        steps = job.get("steps")
        for step in steps if isinstance(steps, list) else []:
            if isinstance(step, dict) and "uses" in step:
                yield job_id, str(step["uses"])


def violations(path, document):
    return [
        f"{path} job {job_id} uses {reference}, which is not pinned to a full commit SHA"
        for job_id, reference in references(document)
        if not reference.startswith("./") and not PINNED.match(reference)
    ]


def main():
    if len(sys.argv) != 2:
        raise ValueError("expected one argument: context file")
    point = execution_point(sys.argv[1])
    selected = select_snapshot(point)
    paths = workflow_paths(selected)
    found = [message for path in paths for message in violations(path, parse(read(selected, path)))]
    for message in found:
        print(f"FAIL: {message}")
    if not found:
        print(f"PASS: all remote actions are pinned to a full commit SHA in {len(paths)} workflows ({point})")
    return 1 if found else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:  # A crash is a technical failure, never a FAIL verdict.
        print(f"ERROR: unexpected {type(exc).__name__}", file=sys.stderr)
        sys.exit(2)
