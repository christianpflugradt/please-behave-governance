"""Require the shared structure of release.yml: the shared plan and readiness workflows, a candidate job and a publish job."""

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


GOVERNANCE = "christianpflugradt/please-behave-governance"
PLAN = ".github/workflows/release-plan.yml"
READINESS = ".github/workflows/release-readiness.yml"
ACTIONS = "actions/release"
JOBS = {"plan", "release-readiness", "release-candidate", "publish"}
FORBIDDEN_FILES = ("release.config.*", ".releaserc*")


def governed(step_or_job, path):
    """True when the job or step uses the shared workflow or action of the governance repository, pinned to a full commit SHA."""
    return re.fullmatch(rf"{re.escape(GOVERNANCE)}/{re.escape(path)}@[0-9a-f]{{40}}( #.*)?", str(step_or_job.get("uses", ""))) is not None


def needs(job):
    return set(as_list(job.get("needs")))


def operation(step, name):
    return uses_governance(step, f"{ACTIONS}/evidence") and with_value(step, "operation") == name


def uses_governance(step, path):
    return str(step.get("uses", "")).startswith(f"{GOVERNANCE}/{path}@")


def has_step(job, test):
    return any(test(step) for step in steps(job))


def plan_violations(job):
    found = []
    if not governed(job, PLAN):
        found.append(f"job plan must use the shared workflow {PLAN} pinned to a full commit SHA")
    if job.get("if") != "github.ref == 'refs/heads/main'":
        found.append("job plan must run only on refs/heads/main")
    if job.get("permissions") != {"contents": "write"}:
        found.append("job plan must have exactly contents: write")
    return found


def readiness_violations(job):
    found = []
    if not governed(job, READINESS):
        found.append(f"job release-readiness must use the shared workflow {READINESS} pinned to a full commit SHA")
    if needs(job) != {"plan"}:
        found.append("job release-readiness must need plan only, so that it runs in parallel with the candidate")
    if job.get("permissions") != {"contents": "read", "actions": "read"}:
        found.append("job release-readiness must have exactly contents: read and actions: read")
    if with_value(job, "source-sha") != "${{ needs.plan.outputs.source_sha }}":
        found.append("job release-readiness must pass the source_sha output of plan as source-sha")
    return found


def candidate_violations(job):
    found = []
    if needs(job) != {"plan"}:
        found.append("job release-candidate must need plan only, so that it runs in parallel with release-readiness")
    found += expect_steps(
        "release-candidate",
        job,
        [
            (f"{ACTIONS}/governance pinned to a full commit SHA", lambda s: uses_governance(s, f"{ACTIONS}/governance")),
            ("the evidence action with operation record", lambda s: operation(s, "record")),
        ],
    )
    return found


def publish_violations(job):
    found = []
    if needs(job) != {"plan", "release-readiness", "release-candidate"}:
        found.append("job publish must need plan, release-readiness and release-candidate")
    condition = str(job.get("if", ""))
    for name in ("release-readiness", "release-candidate"):
        if f"needs.{name}.result == 'success'" not in condition:
            found.append(f"job publish must run only when {name} succeeded")
    publishing = [
        s for s in steps(job) if uses_governance(s, f"{ACTIONS}/publish-release") or uses_governance(s, f"{ACTIONS}/publish-image")
    ]
    if len(publishing) != 1:
        found.append("job publish must publish exactly once, with publish-release (files) or publish-image (images)")
    found += expect_steps(
        "publish",
        job,
        [
            ("the evidence action with operation verify", lambda s: operation(s, "verify")),
            (
                "publish-release or publish-image",
                lambda s: uses_governance(s, f"{ACTIONS}/publish-release") or uses_governance(s, f"{ACTIONS}/publish-image"),
            ),
        ],
    )
    writes = {key for key, value in as_dict(job.get("permissions")).items() if value == "write"}
    if "contents" not in writes or writes - {"contents", "packages"}:
        found.append("job publish must have contents: write, and packages: write only when it publishes images")
    return found


def violations(document):
    found = []
    if document.get("name") != "Release":
        found.append('name must be "Release"')
    events = triggers(document)
    if "workflow_dispatch" not in events:
        found.append("must run on workflow_dispatch")
    if {"push", "pull_request", "pull_request_target"} & set(events):
        found.append("must not run on push or pull requests")
    concurrency = as_dict(document.get("concurrency"))
    if concurrency.get("group") != "release-main" or flag(concurrency.get("cancel-in-progress")) != "false":
        found.append("concurrency must be group release-main without cancel-in-progress")
    listed = jobs(document)
    if set(listed) != JOBS:
        found.append(f"jobs must be exactly {', '.join(sorted(JOBS))}")
        return found + common(document)
    found += plan_violations(listed["plan"]) + readiness_violations(listed["release-readiness"])
    found += candidate_violations(listed["release-candidate"]) + publish_violations(listed["publish"])
    for job_id, job in listed.items():
        for step in steps(job):
            if re.search(r"semantic-release|npm run release", str(step.get("run", ""))):
                found.append(f"job {job_id} must not run Semantic Release itself; the shared actions do")
            if str(step.get("uses", "")).startswith(GOVERNANCE) and not re.search(r"@[0-9a-f]{40}", str(step["uses"])):
                found.append(f"job {job_id} uses a governance action without a full commit SHA")
    return found + common(document)


def repository_violations(selected):
    """Own release machinery next to the shared one, and the missing artifact policy."""
    found = []
    for pattern in FORBIDDEN_FILES:
        for name in tracked_names(selected):
            if fnmatch.fnmatchcase(name, pattern):
                found.append(f"{name} must not exist; the shared release actions own the Semantic Release policy")
    package = read_file(selected, "package.json")
    if package is not None and "release" in as_dict(json.loads(package)):
        found.append('package.json must not have a "release" key; the shared release actions own the Semantic Release policy')
    policy = read_file(selected, ".github/release-artifacts.json")
    if policy is None:
        found.append(".github/release-artifacts.json is missing")
    else:
        data = json.loads(policy)
        if not isinstance(data, dict) or set(data) != {"root", "targets"} or not isinstance(data["targets"], dict) or not data["targets"]:
            found.append('.github/release-artifacts.json must be an object with "root" and a non-empty "targets"')
    return found


def tracked_names(selected):
    """Root file names of the selected snapshot."""
    if selected == "head":
        raw = subprocess.run(["git", "ls-tree", "-z", "--name-only", "HEAD"], capture_output=True, check=False)
    else:
        raw = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", ":(top,glob)*"], capture_output=True, check=False
        )
    if raw.returncode:
        raise RuntimeError(f"git failed (exit {raw.returncode})")
    return sorted({os.fsdecode(path) for path in raw.stdout.split(b"\0") if path and b"/" not in path})


def main():
    if len(sys.argv) != 2:
        raise ValueError("expected argument: context file")
    status = run(sys.argv, "release.yml", violations)
    point = execution_point(sys.argv[1])
    found = repository_violations(select_snapshot(point))
    for message in found:
        print(f"FAIL: {message}")
    return 1 if (status or found) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # A crash is a technical failure, never a FAIL verdict.
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(2)
