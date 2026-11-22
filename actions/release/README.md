# Release actions

Prototype of the shared release machinery: Semantic Release policy, version planning and publication live
here once instead of in every repository. A repository supplies only its tag format, its assets and its
build and verification steps.

| Action | Purpose |
| --- | --- |
| `actions/release/plan` | Calculates the next release without publishing; outputs `release`, `version`, `source-sha`, `mode` |
| `actions/release/publish-release` | Publishes the planned version as a GitHub release with files; refuses if the version or `main` changed since planning |
| `actions/release/check-image` | Verifies the candidate container image archives of one platform in the candidate job |
| `actions/release/publish-image` | Pushes the verified candidate images to `ghcr.io` and publishes the planned release without files |
| `actions/release/check-ci` | Proves that CI succeeded for the exact source commit within one trusted workflow run, including reruns |
| `actions/release/evidence` | `record`s the hashes of a release candidate per target and uploads them as `release-evidence-<run>-<attempt>-<target>`, together with the candidate files as `release-candidate-<run>-<attempt>-<target>`; `verify` downloads both for every target, restores the candidate files below the policy `root` and verifies the complete set before publication |
| `actions/release/governance` | verifies the repository at the `release` point of Please Behave and uploads the governance report; `target` is appended to the report name when a matrix of candidate jobs uploads several |
| `actions/dependency-audit` | scans the dependencies of a repository, renders the findings as a step summary, fails from a severity and uploads the report; see its own README |

`plan`, `publish-release` and `publish-image` need Node 24.10+ on `PATH` (for example `install_args: node` in `jdx/mise-action`) and install their
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
- uses: christianpflugradt/please-behave-governance/actions/release/publish-release@<FULL_COMMIT_SHA>
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
| `.github/workflows/release-readiness.yml` | the `release-readiness` job | `source-sha`, `required-jobs` |

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
as strings (`'true'`), so a scheduled run, which has no inputs, also works. `release-readiness` only proves CI; governance rules at the `release` point run in the candidate job. The workflows pin the actions of this directory to a full commit SHA, which Renovate keeps current in this
repository; consumers pin the workflow files and receive the tracking branch `updates/workflows/release`.

## Versions

The release levels follow Conventional Commits. While the latest official release is a `0.x` version, a breaking change raises
the minor version instead of the major one, because Semver allows breaking changes in the `0.x` series. The step to `1.0.0`
is a deliberate, manual one: tag it yourself.

A repository without any release starts at `0.1.0`, whatever its commits, and there is no input for another start.
Semantic Release would start at `1.0.0` and offers no setting for that. Plan and publish therefore tag the first
commit locally with the seed `v0.0.0` (in the repository's tag format) before Semantic Release runs, and remove it before
the release tag is created, so it never reaches the remote. The first release notes list the commits after the first commit.

## Two ways to publish

A repository publishes either files or images, and each way has its own action instead of one action with switches.

- **GitHub release with files** (`publish-release`): inputs `version`, `assets` (globs), `mode`, `tag-format`, `force`. `mode: prerelease`
  publishes a development release `<stable>-dev.<yyyymmdd>.<n>` of the latest stable release instead of an official one.
- **Container images** (`check-image` in each candidate job, `publish-image` in the publish job): every platform builds its own
  candidate on a native runner, a matrix of candidate jobs with the targets `linux-amd64` and `linux-arm64`. Each job builds an OCI
  archive `dist/<name>-<target>.tar` for every image of the repository, one platform with the OCI labels
  `org.opencontainers.image.version=v<version>` and `org.opencontainers.image.revision=<sha>` and with BuildKit's provenance and SBOM
  attestations, and `check-image` verifies them:

  ```sh
  docker buildx build --platform linux/amd64 --provenance=mode=max --sbom=true \
    --label "org.opencontainers.image.version=v${EXPECTED_RELEASE_VERSION}" \
    --label "org.opencontainers.image.revision=${GITHUB_SHA}" \
    --output type=oci,dest=dist/<name>-linux-amd64.tar .
  ```

  `check-image` takes `version`, `target` and `images` (the names, one per line; each is a repository below `ghcr.io/<owner>`),
  and the job then records the archives with `evidence` as the files of its target. The publish job needs no image list:
  `publish-image` takes the names from the archives that `evidence` restored. It verifies them again before the tag exists,
  assembles the platform images of each name into one image index (deterministic, so the same archives always give the same
  digest) and, after the tag, copies the archived blobs and manifests unchanged to `:v<version>` and `:latest`, so the published
  digest is the verified one. It builds nothing and logs in to the registry with the job token, whose credentials the attestation
  reads. After the release it attests each published index with `actions/attest`: a signed SLSA build provenance bound to the workflow
  identity and pushed to the registry next to the image (at most four images per release; a failed attestation fails the job).
  Verify an image with `gh attestation verify oci://ghcr.io/<owner>/<name>@<index digest> --owner <owner>`. Attestations need a public
  repository (or GitHub Enterprise Cloud). Only the publish job holds write permissions, `contents`, `packages`, `id-token` and
  `attestations`. Images are published with official releases only. The repository's own release-point rule, for example a smoke
  test of the image, runs in each candidate job through the `governance` action, so it runs natively on every platform.

  The policy lists the archives per target, for example
  `{"root": "dist", "targets": {"linux-amd64": ["app-linux-amd64.tar"], "linux-arm64": ["app-linux-arm64.tar"]}}`.

## Updates

Pin both actions to the same full commit SHA. Like rulesets, adapter profiles and mise task groups, `actions/release`
has a content-aware tracking branch `updates/actions/release`, published by the `Update import tracking`
workflow only when a file below the directory changed. The `renovate-content-aware` preset tracks it for
`uses:` lines in the workflows and opens one grouped PR; with only `renovate-governor`, every commit on `main`
moves the pin.

## Tests

`mise run setup` installs the locked dependencies, then `node --test` in this directory runs the offline tests.
The `node-tests` check runs them at `pre-push` and `check`.

## Candidate files

`evidence` also carries the candidate between jobs. `record` archives exactly the files that the policy lists for the target
and uploads them; `verify` restores them from the archives of every target, refuses any member the policy does not list,
and hashes the restored files against the evidence. A repository therefore needs no artifact steps of its own around
`evidence`: Publish checks out the source, runs `verify`, and finds the candidate where the policy puts it. Add
`/release-candidate/` next to `/release-evidence/` to `.gitignore`.

## Repository files

Everything else is derived from the workflow context. A repository keeps only:

- `.github/release-artifacts.json` for `evidence`: `{"root": "dist", "targets": {"macos": ["App-{version}.zip"]}}`; a matrix of
  candidate jobs lists one target per job whose files are published, and the other jobs record nothing.
  `{version}` is the only substitution; `repository` and `workflow` (`.github/workflows/release.yml`) are not repeated.
- The CI job names in the `required-jobs` input of `check-ci`.

## Candidate matrices

When the candidate job is a matrix (platforms, architectures, Java versions), every entry builds and verifies the candidate. The
`governance` action takes the matrix entry as `target`, so that the entries upload their reports under different names. Entries
whose files are not published, such as the older Java versions of a JAR that is built once for release, call `evidence` neither
with `record` nor with a target of their own: the policy lists only the targets that are published, and `evidence` records each
of them from the one entry that built it.

The 0.x development series of `please-behave/cli` is covered: while the latest official release is a `0.x` version, a breaking
change raises the minor version.
