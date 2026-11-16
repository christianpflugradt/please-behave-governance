"""Require the rebase policy of private repositories in renovate.json: rebase only on conflicts, except for the governor pins."""

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


TARGET = "renovate.json"
GOVERNED = {
    "please-behave/cli",
    "please-behave/action",
    "https://github.com/christianpflugradt/please-behave-governance.git",
}


def items(value):
    return value if isinstance(value, list) else []


def violations(config):
    """Return what the parsed renovate.json lacks. Everything else in it is the repository's own."""
    if not isinstance(config, dict):
        return ["must be a JSON object"]
    found = []
    if config.get("rebaseWhen") != "conflicted":
        found.append('requires rebaseWhen = "conflicted" so open PRs are not rebased and re-checked on every push to main')
    rules = [rule for rule in items(config.get("packageRules")) if isinstance(rule, dict)]
    if not any(rule.get("rebaseWhen") == "behind-base-branch" and GOVERNED <= set(items(rule.get("matchPackageNames"))) for rule in rules):
        found.append(
            'requires a packageRules entry with rebaseWhen = "behind-base-branch" and matchPackageNames ' + ", ".join(sorted(GOVERNED))
        )
    return found


def main():
    if len(sys.argv) != 2:
        raise ValueError("expected one argument: context file")
    point = execution_point(sys.argv[1])
    content = read_file(select_snapshot(point), TARGET)
    if content is None:
        print(f"FAIL: {TARGET} is missing")
        return 1
    try:
        config = json.loads(content.decode("utf-8"))
    except json.JSONDecodeError as exc:
        print(f"FAIL: {TARGET} is not valid JSON ({exc.msg} at line {exc.lineno})")
        return 1
    found = violations(config)
    for message in found:
        print(f"FAIL: {TARGET} {message}")
    if not found:
        print(f"PASS: {TARGET} states the private rebase policy ({point})")
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
