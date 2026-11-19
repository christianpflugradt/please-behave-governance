# Shared mise tasks

Recurring [mise](https://mise.jdx.dev/tasks/) tasks that are not tied to Please Behave live
below `mise-tasks/<group>/`. A group is a directory of file tasks for repositories that share an
attribute, such as `mise-tasks/release` for releasable repositories. There is no standard set:
a repository includes exactly the groups that apply to it.

| Group | Repository group | Tasks |
| --- | --- | --- |
| `mise-tasks/release` | Releasable repositories | `release [--follow]`: wait for CI on `main`, then start the Release workflow |

## Import

Include a group in the root `mise.toml`, pinned to a full commit SHA like every other import:

```toml
[task_config]
includes = [
  ".mise/tasks", # `includes` replaces mise's default task directories, so keep local ones
  "git::https://github.com/christianpflugradt/please-behave-governance.git//mise-tasks/release?ref=<FULL_COMMIT_SHA>",
]
```

Entries later in the list override earlier ones with the same task name. The `mise-release`
Check accepts this include in place of a local `[tasks.release]`.

The `release` task reads `RELEASE_WORKFLOW` (default `release.yml`) and `RELEASE_CI_WORKFLOW`
(default `ci.yml`) for repositories with other workflow file names.

## Updates

The `Update import tracking` workflow publishes `updates/mise-tasks/<group>` like it does for
rulesets and providers: the branch advances to a CI-tested commit only when a file below the
group directory changed. Renovate, with the `renovate-content-aware` preset, tracks that branch
and opens one grouped PR; with only `renovate-governor`, every commit on `main` moves the pin.
Pin the SHA a tracking branch points to for the first import.

Keep a group small and self-contained: a task is one executable file with `#MISE` and `#USAGE`
headers, because only the files of the group directory are fetched and tracked.
