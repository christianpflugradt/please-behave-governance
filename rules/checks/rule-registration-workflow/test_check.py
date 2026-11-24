import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECK = Path(__file__).with_name("check.py")
CATALOG = "christianpflugradt/repository-catalog"
CALLER = """on:
  push:
    branches: [main]
    paths:
      - .please/checks/**/rule.toml
      - .please/checks/**/rule.yaml
      - .please/config.toml
      - .github/workflows/register-rules.yml
  workflow_dispatch:
permissions:
  contents: read
  id-token: write
jobs:
  register:
    uses: %s/.github/workflows/rule-registration.yml@%s
    secrets:
      GATEWAY_URL: ${{ secrets.GATEWAY_URL }}
      GATEWAY_KEY: ${{ secrets.GATEWAY_KEY }}
""" % (CATALOG, "a" * 40)


class RegistrationWorkflowTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.git("init", "-q")
        self.git("remote", "add", "origin", "git@github.com:example/owner.git")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run_check(self):
        return subprocess.run([sys.executable, "-B", CHECK], cwd=self.root, capture_output=True, text=True)

    def configure(self, caller=CALLER, publish='key_env = "PLEASE_GATEWAY_KEY"'):
        self.write(".please/config.toml", f"[publish]\n{publish}\n")
        self.write(".github/workflows/register-rules.yml", caller)
        self.write(".please/checks/sample/rule.toml", 'id = "sample"\nversion = "1.0"\n')
        self.write(".please/checks/sample/rule.yaml", "purpose: Test workflow coverage.\n")

    def test_allows_repositories_without_local_rules_and_documentation_examples(self):
        self.assertEqual(self.run_check().returncode, 0)
        self.git("remote", "set-url", "origin", "https://github.com/please-behave/docs")
        self.write("examples/project/rule.toml", "")
        self.assertEqual(self.run_check().returncode, 0)

    def test_accepts_versioned_rule_and_pinned_caller(self):
        self.configure()
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_accepts_oidc_with_or_without_a_key_and_job_permissions(self):
        caller = CALLER.replace("  id-token: write\n", "").replace(
            "  register:\n", "  register:\n    permissions:\n      contents: read\n      id-token: write\n"
        )
        key = "      GATEWAY_KEY: ${{ secrets.GATEWAY_KEY }}\n"
        for workflow in (caller, caller.replace(key, "")):
            with self.subTest(workflow=workflow):
                self.configure(workflow, publish='auth = "github-oidc"')
                result = self.run_check()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_job_permissions_override_workflow_permissions(self):
        self.configure(CALLER.replace("  register:\n", "  register:\n    permissions:\n      contents: read\n"))
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("contents: read and id-token: write", result.stdout)

    def test_oidc_requires_gateway_url(self):
        self.configure(CALLER.replace("      GATEWAY_URL: ${{ secrets.GATEWAY_URL }}\n", ""), publish='auth = "github-oidc"')
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("must pass GATEWAY_URL", result.stdout)

    def test_api_key_authentication_requires_gateway_key(self):
        self.configure(CALLER.replace("      GATEWAY_KEY: ${{ secrets.GATEWAY_KEY }}\n", ""))
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("pass GATEWAY_KEY for API-key authentication", result.stdout)

    def test_reports_missing_metadata_and_broad_filters(self):
        self.configure(CALLER.replace(".please/checks/**/rule.yaml", ".please/**"))
        self.write(".please/checks/sample/rule.toml", 'id = "sample"\n')
        (self.root / ".please/checks/sample/rule.yaml").unlink()
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        for message in ("rule.toml must declare a version", "rule.yaml is missing", "push.paths includes an unrelated path"):
            self.assertIn(message, result.stdout)

    def test_requires_review_prompt_in_the_push_filters(self):
        self.configure()
        self.write(".please/checks/sample/rule.toml", 'id = "sample"\nversion = "1.0"\n[review]\nprompt = "prompt.md"\n')
        result = self.run_check()
        self.assertEqual(result.returncode, 1)
        self.assertIn("push.paths must include .please/checks/**/prompt.md", result.stdout)


if __name__ == "__main__":
    unittest.main()
