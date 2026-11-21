# Please Behave governance

Personal governance policies maintained by Christian Pflugradt for
[Please Behave](https://github.com/please-behave/cli). Rulesets group requirements
for repositories with common attributes and execution points. Import the bundles
that apply to a repository; each bundle adds to the others.

| Ruleset path | Repository group | Execution points | Current rules |
| --- | --- | --- | --- |
| `rulesets/repository-conventions` | Every repository, public or private, software or otherwise | `pre-commit`, `check`; `commit-msg` for commit rules | `AGENTS.md` size limit, EditorConfig baseline, Conventional Commit syntax and advisory header wording, mise governor pin with `format`, `verify` and `setup` tasks, Renovate baseline, workflow syntax with `jactionlint`, portable POSIX `sh` scripts checked with ShellCheck |
| `rulesets/open-source-software` | Open-source software | `check`, `pre-push` | Approved license, CI and Statistics workflow structure, GitHub metadata |
| `rulesets/private-repositories` | Private repositories on the free Actions minutes, instead of open-source software | `check`, `pre-push` | Pull-request-only CI with bounded jobs, Renovate rebase policy, private visibility |
| `rulesets/releasable-open-source-software` | Releasable open-source software | `check`, `pre-push` | `release` task, release workflow structure |
| `rulesets/security` | Repositories adopting the security policy | `check`, `pre-push` | Sensitive filenames, secret scanning, pinned workflow actions and Dependency Audit workflow structure |

Repository conventions stay lightweight and work through Git hooks alone; `check` adds the same checks to CI.
Security is a deliberate domain separation. License checking belongs to the
open-source bundle.

## Principles

The catalog standardizes principles, not technologies. It prescribes no language,
framework, build tool, test runner or linter, and it places no limit on the artifact
a repository produces: a compiled binary, a JAR, several container images or a
deployment all fit. Structure and policy are shared where there is no legitimate
reason to deviate; where there is one, the choice stays local. The only technology
the catalog sets is a small layer of governance tooling and the glue around a project,
such as shell scripts, and it complements other tools instead of excluding them.
The platform it works on (Please Behave, GitHub, mise, Renovate) is its environment,
not a constraint on what is built.

Goals:

- **Predictability** (no surprises): a repository behaves as one expects, also when
  seen for the first time.
- **Stability** (no drift): what was agreed stays agreed, over time and across
  repositories.
- **Simplicity** (no unnecessary complexity): exactly what is needed and easy to survey.

Means:

- **Consistency**: the same commands, structure and meaning everywhere, defined once
  instead of renegotiated per repository.
- **Frugality**: nothing is introduced without a reason, whether a plugin, a
  permission, a tool or a rule.
- **Reproducibility**: CI/CD that verifies from a neutral environment, as much as
  possible defined as code, and version pinning instead of moving targets.

Two measures qualify the means. Standardize only where there is no legitimate interest
in deviating. And weigh what cannot be undone more heavily than what can, as with
exposing a private repository.

Every Rule and Rule Set states why it exists in a `rule.yaml` or `ruleset.yaml` next to
its definition, following the [rule knowledge schemas](https://github.com/please-behave/cli/tree/main/why/contracts/rule-knowledge).

## Layout

```text
rulesets/
  <ruleset>/ruleset.toml                   # Policy bundles, grouped logically rather than by execution point
  <ruleset>/ruleset.yaml                   # Why the set exists and which Rules belong to it
rules/checks/<rule>/                       # rule.toml, rule.yaml, implementation and resources
rules/reviews/<rule>/                      # rule.toml, rule.yaml, prompt and resources
adapters/<id>/                           # adapter.toml and adapter
docs/                                     # Policy usage and compatibility details
```

Sets reference Rule directories relative to this repository root. Multiple sets
may reuse a Rule; consumers import adapter profile packages independently. Commands run
in the consumer root: `@rule/` selects the Rule directory, `@source/` this source
repository, and `@adapter/` an independently imported adapter profile directory.
Imported source resources must remain immutable.

## Configuration

Use published Please Behave 0.8.0 or later for the complete catalog. Install dependencies
explicitly; rules and adapter profiles never install tools. Pin each ruleset and adapter profile
import to a reviewed full Git commit, then run `please sync` to validate resources
and confirm managed hook installation. Synchronization does not execute checks or
call adapters. Subsequent execution loads the immutable cached revision offline;
the Gemini operations themselves still require network access.

| Bundle | Required consumer configuration | Tools and credentials |
| --- | --- | --- |
| Repository conventions | `agents-size.max_tokens`; `commit-message.scopes_file`; independent `gemini-flash-lite` and `gemini-flash` adapter profile imports | Python 3.9+ (3.11+ for the mise checks), Git, `jactionlint`, ShellCheck and `GEMINI_API_KEY` for the wording and `AGENTS.md` reviews |
| Open-source software | `license.target`, usually `LICENSE` | Python 3.9+, Git, `yq`; network and optionally `GITHUB_TOKEN` for `github-metadata` at `check` |
| Private repositories | none | Python 3.9+, Git, `yq`; `GITHUB_REPOSITORY` and `GITHUB_TOKEN` for `private-visibility` at `check` |
| Releasable open-source software | none | Python 3.11+, Git |
| Security | `dependency-audit-workflow.dependencies`, `audited` or `none` | Python 3.9+, Git, `yq`, Gitleaks v8 with `gitleaks dir` |

The Gemini adapters accept adapter protocol versions `0` and `1` and return the
version supplied in the request. Existing version `1` governors remain compatible;
no additional adapter call is made for version negotiation.

The wording and `AGENTS.md` quality Reviews are advisory; its FAIL and ERROR results remain visible without
blocking commits. Deterministic checks are blocking. Security and license checks
inspect the working tree at `check` and `HEAD` at `pre-push`, without Git history
or every pushed ref. Technical failures never establish PASS.

See complete import examples, parameters, evidence boundaries and limitations:

- [Repository conventions and Gemini](docs/repository-conventions.md)
- [Open-source software and license policy](docs/open-source-software.md)
- [Private repositories and Actions minutes](docs/private-repositories.md)
- [Release task and policy](docs/releases.md)
- [Security and scanner configuration](docs/security.md)
- [Compatibility and path migration](docs/migration.md)
- [Shared Renovate governor preset](docs/governor-updates.md)
- [Shared mise tasks](docs/mise-tasks.md)
- [Shared mise tools](docs/mise-tools.md)
- [Shared release actions](actions/release/README.md)
- [Shared dependency audit actions](actions/dependency-audit/README.md)

## Validation

This repository is governed by Please Behave itself: `.please/config.toml` imports
the `repository-conventions`, `security` and `open-source-software` bundles from this
repository's own published revisions, as any consumer does. It is not releasable, so
it imports no release bundle. Local Rules format Python with Ruff and run the unit
tests, which live next to the code as `test_*.py`. They use temporary local Git
repositories and mocked HTTP and must never call live AI providers or network APIs.
Workflow tooling and its offline tests live in `.github/scripts/` and run through
the same test gate.
Git hooks and the `CI` workflow are the validation gates; `mise run verify` gives quick
diagnosis. The CI workflow needs the `GEMINI_API_KEY` repository secret for the advisory
Reviews. See [AGENTS.md](AGENTS.md) for change boundaries and agent guidance.
