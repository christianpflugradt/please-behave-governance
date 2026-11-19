"""Offline check that every shared action manifest is valid YAML with the keys GitHub requires."""

from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ActionManifestTests(unittest.TestCase):
    def test_manifests_parse_and_declare_a_composite_run(self):
        manifests = sorted((ROOT / "actions").rglob("action.yml"))
        self.assertTrue(manifests)
        for manifest in manifests:
            with self.subTest(manifest=str(manifest.relative_to(ROOT))):
                for expression in ('has("name")', 'has("description")', '.runs.using == "composite"'):
                    result = subprocess.run(["yq", "-e", expression, str(manifest)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr or expression)


if __name__ == "__main__":
    unittest.main()
