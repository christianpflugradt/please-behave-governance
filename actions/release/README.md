# Release actions

Prototype of the shared release machinery: Semantic Release policy, version planning and publication live
here once instead of in every repository. A repository supplies only its tag format, its assets and its
build and verification steps.

| Action | Purpose |
| --- | --- |
| `actions/release/plan` | Calculates the next release without publishing; outputs `release`, `version`, `source-sha`, `mode` |
| `actions/release/publish` | Publishes the planned version; refuses if the version or `main` changed since planning |

Both need Node 24.10+ on `PATH` (for example `install_args: node` in `jdx/mise-action`) and install their
pinned dependencies from `package-lock.json` themselves. The repository must not keep its own
`release.config.*`, `.releaserc*` or `release` key in `package.json`; the actions refuse to run with one.

## Modes

- `release` (default): Semantic Release derives the version from Conventional Commits and creates the tag and
  GitHub release with the `assets` globs. Used for manual and scheduled releases alike.
- `prerelease`: a development release `<stable>-dev.<yyyymmdd>.<n>` (tag `dev-<version>`) of the latest stable
  release, counted per day in Europe/Berlin.
- `force: 'true'` (release mode): a patch release even if no commit would raise the version, to test the
  release process. Hand the same value to `plan` and `publish`.

## Usage

```yaml
on:
  workflow_dispatch:
    inputs:
      force: { type: boolean, default: false }
      mode: { type: choice, options: [release, prerelease], default: release }
  schedule:
    - cron: '17 4 1 * *'
# plan job
- uses: christianpflugradt/please-behave-governance/actions/release/plan@<FULL_COMMIT_SHA>
  id: plan
  with:
    mode: ${{ github.event_name == 'schedule' && 'release' || inputs.mode }}
    force: ${{ inputs.force }}
# publish job, after the candidate was built and verified
- uses: christianpflugradt/please-behave-governance/actions/release/publish@<FULL_COMMIT_SHA>
  with:
    version: ${{ needs.plan.outputs.version }}
    mode: ${{ needs.plan.outputs.mode }}
    force: ${{ inputs.force }}
    assets: |
      dist/*.zip
      dist/SHA256SUMS
```

`mise run release --force` and `--prerelease` set the `force` and `mode` dispatch inputs.

## Updates

Pin both actions to the same full commit SHA. Like rulesets, providers and mise task groups, `actions/release`
has a content-aware tracking branch `updates/actions/release`, published by the `Update import tracking`
workflow only when a file below the directory changed. The `renovate-content-aware` preset tracks it for
`uses:` lines in the workflows and opens one grouped PR; with only `renovate-governor`, every commit on `main`
moves the pin.

## Tests

`mise run setup` installs the locked dependencies, then `node --test` in this directory runs the offline tests.
The `node-tests` check runs them at `pre-push` and `check`.

## Not covered yet

CI and evidence verification (`check-release-ci.py`, `release-evidence.py`), local publication hooks such as the
GHCR push, asset labels, and the 0.x development series of `please-behave/cli`. The Renovate managers for the
`uses:` lines are untested against a live Renovate run.
