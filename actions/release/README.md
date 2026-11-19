# Release actions

Prototype of the shared release machinery: Semantic Release policy, version planning and publication live
here once instead of in every repository. A repository supplies only its tag format, its assets and its
build and verification steps.

| Action | Purpose |
| --- | --- |
| `actions/release/plan` | Calculates the next release without publishing; outputs `release`, `version`, `source-sha`, `mode` |
| `actions/release/publish` | Publishes the planned version; refuses if the version or `main` changed since planning |
| `actions/release/check-ci` | Proves that CI succeeded for the exact source commit within one trusted workflow run, including reruns |
| `actions/release/evidence` | `record`s the hashes of a release candidate per target and uploads them as `release-evidence-<run>-<attempt>-<target>`; `verify` downloads the evidence of every target and verifies the complete set before publication |

`plan` and `publish` need Node 24.10+ on `PATH` (for example `install_args: node` in `jdx/mise-action`) and install their
pinned dependencies from `package-lock.json` themselves. `check-ci` and `evidence` need only Python 3.
Pin every action to the same full commit SHA. The repository must not keep its own
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

`mise run release --force`, `--prerelease` and `--release` set the `force` and `mode` dispatch inputs.

## Reusable workflows

The first two jobs of a release are identical in every repository and exist as reusable workflows, which the release
workflow calls instead of repeating their steps:

| Workflow | Replaces | Inputs |
| --- | --- | --- |
| `.github/workflows/release-plan.yml` | the `plan` job | `mode`, `tag-format`, `force` |
| `.github/workflows/release-readiness.yml` | the `release-readiness` job | `source-sha`, `required-jobs`, `tools`, `governance`, `gradle`, `timeout-minutes` |

```yaml
jobs:
  plan:
    if: github.ref == 'refs/heads/main'
    permissions:
      contents: write
    uses: christianpflugradt/please-behave-governance/.github/workflows/release-plan.yml@<FULL_COMMIT_SHA>
    with:
      force: ${{ inputs.force }}
  release-readiness:
    needs: plan
    if: needs.plan.outputs.release == 'true'
    permissions:
      contents: read
      actions: read
    uses: christianpflugradt/please-behave-governance/.github/workflows/release-readiness.yml@<FULL_COMMIT_SHA>
    with:
      source-sha: ${{ needs.plan.outputs.source_sha }}
      required-jobs: Check
```

The calling job grants the permissions, because a called workflow cannot hold more than its caller. Booleans are passed
as strings (`'true'`), so a scheduled run, which has no inputs, also works. `release-readiness` outputs `ci_run_id` and
`ci_attempt`. The workflows pin the actions of this directory to a full commit SHA, which Renovate keeps current in this
repository; consumers pin the workflow files and receive the tracking branch `updates/workflows/release`.

## Hooks

A repository whose release has more to do than tagging, for example publishing a container image, passes shell
commands to `publish` instead of carrying its own Semantic Release plugin. They run in the repository root with the
release environment (`EXPECTED_RELEASE_VERSION` and the `GITHUB_*` variables), and a failing command refuses the release:

| Input | Runs |
| --- | --- |
| `verify-command` | with every verification of the release, after the shared source checks |
| `prepare-command` | before the tag is created, followed by another source check |
| `publish-command` | after the tag is created |

`verify-command` also runs once before a `prerelease` is created; `prepare-command` and `publish-command` apply to the
`release` mode only. An asset line may end in ` :: <label>` to set the label shown on the release.

## Updates

Pin both actions to the same full commit SHA. Like rulesets, providers and mise task groups, `actions/release`
has a content-aware tracking branch `updates/actions/release`, published by the `Update import tracking`
workflow only when a file below the directory changed. The `renovate-content-aware` preset tracks it for
`uses:` lines in the workflows and opens one grouped PR; with only `renovate-governor`, every commit on `main`
moves the pin.

## Tests

`mise run setup` installs the locked dependencies, then `node --test` in this directory runs the offline tests.
The `node-tests` check runs them at `pre-push` and `check`.

## Repository files

Everything else is derived from the workflow context. A repository keeps only:

- `.github/release-artifacts.json` for `evidence`: `{"root": "dist", "targets": {"macos": ["App-{version}.zip", "metadata.json"]}}`.
  `{version}` is the only substitution; `repository` and `workflow` (`.github/workflows/release.yml`) are not repeated.
- The CI job names in the `required-jobs` input of `check-ci`.

## Not covered yet

Asset labels, and the 0.x development series of `please-behave/cli`. The Renovate managers for the
`uses:` lines are untested against a live Renovate run.
