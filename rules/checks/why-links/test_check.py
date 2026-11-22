"""Offline tests for the Why declaration and link rule."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECK = Path(__file__).resolve().with_name("check.py")


class WhyLinksTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.env = os.environ.copy()
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR"):
            self.env.pop(name, None)
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        subprocess.run(["git", "init", "--quiet", "--template="], cwd=self.root, env=self.env, check=True)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def declare(self, *linked):
        lines = ["repository:", "  alias: current", "  source: github:Example-Org/current", "linked:" if linked else "linked: []"]
        for alias, source in linked:
            lines.extend((f"  - alias: {alias}", f"    source: {source}"))
        self.write("why/repositories.yaml", "\n".join(lines) + "\n")

    def verdict(self, code, *messages, env=None):
        result = subprocess.run(
            [sys.executable, str(CHECK)], cwd=self.root, env=env or self.env, capture_output=True, text=True, check=False
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, code, output)
        for message in messages:
            self.assertIn(message, output)

    def test_declaration_used_by_markdown_yaml_and_github_links(self):
        self.declare(
            ("shared", "github:example-org/shared-repo"),
            ("contracts", "github:example-org/contracts"),
            ("guide", "github:other-org/guide"),
        )
        self.write(
            "README.md",
            "See `shared:DEC-0001`, [a guide][guide], and "
            "https://github.com/EXAMPLE-ORG/CURRENT/issues/1.\n\n"
            "[guide]: https://github.com/Other-Org/Guide/blob/main/README.md\n",
        )
        self.write("why/requirements/REQ-0001.yaml", "decisions: [contracts:DEC-0001]\n")
        self.verdict(0, "PASS")

    def test_no_why_or_declaration(self):
        self.write("README.md", "[Other repo](https://github.com/example-org/other)\n")
        self.verdict(0)

    def test_reference_without_declaration(self):
        self.write("README.md", "See `shared:DEC-0001`.\n")
        self.verdict(1, "undeclared Why alias shared")

    def test_unknown_and_own_aliases_are_not_linked(self):
        self.declare()
        self.write("README.md", "See shared:CON-0001 and current:API-0001.\n")
        self.verdict(1, "undeclared Why alias shared", "undeclared Why alias current")

    def test_undeclared_same_owner_repo_and_unused_entry(self):
        self.declare(("unused", "github:example-org/unused"))
        self.write("README.md", "[Unlisted](https://github.com/Example-Org/unlisted/pull/1)\n")
        self.verdict(1, "undeclared GitHub repository example-org/unlisted", "unused linked repository unused")

    def test_unrelated_owner_does_not_need_declaration(self):
        self.declare()
        self.write("README.md", "[Other](https://github.com/other-org/unlisted)\n")
        self.verdict(0)

    def test_same_source_different_aliases_can_have_unique_usage(self):
        self.declare(("shared", "github:example-org/shared"), ("alternate", "github:example-org/shared"))
        self.write("README.md", "See `shared:REQ-0001`.\n")
        self.verdict(1, "unused linked repository alternate")

    def test_duplicate_aliases(self):
        for entries in (
            (("current", "github:example-org/other"),),
            (("shared", "github:example-org/shared"), ("shared", "github:example-org/other")),
        ):
            with self.subTest(entries=entries):
                self.declare(*entries)
                self.verdict(1, "duplicate alias")

    def test_invalid_declaration(self):
        for content in (
            "repository: {alias: current, source: github:example-org/current}\n",
            "repository: {alias: Current, source: github:example-org/current}\nlinked: []\n",
            "repository: {alias: current, source: github:example-org/..}\nlinked: []\n",
            "repository: {alias: current, source: https://github.com/example-org/current}\nlinked: []\n",
            "repository: {alias: current, source: github:example-org/current}\nlinked: {}\n",
            "repository: {alias: current, source: github:example-org/current}\nlinked: []\nextra: false\n",
            "repository: [\n",
        ):
            with self.subTest(content=content):
                self.write("why/repositories.yaml", content)
                self.verdict(1, "FAIL")

    def test_local_links_and_anchors_resolve(self):
        self.write(
            "docs/README.md",
            "# Start\n\n"
            "[Here](#start) [Next][next] [Again](/docs/Next%20Page.md#heading-1) "
            "[Marker](Next%20Page.md#marker) [File](../LICENSE) [Folder](../docs/)\n\n"
            "[next]: <Next Page.md#heading>\n",
        )
        self.write("docs/Next Page.md", 'Heading\n=======\n\n## Heading\n\n<a name="marker"></a>\n')
        self.write("LICENSE", "License text.\n")
        self.verdict(0)

    def test_missing_local_destinations_fail_even_without_declaration(self):
        self.write("README.md", "# Present\n\n[Broken](absent.md) [Missing](#absent) [Outside](../outside.md)\n")
        self.verdict(1, "missing local target 'absent.md'", "missing anchor '#absent'", "missing local target '../outside.md'")

    def test_ignored_files_and_symlink_targets_are_outside_snapshot(self):
        self.write(".gitignore", "generated.md\n")
        self.write("generated.md", "[Missing](missing.md)\n")
        self.write("README.md", "See [generated](generated.md) and [symlink](alias.md).\n")
        (self.root / "alias.md").symlink_to("README.md")
        self.verdict(1, "missing local target 'generated.md'", "missing local target 'alias.md'")

    def test_fenced_examples_and_empty_draft_destinations_are_ignored(self):
        self.declare()
        self.write(
            "README.md",
            "- [Draft]()\n\n"
            "````markdown\n```\n[Broken](missing.md) shared:DEC-0001 https://github.com/example-org/unlisted\n```\n````\n\n"
            "> ~~~markdown\n> [Broken](missing.md) shared:REQ-0001\n> ~~~\n",
        )
        self.verdict(0)

    def test_missing_tools_are_errors(self):
        self.verdict(2, "ERROR: missing file or tool: git", env=dict(self.env, PATH=""))


if __name__ == "__main__":
    unittest.main()
