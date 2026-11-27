# Why

Import the optional `rulesets/why` bundle if your repository documents with
the [Why standard](https://github.com/please-behave/why/tree/12211e6280c39f4affccfd805aba33ff73f1bc3b/standard).
The blocking `why-artifacts` and `why-links` checks read the working tree at
`pre-commit`, `pre-push` and `check`. They run offline with bundled schemas
and use Git, Python 3.9+, Mike Farah's `yq` v4 and Pandoc on `PATH`.

```toml
[[imports]]
git = "https://github.com/christianpflugradt/repository-catalog.git"
path = "rulesets/why"
ref = "<FULL_REVIEWED_COMMIT_SHA>"
```

Include [the bundle's mise tools](mise-tools.md) at the same commit. Run
`please sync` to resolve the import and install managed hooks. Run
`please run --at check` to check the repository on demand.

## Artifacts

`why-artifacts` covers `*.yaml` directly under `why/requirements`, `why/decisions`
and `why/components`, and `why/contracts/<interface>/api.yaml`. Every
artifact must conform to its schema and have a unique ID, including IDs
inside a contract index. Requirement, decision and component filenames match
their IDs. Contract indices use the fixed name `api.yaml`.

Unqualified references in schema-defined fields must point to local artifacts.
For example, a requirement's `decisions` list points to the decisions that
shaped it. Qualified references of the form `alias:ID` are checked for
syntax and artifact kind. Contract documents are regular `.md` files and
optional schemas are regular `.json` files with an object or Boolean value.
Their paths are relative to the index and cannot traverse symlinks.

A repository without `why/` passes. Other artifact folders are ignored.
Schema and reference violations return FAIL. Invalid artifact YAML or a
required tool failing to run returns ERROR.

## Repository declarations and links

`why-links` checks local links and anchors in Markdown files tracked by Git or
present in the working tree and eligible to be added. It ignores fenced code
blocks and empty link targets such as draft book entries.

Declare the current repository and the repositories you link to in
`why/repositories.yaml`. Aliases must be unique across the file. Use an alias
from the `linked` list for every qualified reference in Markdown or a Why
artifact. GitHub links to the same owner must name the current repository or a
linked repository. Each linked entry must be used by a qualified reference or
a link to its declared source.

Without a declaration, local links still need to resolve and qualified
references fail. Links to GitHub are then treated as external links. The
check uses only the repository snapshot. Checkout identification by Git remote
and verification of targets in other repositories are separate procedures.

For repositories with code, `rulesets/why-components` adds component ownership and requirement areas; see [Why components](why-components.md).
