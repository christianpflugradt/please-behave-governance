"""Run shfmt in diff mode on all tracked *.sh files; no scripts means PASS."""

import os
import subprocess
import sys


def main():
    listed = subprocess.run(["git", "ls-files", "-z", "--", "*.sh"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if listed.returncode:
        raise RuntimeError("git ls-files failed")
    scripts = sorted(os.fsdecode(path) for path in listed.stdout.split(b"\0") if path and os.path.isfile(os.fsdecode(path)))
    if not scripts:
        print("PASS: no shell scripts to check")
        return 0
    result = subprocess.run(["shfmt", "-d", "--", *scripts], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode == 0:
        print(f"PASS: shfmt found no formatting differences in {len(scripts)} scripts")
        return 0
    # shfmt also exits 1 for syntax errors, but only differences appear on standard output.
    if result.returncode == 1 and result.stdout:
        print(result.stdout, end="")
        print("FAIL: shfmt would reformat these scripts; run mise run format")
        return 1
    raise RuntimeError(f"shfmt failed (exit {result.returncode}): {result.stderr.strip()}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:  # A crash is a technical failure, never a FAIL verdict.
        print(f"ERROR: unexpected {type(exc).__name__}", file=sys.stderr)
        sys.exit(2)
