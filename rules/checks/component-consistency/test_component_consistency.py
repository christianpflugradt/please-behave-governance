"""The component consistency check judges ownership, Rust dependencies and requirement areas and reports crashes as exit 2."""

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("check.py")
spec = importlib.util.spec_from_file_location("component_check", SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def component(identifier, files, depends_on=()):
    return {"id": identifier, "name": identifier, "files": list(files), "depends_on": list(depends_on)}


class GraphTests(unittest.TestCase):
    def test_references_read_paths_and_use_groups(self):
        text = "use crate::{config::{self, Check}, limits::*, planner};\nlet x = crate::report::Item;"
        self.assertEqual(check.references(text, "crate"), {"config", "limits", "planner", "report"})

    def test_mutual_pairs_list_each_pair_once(self):
        graph = {"A": {"B", "C"}, "B": {"A"}, "C": set()}
        self.assertEqual(check.mutual_pairs(graph), [("A", "B")])

    def test_test_files_follow_conventions(self):
        tests = ["tests/a.rs", "src/tests.rs", "src/a_test.rs", "web/__tests__/a.ts", "web/a.test.ts", "web/a.spec.tsx"]
        for name in tests:
            self.assertTrue(check.is_test_file(Path(name)), name)
        for name in ["src/a.rs", "web/a.ts", "web/contest.ts"]:
            self.assertFalse(check.is_test_file(Path(name)), name)


@unittest.skipUnless(shutil.which("yq"), "yq is required to read YAML")
class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.file("Cargo.toml", '[package]\nname = "demo-app"\n')
        self.file("src/lib.rs", "pub mod a;\npub mod b;\n")
        self.file("src/a.rs", "use crate::b;\n")
        self.file("src/b.rs", "")
        self.write(component("COMP-0001", ["src/lib.rs", "src/a.rs"], ["COMP-0002"]))
        self.write(component("COMP-0002", ["src/b.rs"]))
        self.write(requirement("REQ-0001", "core"), "requirements")

    def file(self, name, content=""):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def write(self, value, folder="components"):
        self.file(f"why/{folder}/{value['id']}.yaml", json.dumps(value))

    def errors(self, roots=("src",)):
        return check.check(self.root, [Path(root) for root in roots])[2]

    def assert_error(self, *words, roots=("src",)):
        errors = self.errors(roots)
        self.assertTrue(any(all(word in error for word in words) for error in errors), errors)

    def test_a_consistent_rust_map_passes(self):
        self.assertEqual(self.errors(), [])

    def test_a_file_without_component_fails(self):
        self.file("src/c.rs")
        self.assert_error("src/c.rs", "no component")

    def test_a_file_in_two_components_fails(self):
        self.write(component("COMP-0002", ["src/b.rs", "src/a.rs"]))
        self.assert_error("assigned to both")

    def test_a_listed_file_outside_the_compiled_product_fails(self):
        self.file("README.md")
        self.write(component("COMP-0002", ["src/b.rs", "README.md"]))
        self.assert_error("README.md", "not part of the compiled product")

    def test_a_missing_listed_file_fails(self):
        self.write(component("COMP-0002", ["src/b.rs", "src/gone.rs"]))
        self.assert_error("file does not exist: src/gone.rs")

    def test_an_undeclared_dependency_fails(self):
        self.write(component("COMP-0001", ["src/lib.rs", "src/a.rs"]))
        self.assert_error("undeclared dependency on COMP-0002")

    def test_a_stale_dependency_fails(self):
        self.write(component("COMP-0002", ["src/b.rs"], ["COMP-0001"]))
        self.assert_error("not used by any source file")

    def test_a_component_cannot_depend_on_itself(self):
        self.write(component("COMP-0002", ["src/b.rs"], ["COMP-0002"]))
        self.assert_error("depends on itself")

    def test_test_only_code_does_not_count_as_a_dependency(self):
        self.file("src/b.rs", "#[cfg(all(test, unix))]\nmod tests { use crate::a; }\n")
        self.assertEqual(self.errors(), [])

    def test_test_files_need_no_component(self):
        self.file("src/tests.rs", "use crate::a;\n")
        self.file("src/b_test.rs")
        self.assertEqual(self.errors(), [])

    def test_the_binary_depends_on_the_library_by_crate_name(self):
        self.file("src/main.rs", "use demo_app::a;\n")
        self.write(component("COMP-0003", ["src/main.rs"], ["COMP-0001"]))
        self.assertEqual(self.errors(), [])

    def test_reexported_items_resolve_to_their_module(self):
        self.file("src/lib.rs", "pub mod a;\npub mod b;\npub use b::Item;\n")
        self.file("src/a.rs", "use crate::Item;\n")
        self.assertEqual(self.errors(), [])

    def test_an_embedded_file_must_belong_to_a_component(self):
        self.file("why/contracts/text.md")
        self.file("src/b.rs", 'const T: &str = include_str!("../why/contracts/text.md");\n')
        self.assert_error("why/contracts/text.md", "no component")

    def test_an_owned_embedded_file_passes(self):
        self.file("why/contracts/text.md")
        self.file("src/b.rs", 'const T: &str = include_str!("../why/contracts/text.md");\n')
        self.write(component("COMP-0002", ["src/b.rs", "why/contracts/text.md"]))
        self.assertEqual(self.errors(), [])

    def test_a_missing_embedded_file_fails(self):
        self.file("src/b.rs", 'const T: &str = include_str!("gone.txt");\n')
        self.assert_error("embeds missing file")

    def test_migrations_are_embedded_by_the_macro(self):
        self.file("migrations/0001_init.sql")
        self.file("src/b.rs", "static M: Migrator = sqlx::migrate!();\n")
        self.assert_error("migrations/0001_init.sql", "no component")
        self.write(component("COMP-0002", ["src/b.rs", "migrations/0001_init.sql"]))
        self.assertEqual(self.errors(), [])

    def test_embedded_files_of_test_code_are_ignored(self):
        self.file("src/b.rs", '#[cfg(test)]\nmod tests { const T: &str = include_str!("gone.txt"); }\n')
        self.assertEqual(self.errors(), [])

    def test_mixed_roots_check_ownership_of_typescript_only(self):
        self.file("web/src/app.ts", "import './util';\n")
        self.file("web/src/util.ts")
        self.file("web/src/app.test.ts")
        self.write(component("COMP-0003", ["web/src/app.ts", "web/src/util.ts"], ["COMP-0001"]))
        self.assertEqual(self.errors(("src", "web/src")), [])
        self.file("web/src/extra.ts")
        self.assert_error("web/src/extra.ts", "no component", roots=("src", "web/src"))

    def test_a_requirement_without_area_fails(self):
        self.write({"id": "REQ-0002", "name": "R"}, "requirements")
        self.assert_error("REQ-0002", "no area")

    def test_a_blank_area_fails(self):
        self.write(requirement("REQ-0002", "  "), "requirements")
        self.assert_error("REQ-0002", "no area")


def requirement(identifier, area):
    return {"id": identifier, "name": "Requirement", "area": area}


class RunTests(unittest.TestCase):
    def run_check(self, root, *arguments):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *arguments], cwd=root, capture_output=True, text=True, check=False)

    def test_no_roots_declared_does_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            for value in ("", " , "):
                result = self.run_check(directory, value)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.startswith("PASS:"))

    @unittest.skipUnless(shutil.which("yq"), "yq is required to read YAML")
    def test_a_violation_fails_with_exit_1(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "src").mkdir()
            (Path(directory) / "src/a.rs").write_text("")
            result = self.run_check(directory, "src")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertTrue(result.stderr.startswith("FAIL:"))

    def test_an_invalid_root_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            for value in ("missing", "../outside", "/absolute"):
                self.assertEqual(self.run_check(directory, value).returncode, 2, value)

    def test_a_missing_argument_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_check(directory)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertTrue(result.stderr.startswith("ERROR:"))


if __name__ == "__main__":
    unittest.main()
