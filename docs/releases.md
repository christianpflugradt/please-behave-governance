# Releasable open-source software

Import `rulesets/releasable-open-source-software` in addition to the other bundles.
It runs the blocking `mise-release` Check at `pre-push` (`HEAD`) and `check` (working tree).
The bundle takes no parameters. Example for the consumer's `.please/config.toml`:

```toml
[[imports]]
git = "https://github.com/christianpflugradt/please-behave-governance.git"
path = "rulesets/releasable-open-source-software"
ref = "<FULL_REVIEWED_COMMIT_SHA>"
```

## Release task

The blocking `mise-release` Check requires a `release` task in the root `mise.toml` (same
rules as the [Mise baseline](repository-conventions.md#mise-baseline); it needs Python 3.11+).
Either define it locally, or include the shared task group `mise-tasks/release` pinned to a
full commit SHA; see [Shared mise tasks](mise-tasks.md). The task starts the release workflow on
the remote `main`, never on local state.

## Release policy and workflow

Release policy (commit types and release levels, release notes), version calculation,
CI and evidence verification and publication are not checked in the consuming repository: the
shared release action and workflow carry them out. The former `semantic-release` and
`release-workflow` Checks are retired. Old commit pins keep them.
