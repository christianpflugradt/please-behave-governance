"""Require ci.yml to call the shared CI workflow; triggers and the runner input, also as a matrix, stay with consumers."""

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


RUNNER = re.compile(r"(?:ubuntu|macos|windows)-\d+(?:\.\d+)*(?:-[a-z0-9]+)?")


GOVERNANCE = "christianpflugradt/repository-catalog"
CHECK = ".github/workflows/ci-check.yml"
GEMINI = {"GEMINI_API_KEY": "${{ secrets.GEMINI_API_KEY }}"}
GATEWAY = {"GATEWAY_URL": "${{ secrets.GATEWAY_URL }}", "GATEWAY_KEY": "${{ secrets.GATEWAY_KEY }}"}


def governed(job, path):
    """True when the job calls the shared workflow of the governance repository, pinned to a full commit SHA."""
    return re.fullmatch(rf"{re.escape(GOVERNANCE)}/{re.escape(path)}@[0-9a-f]{{40}}( #.*)?", str(job.get("uses", ""))) is not None


def runner_violations(job):
    """The input runner names a versioned runner, or a matrix key of the job that lists only versioned runners."""
    runner = as_dict(job.get("with")).get("runner")
    if runner is None:
        return []
    key = re.fullmatch(r"\$\{\{ matrix\.([A-Za-z_][A-Za-z0-9_-]*) \}\}", str(runner))
    if key is None:
        if RUNNER.fullmatch(str(runner)):
            return []
        return [f"input runner must name a versioned runner such as ubuntu-26.04, not {runner!r}"]
    listed = as_list(as_dict(as_dict(job.get("strategy")).get("matrix")).get(key.group(1)))
    if listed and all(RUNNER.fullmatch(str(entry)) for entry in listed):
        return []
    return [f"matrix {key.group(1)} must list versioned runners such as ubuntu-26.04"]


def check_violations(job):
    """The caller job only calls the shared workflow, which runs the reviews, and hands over the key of their model API and optionally the gateway secrets."""
    found = []
    if not governed(job, CHECK):
        found.append(f"job check must use the shared workflow {CHECK} pinned to a full commit SHA")
    if "steps" in job:
        found.append("job check must not define its own steps; the shared workflow runs the checks")
    found += runner_violations(job)
    if as_dict(job.get("secrets")) not in (GEMINI, GEMINI | GATEWAY):
        found.append(
            "job check must pass GEMINI_API_KEY as ${{ secrets.GEMINI_API_KEY }}, "
            "and either both or none of GATEWAY_URL and GATEWAY_KEY as the secrets of the same name"
        )
    return found


def violations(document):
    found = []
    if document.get("name") != "CI":
        found.append('name must be "CI"')
    events = triggers(document)
    push = as_dict(events.get("push"))
    if "main" not in as_list(push.get("branches")):
        found.append("must run on push to main")
    if "pull_request" not in events:
        found.append("must run on pull_request")
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
