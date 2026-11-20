# Security

The `rulesets/security` set runs four blocking checks at `check` and `pre-push`:

- `sensitive-files` rejects `.env` and `.env.*` filenames, except `.env.example`,
  `.env.sample` and `.env.template`, and files ending in `.tfstate` or containing
  `.tfstate.`. Matching is case-sensitive and applies in every directory.
- `secret-scan` scans file contents with Gitleaks.
- `workflow-action-pins` requires every `uses` of a remote action or reusable workflow in the files directly below
  `.github/workflows` to be pinned to a full 40-character commit SHA. Local references starting with `./` are exempt.
  Tags and branches fail, because they can be moved after review. A repository without workflows passes. It needs `yq`
  (v4) on `PATH` and Python 3.9+; a missing tool or invalid YAML returns ERROR.
- `dependency-audit-workflow` makes every repository decide how it handles dependency vulnerabilities. It verifies
  structure only, never that the workflow ran or passed, and it does not scan dependencies: scanners, reports,
  summaries, caches and severity handling are the repository's own. It needs the required parameter `dependencies`:
  - `audited` requires `.github/workflows/dependency-audit.yml`, which must exist and follow the shared structure:
    - `name: Dependency Audit`, triggered by `workflow_dispatch` and by exactly one `schedule` entry, never by `push`,
      `pull_request` or `pull_request_target`. The cron runs daily or weekly at a fixed time: day of month and month are
      `*`, the day of week is `*` or one weekday number, the minute is a number from 1 to 59 and the hour is 0 to 4
      (UTC, before 07:00 in Europe/Berlin in summer and winter time). A minute off the top of the hour avoids GitHub's
      busiest scheduling slot; for example `17 3 * * *`. Repositories with limited Actions minutes choose weekly.
    - A top-level `env` entry `AUDIT_FAIL_ON` with one of the literals `low`, `medium`, `high` or `critical` states the
      severity from which the audit fails. The rule checks that it is declared and valid; how the audit applies it is up
      to the workflow. Projects start with `medium` and raise it individually.
    - At least one job `audit` or `audit-<name>`, which calls at least one audit action of `actions/dependency-audit`
      (`npm`, `cargo`, `python`, `owasp`) and ends with its `report` action. All calls are pinned to the same full commit
      SHA, `report` runs with `if: ${{ !cancelled() }}` and takes `fail-on: ${{ env.AUDIT_FAIL_ON }}`. Python projects are
      audited from their `uv.lock`. Top-level `permissions` are exactly `contents: read` and no job grants
      a write permission. Every `actions/checkout` sets `persist-credentials: false`, and every job sets `timeout-minutes`.
  - `none` declares that the repository has no dependencies. The rule then fails when any tracked or nonignored file in
    any directory has a name from the candidate list in `rules/checks/dependency-audit-workflow/manifests.txt` (for example
    `package.json`, `Cargo.toml`, `build.gradle.kts`, `pyproject.toml` or `requirements*.txt`; names are matched
    case-sensitively), so a manifest that appears later turns the declaration into a failure. The list names the
    candidates only; the rule does not check which tool the workflow uses for which ecosystem.

  Any other value of `dependencies` is an ERROR. Keep the audit independent of build and release workflows; the rule does
  not require or forbid a release dependency on it. It needs `yq` (v4) on `PATH` for `audited` and Python 3.9+; a missing
  tool or invalid YAML returns ERROR.

`check` examines tracked and nonignored untracked working-tree files, including
unstaged edits. Deleted files are excluded; ignored files are included only when
tracked. `pre-push` examines the current `HEAD` tree, independent of working-tree
or index changes. Neither point scans Git history. `pre-push` does not inspect
other refs being pushed or consume Git's pre-push input.

Requirements: POSIX `sh`, Python 3.9+, Git, `yq` (v4) and Gitleaks v8 supporting `gitleaks dir`
(tested with 8.30.1). Install these tools explicitly before running checks.
Please Behave and these rules install no dependencies.

```toml
[[imports]]
git = "https://github.com/christianpflugradt/please-behave-governance.git"
path = "rulesets/security"
ref = "<FULL_REVIEWED_COMMIT_SHA>"

[imports.rule_parameters.dependency-audit-workflow]
dependencies = "audited" # or "none" without any dependency manifest
```

Pin a full commit hash for reproducible imports. Run `please sync` to resolve
the import and install the managed pre-push hook, then run
`please run --at check` to inspect the working tree explicitly.
See the scanner configuration and limitations below.

## Snapshot selection

The security and license rules take the snapshot from the run context's execution
point: `pre-commit` and `check` use `worktree`, `pre-push` uses `head`. Any other
execution point is ERROR. Neither selection scans history.

## Scanner configuration and limitations

The `rulesets/security` ruleset uses `gitleaks dir` on a temporary
snapshot. `check` copies current tracked and nonignored untracked files;
`pre-push` reads blobs from `HEAD` directly, without export attributes or history.

Gitleaks uses `.gitleaks.toml` and `.gitleaksignore` from that snapshot, or its
built-in detection rules when no configuration is present. `GITLEAKS_CONFIG` and
`GITLEAKS_CONFIG_TOML` are ignored so an external file or working-tree override
cannot change the committed scan. Use Gitleaks' standard configuration allowlists
for false positives. Keep any allowlists narrow and review their changes.

File contents behind symlinks are never scanned, including when a working-tree
directory has been replaced by a symlink. The filename check still rejects a
prohibited symlink name. Git submodules and other nonregular entries cause the
content scanner to report ERROR because their contents are outside its snapshot
coverage. Git LFS files in `HEAD` are scanned as their committed pointer blobs.

PASS exits 0; detected violations exit 1; technical errors exit 2. Missing tools,
Git failures, invalid configuration, missing/malformed scanner reports and
inconsistent scanner results cannot produce PASS. Internally, Gitleaks receives
a dedicated findings exit code to distinguish detections from tool errors.
Findings show escaped filenames and rule IDs, without secret values or matching
lines. Raw tool output is suppressed because it may contain sensitive data.
Please Behave enforces the check's configured timeout.

These checks are complementary: a secret-free `.env` still violates the filename
policy, while a secret in an ordinary source file can fail the content scan.
Allowed template filenames remain subject to secret scanning. Filename matching
and Gitleaks detection cannot establish that every secret has been found.
Imported rules execute with the consumer's permissions; trust their source and
pin their revision.
