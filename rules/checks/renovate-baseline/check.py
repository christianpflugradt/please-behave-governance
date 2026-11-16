"""Require a root renovate.json that states the shared Renovate baseline."""

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
GOVERNOR = "github>christianpflugradt/please-behave-governance:renovate-governor"
DEFAULT_DAYS = 7
SLOW_DAYS = 14


def days(value):
    """Return a minimumReleaseAge such as "7 days" or "2 weeks" in days; 0 when absent or unsupported."""
    match = re.fullmatch(r"(\d+) (day|week)s?", value) if isinstance(value, str) else None
    return int(match.group(1)) * (7 if match.group(2) == "week" else 1) if match else 0


def items(value):
    return value if isinstance(value, list) else []


def has(rule, key, value):
    return value in items(rule.get(key))


def violations(config):
    """Return what the parsed renovate.json lacks. Everything else in it is the repository's own."""
    if not isinstance(config, dict):
        return ["must be a JSON object"]
    found = []
    if GOVERNOR not in items(config.get("extends")):
        found.append(f"must extend {GOVERNOR}")
    if config.get("platformAutomerge") is not False:
        found.append("requires platformAutomerge = false")
    if days(config.get("minimumReleaseAge")) < DEFAULT_DAYS:
        found.append(f"requires a minimumReleaseAge of at least {DEFAULT_DAYS} days")
    rules = [rule for rule in items(config.get("packageRules")) if isinstance(rule, dict)]
    if not any(has(r, "matchUpdateTypes", "major") and days(r.get("minimumReleaseAge")) >= SLOW_DAYS for r in rules):
        found.append(f"requires a packageRules entry for major updates with a minimumReleaseAge of at least {SLOW_DAYS} days")
    if not any(
        (has(r, "matchManagers", "github-actions") or has(r, "matchFileNames", ".github/**"))
        and days(r.get("minimumReleaseAge")) >= SLOW_DAYS
        for r in rules
    ):
        found.append(f"requires a packageRules entry for GitHub Actions with a minimumReleaseAge of at least {SLOW_DAYS} days")
    if not any(
        r.get("automerge") is True
        and has(r, "matchUpdateTypes", "minor")
        and has(r, "matchUpdateTypes", "patch")
        and not has(r, "matchUpdateTypes", "major")
        for r in rules
    ):
        found.append("requires a packageRules entry with automerge = true for minor and patch updates but not major ones")
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
        print(f"PASS: {TARGET} states the baseline ({point})")
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
