"""Run the release action tests with the Node test runner after its locked dependencies are installed."""

import subprocess
import sys
from pathlib import Path

ACTION = Path("actions/release")


def main():
    if not (ACTION / "node_modules").is_dir():
        # Missing dependencies are a tooling problem, not a verdict about the tests.
        print("ERROR: run `mise run setup` to install the locked release action dependencies", file=sys.stderr)
        return 2
    return 1 if subprocess.run(["node", "--test", str(ACTION)]).returncode else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # A crash is a technical failure, never a FAIL verdict.
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(2)
