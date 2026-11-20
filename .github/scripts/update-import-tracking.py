"""Publish content-aware import branches for rulesets, providers, mise task groups and actions pointing to commits already tested by CI."""

import argparse
from pathlib import Path
import re
import subprocess
import sys
import tomllib


PREFIX = "refs/remotes/origin/updates/"
PACKAGE = re.compile(r"(?:(?:rulesets|providers|mise-tasks|actions)/[a-z0-9][a-z0-9-]*|workflows/(?:ci|release|statistics))")
WORKFLOW = re.compile(r"\.github/workflows/(ci|release|statistics)-[a-z-]+\.yml")
RULE = re.compile(r"rules/(?:checks|reviews)/[a-z0-9][a-z0-9-]*")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True, timeout=120).stdout


def ancestor(root, older, newer):
    result = subprocess.run(["git", "merge-base", "--is-ancestor", older, newer], cwd=root, capture_output=True, timeout=120)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return result.returncode == 0


def packages(root, revision):
    names = (
        git(root, "ls-tree", "-r", "--name-only", revision, "--", "rulesets", "providers", "mise-tasks", "actions", ".github/workflows")
        .decode()
        .splitlines()
    )
    found = set()
    for name in names:
        if (name.startswith("rulesets/") and name.endswith("/ruleset.toml")) or (
            name.startswith("providers/") and name.endswith("/provider.toml")
        ):
            found.add(str(Path(name).parent))
        elif match := WORKFLOW.fullmatch(name):
            # The reusable workflows of one family form one package, because GitHub allows no subdirectories for them.
            found.add(f"workflows/{match.group(1)}")
        elif name.startswith(("mise-tasks/", "actions/")) and name.count("/") >= 2:
            # A task group or action group is every file below its directory, such as mise-tasks/release or actions/release.
            found.add("/".join(name.split("/")[:2]))
    return sorted(found)


def definition(root, revision, package):
    try:
        return git(root, "show", f"{revision}:{package}/ruleset.toml")
    except subprocess.CalledProcessError:
        # Tracking branches published before the rename still point at config.toml.
        return git(root, "show", f"{revision}:{package}/config.toml")


def snapshot(root, revision, package):
    if not PACKAGE.fullmatch(package):
        raise ValueError(f"unsupported package path: {package}")
    paths = {package}
    if package.startswith("workflows/"):
        listing = git(root, "ls-tree", "--name-only", "-z", revision, ".github/workflows/").decode().split("\0")
        family = package.removeprefix("workflows/")
        paths = {name for name in listing if (match := WORKFLOW.fullmatch(name)) and match.group(1) == family}
    elif package.startswith("rulesets/"):
        config = tomllib.loads(definition(root, revision, package).decode())
        # This catalog uses direct Rule references, without nested imports.
        if "imports" in config or "rule_sets" in config:
            raise ValueError(f"nested imports are unsupported: {package}")
        for entry in config["rules"]:
            rule = entry if isinstance(entry, str) else entry["id"]
            if not RULE.fullmatch(rule):
                raise ValueError(f"unsupported Rule path: {rule}")
            git(root, "cat-file", "-e", f"{revision}:{rule}/rule.toml")
            paths.add(rule)
    elif package.startswith("providers/"):
        git(root, "cat-file", "-e", f"{revision}:{package}/provider.toml")
    # Tree entries cover bytes, filenames and modes; commit metadata is excluded.
    return git(root, "ls-tree", "-r", "-z", revision, "--", *sorted(paths))


def plan(root, revision):
    available = set(packages(root, revision))
    existing = {}
    for line in git(root, "for-each-ref", "--format=%(refname) %(objectname)", PREFIX).decode().splitlines():
        reference, commit = line.split()
        existing[reference.removeprefix(PREFIX)] = commit
    removed = existing.keys() - available
    if removed:
        raise ValueError(f"migrate consumers before retiring packages: {', '.join(sorted(removed))}")
    updates = []
    for package in sorted(available):
        current = existing.get(package)
        content = snapshot(root, revision, package)
        if current:
            if ancestor(root, revision, current):
                continue  # An equal or newer CI completion has already published.
            if not ancestor(root, current, revision):
                raise ValueError(f"tracking branch diverges from source history: {package}")
            if snapshot(root, current, package) == content:
                continue
        updates.append(f"{revision}:refs/heads/updates/{package}")
    return updates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish", action="store_true", help="fetch tracking refs and atomically push changed branches; otherwise preview"
    )
    args = parser.parse_args()
    root = Path.cwd()
    revision = git(root, "rev-parse", "HEAD^{commit}").decode().strip()
    if args.publish:
        git(root, "fetch", "--prune", "--no-tags", "origin", f"refs/heads/updates/*:{PREFIX}*")
    updates = plan(root, revision)
    for update in updates:
        print(f"Update {update}")
    if args.publish and updates:
        git(root, "push", "--atomic", "origin", *updates)
    print(f"{'Published' if args.publish else 'Planned'} {len(updates)} tracking branch updates")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
