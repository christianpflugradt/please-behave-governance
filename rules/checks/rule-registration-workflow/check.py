"""Verify that local rule owners can register via the shared GitHub workflow."""

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

CALLER = ".github/workflows/register-rules.yml"
SHARED = ".github/workflows/rule-registration.yml"
CATALOG = "christianpflugradt/repository-catalog"
EXAMPLES = "please-behave/docs"
ORIGIN = re.compile(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+/[^/]+)", re.IGNORECASE)
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
STANDARD_ROOTS = (".please/checks", "rules/checks", "rules/reviews")
SECRETS = {"GATEWAY_URL": "${{ secrets.GATEWAY_URL }}", "GATEWAY_KEY": "${{ secrets.GATEWAY_KEY }}"}


def repository():
    result = subprocess.run(["git", "config", "--get", "remote.origin.url"], capture_output=True, text=True)
    if result.returncode == 1:
        return ""
    result.check_returncode()
    match = ORIGIN.fullmatch(result.stdout.strip())
    return match[1].removesuffix(".git").lower() if match else ""


def rule_files():
    candidates = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", ":(glob)**/rule.toml"],
        text=True,
    )
    return [Path(name) for name in sorted(set(candidates.split("\0")) - {""}) if Path(name).is_file()]


def required_path(file):
    name = file.as_posix()
    for root in STANDARD_ROOTS:
        if name.startswith(f"{root}/") and len(file.relative_to(root).parts) > 1:
            return f"{root}/**/{file.name}"
    return name


def check_rules(rules):
    required = {".please/config.toml", CALLER}
    failures = []
    for rule in rules:
        with rule.open("rb") as source:
            try:
                definition = tomllib.load(source)
            except tomllib.TOMLDecodeError:
                failures.append(f"{rule} must be valid TOML")
                continue
        version = definition.get("version")
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            failures.append(f'{rule} must declare a version such as "1.0"')
        knowledge = rule.with_name("rule.yaml")
        if not knowledge.is_file():
            failures.append(f"{knowledge} is missing")
        required.update((required_path(rule), required_path(knowledge)))
        prompt = definition.get("review", {}).get("prompt")
        if isinstance(prompt, str):
            required.add(required_path(rule.parent / prompt))
    return required, failures


def check_workflow(repo, required):
    if not Path(CALLER).is_file():
        return ["is missing"]
    parsed = subprocess.run(["yq", "--output-format=json", ".", CALLER], capture_output=True, text=True)
    if parsed.returncode:
        return ["must be valid YAML"]
    workflow = json.loads(parsed.stdout)
    if not isinstance(workflow, dict):
        return ["must be a YAML mapping"]

    failures = []
    events = workflow.get("on")
    if not isinstance(events, dict) or set(events) - {"push", "workflow_dispatch"} or "push" not in events:
        failures.append("must run on push and may also use workflow_dispatch")
        events = {}
    push = events.get("push")
    if not isinstance(push, dict) or push.get("branches") != ["main"]:
        failures.append("must limit pushes to branches: [main]")
        push = {}
    paths = push.get("paths")
    if not isinstance(paths, list) or any(not isinstance(path, str) or path.startswith("!") for path in paths):
        failures.append("must declare a positive push.paths filter")
    else:
        for path in sorted(required - set(paths)):
            failures.append(f"push.paths must include {path}")
        for path in sorted(set(paths) - required):
            failures.append(f"push.paths includes an unrelated path: {path}")
    if workflow.get("permissions") != {"contents": "read"}:
        failures.append("must set permissions to contents: read")
    uses = rf"{re.escape(CATALOG + '/' + SHARED)}@[0-9a-f]{{40}}"
    job = workflow.get("jobs")
    if not isinstance(job, dict) or set(job) != {"register"} or not isinstance(job["register"], dict):
        return failures + ["must contain exactly one job named register"]
    job = job["register"]
    reference = str(job.get("uses", ""))
    if not re.fullmatch(uses, reference) and not (repo == CATALOG and reference == f"./{SHARED}"):
        failures.append(f"must use the shared {SHARED} pinned to a full commit SHA, or the relative path in the catalog")
    if set(job) - {"uses", "with", "secrets", "name"}:
        failures.append("register must only call the shared workflow")
    if job.get("secrets") != SECRETS:
        failures.append("register must pass GATEWAY_URL and GATEWAY_KEY explicitly")
    return failures


def main():
    repo = repository()
    if not repo:
        print("PASS: no GitHub origin for rule registration")
        return 0
    if repo == EXAMPLES:
        print("PASS: documentation examples are excluded from registration")
        return 0
    rules = rule_files()
    if not rules:
        print("PASS: no local rule definitions to register")
        return 0
    required, failures = check_rules(rules)
    if repo == CATALOG:
        required.add(SHARED)
    if not Path(".please/config.toml").is_file():
        failures.append(".please/config.toml must configure publish.key_env")
    else:
        with Path(".please/config.toml").open("rb") as source:
            if tomllib.load(source).get("publish", {}).get("key_env") != "PLEASE_GATEWAY_KEY":
                failures.append(".please/config.toml must set publish.key_env to PLEASE_GATEWAY_KEY")
    failures.extend(f"{CALLER} {message}" for message in check_workflow(repo, required))
    for failure in failures:
        print(f"FAIL: {failure}")
    if not failures:
        print("PASS: local rule versions use the shared registration workflow")
    return int(bool(failures))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # Tooling failures must not become a rule verdict.
        print(f"ERROR: could not check rule registration ({type(error).__name__})", file=sys.stderr)
        sys.exit(2)
