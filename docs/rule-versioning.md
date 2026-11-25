# Rule versioning and registration

Each rule declares its version in `rule.toml` as `version = "A.B"`. Start new
rules at `1.0`. Whenever a rule definition or registered text changes, assign a
new version in the same commit. This includes changes to the rule's name,
description, review prompt or knowledge in `rule.yaml`. Raise the first number
for a fundamental change, such as `1.2` to `2.0`. Raise the second number for
other changes, such as `1.2` to `1.3`.

Each Rule Set declares `version = "A.B"` and a short display `name` in
`ruleset.toml`. Start new sets at `1.0`. Raise the set version in the same commit
whenever a member rule's version changes, the membership changes or the set's
registered text changes, such as its name, description or knowledge in `ruleset.yaml`.
Otherwise the ledger answers `conflict`. The set version follows the same `A.B` rules as
a rule version.

The **Register rules** workflow runs on pushes to `main` that touch the
registration paths and on manual dispatch. After the deterministic governance
checks pass, `please register` submits all committed rules owned by this
repository, including the catalog rules and the rules under `.please/checks/`, and then
the Rule Sets under `rulesets/`.
See [Gateway authentication](repository-conventions.md#gateway-authentication)
for the caller's permissions and secrets.

The Job Summary shows each rule's and Rule Set's outcome. A new version is `created` and
repeating an identical registration returns `unchanged`. Changing registered
content without raising the version produces a `conflict` and fails the
workflow. Commit a new version to resolve a conflict. For a transient gateway
failure, rerun the workflow.

Consumers need Please Behave 0.21.0 or newer before adopting a catalog revision
with versioned rules, and 0.23.0 or newer for revisions whose Rule Sets declare `version`
and `name`. Existing full commit pins keep pointing to the same
definitions. When using the catalog's Renovate presets, the consumer's
`please sync` check gates the catalog update until the governor can read it.
