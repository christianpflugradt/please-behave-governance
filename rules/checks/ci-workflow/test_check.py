import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("ci_workflow", Path(__file__).with_name("check.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class CiWorkflowTests(unittest.TestCase):
    def test_gateway_url_accepts_an_optional_key(self):
        gemini = {"GEMINI_API_KEY": "${{ secrets.GEMINI_API_KEY }}"}
        url = {"GATEWAY_URL": "${{ secrets.GATEWAY_URL }}"}
        key = {"GATEWAY_KEY": "${{ secrets.GATEWAY_KEY }}"}
        job = {
            "uses": f"christianpflugradt/repository-catalog/.github/workflows/ci-check.yml@{'a' * 40}",
            "permissions": {"contents": "read", "id-token": "write"},
        }
        for secrets in (gemini, gemini | url, gemini | url | key):
            with self.subTest(secrets=secrets):
                self.assertEqual(mod.check_violations(job | {"secrets": secrets}), [])
        self.assertTrue(mod.check_violations(job | {"secrets": gemini | key}))


if __name__ == "__main__":
    unittest.main()
