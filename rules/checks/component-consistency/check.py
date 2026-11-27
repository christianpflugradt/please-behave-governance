"""Verify component ownership of compiled files, Rust dependencies between components and requirement areas."""

import fnmatch
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True  # Imported rule resources must remain immutable.

TEST_DIRECTORIES = {"tests", "__tests__"}
TEST_PATTERNS = ("tests.rs", "*_test.rs", "*_tests.rs", "*.test.*", "*.spec.*")
EMBED = re.compile(r'include_(?:str|bytes)!\(\s*"([^"]+)"\s*\)')
MIGRATE = re.compile(r'sqlx::migrate!\(\s*(?:"([^"]*)")?\s*\)')
TEST_CONFIGURATION = re.compile(r"^#\[cfg\([^\]]*\btest\b", re.MULTILINE)


def load_yaml(path):
    result = subprocess.run(["yq", "-o=json", "-I=0", ".", str(path)], capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"yq failed for {path}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def load_artifacts(root, folder, prefix):
    """Load every artifact of a kind. Their structure is validated by the why-artifacts Rule."""
    return {path.stem: load_yaml(path) for path in sorted((root / "why" / folder).glob(f"{prefix}-*.yaml"))}


def parse_roots(root, value):
    roots = []
    for entry in (part.strip() for part in value.split(",")):
        if not entry:
            continue
        path = PurePosixPath(entry)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"source root must be relative and stay inside the repository: {entry}")
        if not (root / path).is_dir():
            raise ValueError(f"source root is not a directory: {entry}")
        roots.append(path)
    return roots


def is_test_file(relative):
    return bool(TEST_DIRECTORIES & set(relative.parts[:-1])) or any(fnmatch.fnmatch(relative.name, pattern) for pattern in TEST_PATTERNS)


def source_files(root, source_roots):
    """Map each non-test file below a source root, relative to the repository, to its source root."""
    return {
        path.relative_to(root).as_posix(): source_root
        for source_root in source_roots
        for path in sorted((root / source_root).rglob("*"))
        if path.is_file() and not is_test_file(path.relative_to(root / source_root))
    }


def production_text(path):
    """The Rust source without the code behind its first test configuration attribute."""
    return TEST_CONFIGURATION.split(path.read_text(encoding="utf-8"), maxsplit=1)[0]


def crate_directory(root, path):
    for directory in [path.parent, *path.parent.parents]:
        if (directory / "Cargo.toml").is_file() or directory == root:
            return directory


def embedded_files(root, files):
    """Files the Rust sources embed at compile time, and errors for embeddings that do not resolve."""
    embedded, errors = set(), []
    for relative in sorted(files):
        if not relative.endswith(".rs"):
            continue
        path, text = root / relative, production_text(root / relative)
        targets = [(path.parent / target).resolve() for target in EMBED.findall(text)]
        for argument in MIGRATE.findall(text):
            migrations = (crate_directory(root, path) / (argument or "migrations")).resolve()
            if migrations.is_dir():
                targets += sorted(migrations.rglob("*.sql"))
            else:
                errors.append(
                    f"{relative}: embeds missing migrations directory {migrations.relative_to(root) if migrations.is_relative_to(root) else migrations}"
                )
        for target in targets:
            if not target.is_relative_to(root):
                errors.append(f"{relative}: embeds a file outside the repository: {target}")
            elif not target.is_file():
                errors.append(f"{relative}: embeds missing file {target.relative_to(root)}")
            else:
                embedded.add(target.relative_to(root).as_posix())
    return embedded, errors


def module_of(relative, source_root):
    return PurePosixPath(relative).relative_to(source_root).with_suffix("").parts[0]


def library_name(root, source_root):
    """The library crate name from the Cargo manifest above the source root, or None."""
    manifest = crate_directory(root, root / source_root / "x") / "Cargo.toml"
    if not manifest.is_file():
        return None
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    return data.get("lib", {}).get("name") or data.get("package", {}).get("name", "").replace("-", "_") or None


def root_reexports(root, source_root):
    """Map each item that lib.rs re-exports to the module that defines it."""
    library = root / source_root / "lib.rs"
    items = {}
    if library.is_file():
        for module, names in re.findall(r"pub use (\w+)::(\{[^}]*\}|\w+);", library.read_text(encoding="utf-8")):
            items.update({name: module for name in re.findall(r"\w+", names)})
    return items


def references(text, prefix):
    names = set(re.findall(rf"\b{prefix}::(\w+)", text))
    for group in re.findall(rf"use {prefix}::\{{(.*?)\}};", text, re.DOTALL):
        flat = re.sub(r"\{[^{}]*\}", "", group)
        names |= {re.split(r"::|\s", part.strip())[0] for part in flat.split(",") if part.strip()}
    return names


def observed_dependencies(root, files):
    """Edges (source file, source root, target module) from non-test Rust code."""
    rust = {relative: source_root for relative, source_root in files.items() if relative.endswith(".rs")}
    edges = set()
    for source_root in set(rust.values()):
        own = {relative for relative, other in rust.items() if other == source_root}
        modules = {module_of(relative, source_root) for relative in own}
        reexports = root_reexports(root, source_root)
        library = library_name(root, source_root)
        for relative in sorted(own):
            text = production_text(root / relative)
            names = references(text, "crate")
            if library and PurePosixPath(relative).parent == source_root and PurePosixPath(relative).name == "main.rs":
                names |= references(text, library)
            for name in names:
                target = reexports.get(name, name)
                if target in modules and target != module_of(relative, source_root):
                    edges.add((relative, source_root, target))
    return edges


def mutual_pairs(graph):
    """Pairs of components that depend on each other."""
    return sorted((a, b) for a in graph for b in graph[a] if a < b and a in graph.get(b, ()))


def check_areas(requirements):
    return [
        f"{identifier}: requirement has no area"
        for identifier, requirement in requirements.items()
        if not str(requirement.get("area", "")).strip()
    ]


def check_ownership(components, compiled, root):
    errors, owner = [], {}
    for identifier, component in components.items():
        for path in component["files"]:
            if path in owner:
                errors.append(f"{path}: assigned to both {owner[path]} and {identifier}")
            owner[path] = identifier
            if not (root / path).is_file():
                errors.append(f"{identifier}: file does not exist: {path}")
    errors += [f"{path}: compiled file belongs to no component" for path in sorted(compiled - set(owner))]
    errors += [f"{path}: assigned to {owner[path]} but not part of the compiled product" for path in sorted(set(owner) - compiled)]
    return owner, errors


def check_dependencies(components, owner, files, edges):
    """Compare declared and observed dependencies for components that own Rust modules."""
    errors = []
    module_owner = {}
    for relative, source_root in files.items():
        if relative.endswith(".rs") and relative in owner:
            module_owner[(source_root, module_of(relative, source_root))] = owner[relative]
    checked = {owner[relative] for relative in files if relative.endswith(".rs") and relative in owner}
    found = {identifier: {} for identifier in components}
    for source, source_root, target in sorted(edges):
        origin, destination = owner.get(source), module_owner.get((source_root, target))
        if origin and destination and origin != destination:
            found[origin].setdefault(destination, []).append(f"{PurePosixPath(source).name} uses {target}")
    for identifier in sorted(checked):
        declared = set(components[identifier]["depends_on"])
        if identifier in declared:
            errors.append(f"{identifier}: depends on itself")
        for missing in sorted(set(found[identifier]) - declared):
            errors.append(f"{identifier}: undeclared dependency on {missing} ({found[identifier][missing][0]})")
        for stale in sorted((declared & checked) - set(found[identifier]) - {identifier}):
            errors.append(f"{identifier}: declared dependency on {stale} is not used by any source file")
    notes = [" <-> ".join(pair) for pair in mutual_pairs({k: set(v) for k, v in found.items()})]
    return errors, notes


def check(root, source_roots):
    root = root.resolve()
    requirements = load_artifacts(root, "requirements", "REQ")
    components = load_artifacts(root, "components", "COMP")
    files = source_files(root, source_roots)
    embedded, errors = embedded_files(root, files)
    owner, ownership_errors = check_ownership(components, set(files) | embedded, root)
    dependency_errors, notes = check_dependencies(components, owner, files, observed_dependencies(root, files))
    return requirements, components, errors + check_areas(requirements) + ownership_errors + dependency_errors, notes


def main():
    if len(sys.argv) != 2:
        raise ValueError("expected 1 argument: source_roots")
    root = Path.cwd().resolve()
    source_roots = parse_roots(root, sys.argv[1])
    if not source_roots:
        print("PASS: no source roots declared")
        return
    requirements, components, errors, notes = check(root, source_roots)
    assert not errors, "\n".join(errors)
    for note in notes:
        print(f"NOTE: mutual dependency {note}")
    print(f"PASS: {len(components)} components cover every compiled file exactly once and {len(requirements)} requirements carry an area")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as error:  # A verdict about the repository.
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
    except Exception as error:  # A crash is a technical failure, never a FAIL verdict.
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(2)
