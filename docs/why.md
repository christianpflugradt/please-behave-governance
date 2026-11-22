# Why

Import the optional `rulesets/why` bundle if your repository documents with
the [Why standard](https://github.com/please-behave/why/tree/12211e6280c39f4affccfd805aba33ff73f1bc3b/standard).
The blocking `why-artifacts` check reads the working tree at `pre-commit`,
`pre-push` and `check`. It needs Python 3.9+ and Mike Farah's `yq` v4 on
`PATH`. It runs offline with the schemas bundled in the Rule.

```toml
[[imports]]
git = "https://github.com/christianpflugradt/repository-catalog.git"
path = "rulesets/why"
ref = "<FULL_REVIEWED_COMMIT_SHA>"
```

Include [the bundle's mise tools](mise-tools.md) at the same commit. Run
`please sync` to resolve the import and install managed hooks. Run
`please run --at check` to check the repository on demand.

The Rule covers `*.yaml` directly under `why/requirements`, `why/decisions`
and `why/components`, and `why/contracts/<interface>/api.yaml`. Every
artifact must conform to its schema and have a unique ID, including IDs
inside a contract index. Requirement, decision and component filenames match
their IDs. Contract indices use the fixed name `api.yaml`.

Unqualified references in schema-defined fields must point to local artifacts.
For example, a requirement's `decisions` list points to the decisions that
shaped it. Qualified references such as `shared:DEC-0001` are checked for
syntax and artifact kind. Contract documents are regular `.md` files and
optional schemas are regular `.json` files with an object or Boolean value.
Their paths are relative to the index and cannot traverse symlinks.

A repository without `why/` passes. Other artifact folders are ignored.
Schema and reference violations return FAIL. Invalid artifact YAML or a
required tool failing to run returns ERROR. Repository declarations, links in
prose and code-specific checks are outside the scope of this Rule.
