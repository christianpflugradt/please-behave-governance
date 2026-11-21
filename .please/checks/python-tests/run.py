"""Run Rule, adapter, workflow-tooling and action tests, each in its own interpreter."""

import subprocess
import sys
from pathlib import Path


def main():
    tests = sorted(
        path
        for top in ("rules", "adapters", ".github/scripts", "actions")
        for path in Path(top).rglob("test_*.py")
        if "node_modules" not in path.parts  # Installed dependencies bring their own tests.
    )
    failed = [str(test) for test in tests if subprocess.run([sys.executable, "-B", str(test)]).returncode != 0]
    for test in failed:
        print(f"FAIL: {test}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # A crash is a technical failure, never a FAIL verdict.
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(2)
