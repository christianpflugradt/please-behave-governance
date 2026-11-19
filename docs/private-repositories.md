# Private repositories

The `rulesets/private-repositories` set is for private repositories that run GitHub Actions on
the free plan's monthly minutes (2,000 Linux minutes across all private repositories; each job
is rounded up to a whole minute, macOS bills at 10x and Windows at 2x). It is not combined with
`open-source-software`, because the two define the CI workflow differently: that set also runs CI
on every push to `main`, this one runs it on pull requests only. It runs at `check` and `pre-push`
and adds to `repository-conventions`. Why each rule and the set exist is recorded in the
`rule.yaml` and `ruleset.yaml` files next to their definitions.

- `private-ci-workflow` requires `.github/workflows/ci.yml` to be the lean CI:
  - `name: CI`, triggered by `pull_request` and optionally `workflow_dispatch`, never by `push`,
    `schedule` or any other event. Agents and owners already pass the Git hooks before pushing to
    `main`, so a push run would only bill the same checks twice.
  - Top-level `permissions` are exactly `contents: read`, and `concurrency` sets
    `cancel-in-progress: true` so a force-pushed PR branch does not keep billing superseded runs.
  - A job `check` with, in this order, `actions/checkout` with `persist-credentials: false`,
    `jdx/mise-action` without `install_args`, and `please-behave/action` with
    `execution-point: check`, `install: 'false'`, `sync: 'true'` and `checks-only: 'true'`. Reviews
    are advisory and call a paid model API, so they stay out of CI.
  - Every job sets `timeout-minutes` of at most the `max_job_minutes` parameter, runs on a versioned
    Ubuntu runner such as `ubuntu-24.04` and uses no `strategy.matrix`. A versioned label instead of
    `ubuntu-latest` keeps runner changes reviewable: Renovate's github-actions manager proposes new
    runner versions as PRs. Do not move to a runner that `jactionlint` does not know yet; the
    `workflow-syntax` check of `repository-conventions` fails such a PR on purpose.
  - The check needs `yq` (v4) and Python 3.9+.
- `private-renovate` requires `rebaseWhen: "conflicted"` in `renovate.json` and a `packageRules`
  entry with `rebaseWhen: "behind-base-branch"` for `please-behave/cli`, `please-behave/action` and
  the governance repository's git URL. By default Renovate rebases every open PR whenever `main`
  moves, and each rebase reruns CI; one merged PR then triggers a rebase of all the others. Rebasing
  on conflicts only keeps it at roughly one run per PR. Without branch protection (not available
  on the free plan for private repositories) a PR that is behind still merges. The governor
  and ruleset pins keep the stricter policy, because a ruleset pin must be re-validated after a
  governor update that touches different files. The check reads the file, not Renovate's merged
  configuration.
- `private-visibility` runs at `check` only, in GitHub Actions, and fails when the repository is
  public. It needs `GITHUB_REPOSITORY` and, for a private repository, `GITHUB_TOKEN`, so pass
  `env: GITHUB_TOKEN: ${{ github.token }}` to the Please Behave step. Locally it returns ERROR (2),
  as `github-metadata` does.

The set cannot see billing. Keep the account's Actions spending limit at $0 (or without a payment
method) so exhausting the free minutes stops runs instead of charging; stopped runs leave
Renovate PRs without a check until the month resets.

```toml
[[imports]]
git = "https://github.com/christianpflugradt/please-behave-governance.git"
path = "rulesets/private-repositories"
ref = "<FULL_REVIEWED_COMMIT_SHA>"

[imports.rule_parameters.private-ci-workflow]
max_job_minutes = "5"
```
