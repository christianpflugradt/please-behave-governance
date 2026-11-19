"""Require sh shebangs and run ShellCheck on all tracked *.sh files; no scripts means PASS.

Non-portable scripts listed in .please/shell-exceptions.txt (one full repository path per line) are exempt.
"""

import os
import re
import subprocess
import sys


# Portable shell scripts declare the system's POSIX shell, optionally with arguments.
EXCEPTIONS_FILE = ".please/shell-exceptions.txt"
SH_SHEBANG = re.compile(rb"^#!\s*(/bin/sh|/usr/bin/env\s+sh)(\s|$)")


def is_portable(path):
    """Only *.sh files whose first line declares sh are portable; this excludes bash, zsh and others."""
    if not path.endswith(".sh"):
        return False
    with open(path, "rb") as handle:
        return bool(SH_SHEBANG.match(handle.readline(256)))


def read_exceptions():
    """Full repository-relative paths, one per line; blank lines and lines starting with # are ignored."""
    if not os.path.isfile(EXCEPTIONS_FILE):
        return []
    with open(EXCEPTIONS_FILE, encoding="utf-8") as handle:
        lines = [line.strip() for line in handle]
    return [line for line in lines if line and not line.startswith("#")]


def main():
    listed = subprocess.run(["git", "ls-files", "-z", "--", "*.sh", "*.zsh"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if listed.returncode:
        raise RuntimeError("git ls-files failed")
    scripts = sorted(os.fsdecode(path) for path in listed.stdout.split(b"\0") if path and os.path.isfile(os.fsdecode(path)))
    unportable = [path for path in scripts if not is_portable(path)]
    exceptions = read_exceptions()
    invalid = [entry for entry in exceptions if entry not in unportable]
    if invalid:
        for entry in invalid:
            reason = "wildcards are not allowed" if re.search(r"[*?\[]", entry) else "is not a tracked non-portable shell script"
            print(f"FAIL: exception {ascii(entry)} in {EXCEPTIONS_FILE}: {reason}; list full repository paths of existing scripts only")
        return 1
    scripts = [path for path in scripts if path not in exceptions]
    unportable = [path for path in unportable if path not in exceptions]
    if unportable:
        for path in unportable:
            print(f"FAIL: {ascii(path)} must be a *.sh script with an sh shebang (#!/bin/sh)")
        return 1
    if not scripts:
        print("PASS: no shell scripts to check")
        return 0
    result = subprocess.run(["shellcheck", "--", *scripts])
    if result.returncode == 0:
        print(f"PASS: ShellCheck found no issues in {len(scripts)} scripts")
        return 0
    if result.returncode == 1:  # ShellCheck reports findings with exit 1.
        print("FAIL: ShellCheck reported issues")
        return 1
    raise RuntimeError(f"shellcheck failed (exit {result.returncode})")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:  # A crash is a technical failure, never a FAIL verdict.
        print(f"ERROR: unexpected {type(exc).__name__}", file=sys.stderr)
        sys.exit(2)
