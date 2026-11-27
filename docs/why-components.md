# Why components

Import the optional `rulesets/why-components` bundle if your repository documents with
[Why](why.md) and contains code that its components own. It is a separate bundle because its
Rule needs the `source_roots` parameter: Please Behave resolves every declared parameter of an
imported Rule and has no defaults, so repositories without code, such as documentation, import
the [`why` bundle](why.md) only.

```toml
[[imports]]
git = "https://github.com/christianpflugradt/repository-catalog.git"
path = "rulesets/why-components"
ref = "<FULL_REVIEWED_COMMIT_SHA>"

[imports.rule_parameters.component-consistency]
source_roots = "src,frontend/src"
```

Include [the bundle's mise tools](mise-tools.md) at the same commit. The Check needs Python 3.11+
(`tomllib`) and `yq` v4 on `PATH`. It reads the working tree at `pre-commit`, `pre-push` and `check`.

## Parameter

The required string parameter `source_roots` lists the directories that hold the repository's
code, separated by commas and relative to the repository root, for example `src` or
`src,frontend/src`. An empty value declares no code, and the Check passes without checking
anything. An absolute path, a `..` segment or a directory that does not exist returns ERROR.

## Checks

1. Every file below a source root and every file the Rust sources embed at compile time belongs
   to exactly one component's `files`. Embedded files come from `include_str!`, `include_bytes!`
   (relative to the source file) and `sqlx::migrate!`, which embeds the `.sql` files of its
   migrations directory (`migrations` unless named) next to the nearest `Cargo.toml`. This includes
   files below `why/contracts` that the code embeds. An embedded file that does not exist returns FAIL,
   so a file produced by a build step must exist when the Check runs.
2. Every file a component lists exists and is part of that compiled product.
3. For each component that owns Rust modules, `depends_on` equals the dependencies observed in the
   source, in both directions, expressed between components. Dependencies come from `crate::` paths,
   the items `lib.rs` re-exports and, for a `main.rs` directly below a source root, the library crate
   name from `Cargo.toml`. A module is the first path segment below its source root. Components
   that own no Rust module, such as TypeScript ones, get ownership checks only, and a dependency
   from a Rust component on one of them is not verified. Mutual dependencies are printed as a note
   and do not fail.
4. Every requirement in `why/requirements` has a non-empty `area`. The area is a free string chosen
   by the repository.

Components describe the compiled product, so test files are ignored: files below a `tests` or
`__tests__` directory and files named `tests.rs`, `*_test.rs`, `*_tests.rs`, `*.test.*` or
`*.spec.*`, and in Rust sources everything after the first test configuration attribute. There
is no exclude parameter.

Violations return FAIL (1) and are listed individually; invalid configuration, a missing tool or
unreadable artifacts return ERROR (2). Artifact structure and references stay with the
[`why` bundle](why.md).
