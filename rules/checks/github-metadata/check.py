"""Require a GitHub description of at least MIN_DESCRIPTION characters and MIN_TOPICS to MAX_TOPICS topics."""

import json
import os
import re
import sys
import urllib.error
import urllib.request

MIN_DESCRIPTION = 30
MIN_TOPICS = 3
MAX_TOPICS = 10
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def fetch(repository, token):
    """Return the repository document; the token is optional because the repository is public."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "github-metadata-check"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"https://api.github.com/repos/{repository}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            document = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub API returned HTTP {exc.code} for {repository}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError(f"GitHub API request for {repository} failed: {type(exc).__name__}") from None
    if not isinstance(document, dict):
        raise RuntimeError("GitHub API returned an unexpected document")
    return document


def violations(document):
    found = []
    description = document.get("description")
    length = len(description.strip()) if isinstance(description, str) else 0
    if length < MIN_DESCRIPTION:
        found.append(f"description has {length} characters, at least {MIN_DESCRIPTION} required")
    topics = document.get("topics")
    count = len(topics) if isinstance(topics, list) else 0
    if not MIN_TOPICS <= count <= MAX_TOPICS:
        found.append(f"repository has {count} topics, {MIN_TOPICS} to {MAX_TOPICS} required")
    return found


def main():
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not REPOSITORY.fullmatch(repository):
        raise RuntimeError("GITHUB_REPOSITORY is not set to owner/name; this check runs in GitHub Actions")
    found = violations(fetch(repository, os.environ.get("GITHUB_TOKEN")))
    for line in found:
        print(f"FAIL: {line}")
    if not found:
        print(f"PASS: {repository} has a description and topics within the limits")
    return 1 if found else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except Exception as exc:  # A crash is a technical failure, never a FAIL verdict.
        print(f"ERROR: unexpected {type(exc).__name__}", file=sys.stderr)
        sys.exit(2)
