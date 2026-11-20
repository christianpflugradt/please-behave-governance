# Shared mise tools

The Rules of a ruleset run external tools such as `jactionlint`, ShellCheck, `shfmt`, `yq` and
`gitleaks`. Each ruleset that needs tools ships them as `rulesets/<ruleset>/mise.toml`, so a
repository gets the tools its Rules require together with the Rules, and a Rule that needs a new
tool or version can never reach a repository without it.

| Ruleset | Tools |
| --- | --- |
| `repository-conventions` | `jactionlint`, `shellcheck`, `shfmt` |
| `security` | `gitleaks`, `yq` |
| `open-source-software` | `yq` |
| `private-repositories` | `yq` |
| `releasable-open-source-software` | `yq` |

Languages, runtimes, formatters of the repository's own sources and the pinned
`github:please-behave/cli` stay in the repository's `mise.toml`.

## Import

Include the file of every imported ruleset in the root `mise.toml`, pinned to the same full commit
SHA as the `[[imports]]` entry of that ruleset (mise 2026.10 or newer):

```toml
include = [
  "git::https://github.com/christianpflugradt/please-behave-governance.git//rulesets/repository-conventions/mise.toml?ref=<FULL_COMMIT_SHA>",
]
```

Entries of the repository's own `[tools]` override included ones, so drop local pins of these
tools instead of keeping two versions. An include does not nest.

## Updates

Both the `renovate-governor` and the `renovate-content-aware` preset track the `ref` of such an
include. With the content-aware preset it shares its dependency with the ruleset import, so one
PR moves the Rules and their tools together. Tool versions are maintained once, here, by Renovate's
mise manager.
