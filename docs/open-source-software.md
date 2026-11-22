# Open-source software

The `rulesets/open-source-software` set includes the blocking `license`,
`ci-workflow` and `statistics-workflow` checks at `check` and `pre-push` and the `github-metadata` check at `check`.
The [quality assurance checks](#quality-assurance) follow the license section.
The `license` check It compares the target file byte-for-byte with the regular,
nonhidden files in `rules/checks/license/licenses/`. The included references are
Apache-2.0, PolyForm-Noncommercial-1.0.0 and Unlicense, downloaded unchanged
from [SPDX license-list-data](https://github.com/spdx/license-list-data/tree/31846481de7ea17d92f2f46fad76abe58b2e9bb0/text)
at commit `31846481de7ea17d92f2f46fad76abe58b2e9bb0`. Add or remove reference files to
change the allowlist. Whitespace, line endings and copyright notices must match.

Requirements: Python 3.9+ and Git. The Rule's own script compares the target with the
references in its `licenses` directory.

```toml
[[imports]]
git = "https://github.com/christianpflugradt/please-behave-governance.git"
path = "rulesets/open-source-software"
ref = "<FULL_REVIEWED_COMMIT_SHA>"

[imports.rule_parameters.license]
target = "LICENSE"
```

`target` is a required string containing a repository-relative path without
parent traversal. `check` reads the working-tree file; `pre-push` reads that path
from the current `HEAD`, independently of working-tree changes. Neither point
scans history or every pushed commit. A match returns PASS (0); a missing or
nonmatching target returns FAIL (1); configuration or execution errors return
ERROR (2).

## Quality assurance

- `github-metadata` requires the GitHub repository it runs in to have a description of at least 30
  characters and 3 to 10 topics. It runs only at `check` and only in GitHub Actions: it reads
  `GITHUB_REPOSITORY` and queries the public GitHub API for that repository. Pass
  `GITHUB_TOKEN: ${{ github.token }}` to the step that runs Please Behave to avoid the
  unauthenticated rate limit of shared runners; the token is optional and never printed. Values
  outside the limits return FAIL; a missing `GITHUB_REPOSITORY` or an API failure returns ERROR.
  It supports public repositories only.

- `ci-workflow` requires `.github/workflows/ci.yml` to follow the shared structure:
  - `name: CI`, triggered by `push` to `main` and by `pull_request`; further triggers are allowed.
  - Top-level `permissions` are exactly `contents: read`.
  - A job with the id `check` that calls the shared workflow `ci-check.yml`, pinned to a full commit SHA, and hands over
    `GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}` and, to publish the attestation of the run to a gateway, additionally
    `GATEWAY_URL: ${{ secrets.GATEWAY_URL }}` and `GATEWAY_KEY: ${{ secrets.GATEWAY_KEY }}`, always both. The shared workflow
    passes the URL as the input `gateway-url` of the Action and the key in the environment variable `PLEASE_GATEWAY_KEY`, so the
    repository's `[publish] key_env` must be `PLEASE_GATEWAY_KEY`. Without the secrets nothing is published; a failed delivery never
    changes the verdict. It defines no steps of its own; the shared workflow runs the reviews and limits the job to 30 minutes.
    The shared workflow checks out, installs the tools with `jdx/mise-action`, runs `mise run setup`, runs Please Behave at
    `check` and uploads the report; a repository that needs preparation, such as environment variables, does it in its mise
    tasks or passes them as the input `env` (`NAME=value` per line). The input `runner` (default `ubuntu-26.04`) may be set,
    to a versioned runner, optionally with a suffix such as `-arm`, and Renovate keeps it current. To check several platforms, the job `check` defines
    `strategy.matrix` with a key that lists only versioned runners and passes `runner: ${{ matrix.<key> }}`. The check appears as
    `<caller job name> / Check`, with the matrix entry appended to the caller job name, such as `check (ubuntu-26.04-arm) / Check`.
  - Every job that is no call of a shared workflow sets `timeout-minutes`; the shared workflow sets its own.

  Triggers beyond these, the runner version and further jobs are the repository's own. The rule checks structure only; it does
  not verify that CI ran or passed. It needs `yq` (v4) on `PATH` and Python 3.9+, and a missing tool or invalid YAML returns ERROR.

- `statistics-workflow` passes when `.github/workflows/statistics.yml` does not exist. Otherwise the workflow follows
  the shared structure for sending statistics to a central repository:
  - `name: Statistics`, triggered only by `push` to `main`, never by `workflow_dispatch`, `pull_request` or any other event.
  - Top-level `permissions` are exactly `contents: read`, and no job grants a write permission.
  - At least one job `collect` or `collect-<name>` calculates the statistics. A job `publish` needs every such job, runs only on
    `refs/heads/main` and calls the shared workflow `statistics-publish.yml`, pinned to a full commit SHA, with the input
    `project` and exactly `STATISTICS_TOKEN: ${{ secrets.STATISTICS_TOKEN }}`. It defines no steps of its own and is the only
    job that uses the secret. The repository keeps its own token; the shared workflow sends the payload to `badges-and-stuff`.
  - Every `actions/checkout` sets `persist-credentials: false`, and every job sets `timeout-minutes`.

  What the collect jobs calculate, the scripts they call and runners are the repository's own. The rule
  checks structure only. It needs `yq` (v4) on `PATH` and Python 3.9+, and a missing tool or invalid YAML returns ERROR.

Requirements: Git, and Python 3.9+ for `github-metadata`, `ci-workflow` and `statistics-workflow`, plus the tools named above.

Pin a full commit hash for reproducible imports. Run `please sync` to resolve
the import and install the managed pre-push hook. Exact matching enforces the
chosen reference texts; it does not identify licenses or assess their legal
suitability.
