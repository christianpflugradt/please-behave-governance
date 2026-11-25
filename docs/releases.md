# Releasable open-source software

Import `rulesets/releasable-open-source-software` in addition to the other bundles.
It runs the blocking `mise-release` and `release-workflow` Checks at `pre-push` (`HEAD`) and `check` (working tree).
The bundle takes no parameters. Example for the consumer's `.please/config.toml`:

```toml
[[imports]]
git = "https://github.com/christianpflugradt/repository-catalog.git"
path = "rulesets/releasable-open-source-software"
ref = "<FULL_REVIEWED_COMMIT_SHA>"
```

## Release task

The blocking `mise-release` Check requires a `release` task in the root `mise.toml` (same
rules as the [Mise baseline](repository-conventions.md#mise-baseline); it needs Python 3.11+).
Either define it locally, or include the shared task group `mise-tasks/release` pinned to a
full commit SHA; see [Shared mise tasks](mise-tasks.md). The task starts the release workflow on
the remote `main`, never on local state.

## Release workflow

The blocking `release-workflow` Check keeps every release on the shared machinery, whatever a repository builds. It requires
`.github/workflows/release.yml` to hold the jobs `plan`, `release-readiness`, `release-candidate` and `publish`, and allows further
candidate jobs named `release-candidate-<name>`. These invariants decide:

- `plan` and `release-readiness` call the shared workflows `release-plan.yml` and `release-readiness.yml`, pinned to a full commit SHA.
  Readiness and every candidate job need `plan` and run in parallel.
- Every candidate job (a matrix counts as one job) runs the shared `governance` action and records its files with `evidence`.
  A candidate job has no write permission: only `publish` publishes.
- `publish` needs every other job, runs only when all of them succeeded, verifies the evidence and publishes exactly once with
  `publish-release` (files, `contents: write`, and `id-token: write` to record the release) or `publish-image` (images; it also attests them and needs `contents`, `packages`,
  `id-token` and `attestations` write permission, and no other).
- The workflow starts only on `workflow_dispatch`, and no step runs Semantic Release itself.

The check also requires `.github/release-artifacts.json` and rejects an own Semantic Release configuration. It judges structure
only; what the candidate builds and verifies stays local.

## Release policy

Release policy (commit types and release levels, release notes), version calculation,
CI and evidence verification and publication are not checked in the consuming repository: the
shared release action and workflow carry them out. The former `semantic-release` and
`release-workflow` Checks are retired. Old commit pins keep them.
