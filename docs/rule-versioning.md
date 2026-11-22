# Rule versioning and registration

Each rule declares its version in `rule.toml` as `version = "A.B"`. Start new
rules at `1.0`. Whenever a rule definition or registered text changes, assign a
new version in the same commit. This includes changes to the rule's name,
description, review prompt or knowledge in `rule.yaml`. Raise the first number
for a fundamental change, such as `1.2` to `2.0`. Raise the second number for
other changes, such as `1.2` to `1.3`.

The **Register rules** workflow runs on every push to `main`. After the
deterministic governance checks pass, the Please Behave Action submits all
committed rules owned by this repository to the gateway using the
`GATEWAY_URL` and `GATEWAY_KEY` repository secrets. This includes the catalog
rules and the repository's own rules under `.please/checks/`.

The Job Summary shows each rule's outcome. A new version is `created` and
repeating an identical registration returns `unchanged`. Changing registered
content without raising the version produces a `conflict` and fails the
workflow. Commit a new version to resolve a conflict. For a transient gateway
failure, rerun the workflow.

Consumers need Please Behave 0.21.0 or newer before adopting a catalog revision
with versioned rules. Existing full commit pins keep pointing to the same
definitions. When using the catalog's Renovate presets, the consumer's
`please sync` check gates the catalog update until the governor can read it.
