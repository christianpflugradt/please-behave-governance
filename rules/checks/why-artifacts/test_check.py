"""Offline tests for the imported Why artifact check."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECK = Path(__file__).with_name("check.py").resolve()
REQUIREMENT = {"id": "REQ-0001", "name": "Requirement", "description": "A condition.", "rationale": "A reason."}
DECISION = {"id": "DEC-0001", "name": "Decision", "context": "A context.", "decision": "A choice.", "rationale": "A reason."}
COMPONENT = {
    "id": "COMP-0001",
    "name": "Component",
    "purpose": "A responsibility.",
    "scope": ["A responsibility."],
    "out_of_scope": [{"description": "Another responsibility."}],
    "must_not": ["A forbidden action."],
    "files": ["src/example.rs"],
    "depends_on": [],
}
CONTRACT = {"id": "CON-0001", "name": "Contract", "document": "spec/interface.md", "requirements": ["REQ-0001"]}


class WhyArtifactsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        return path

    def requirement(self, **fields):
        self.write("why/requirements/REQ-0001.yaml", {**REQUIREMENT, **fields})

    def decision(self, **fields):
        self.write("why/decisions/DEC-0001.yaml", {**DECISION, **fields})

    def component(self, **fields):
        self.write("why/components/COMP-0001.yaml", {**COMPONENT, **fields})

    def index(self, *contracts, interface="example", identifier="API-0001"):
        self.write(
            f"why/contracts/{interface}/api.yaml",
            {"id": identifier, "name": "API", "version": 1, "contracts": list(contracts)},
        )

    def verdict(self, expected, *messages, env=None):
        result = subprocess.run([sys.executable, "-B", str(CHECK)], cwd=self.root, capture_output=True, text=True, env=env, check=False)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, expected, output)
        self.assertTrue(output.startswith(("PASS:", "FAIL:", "ERROR:")[expected]), output)
        for message in messages:
            self.assertIn(message, output)

    def test_valid_artifacts_and_qualified_references(self):
        self.requirement(decisions=["DEC-0001", "shared:DEC-0042"])
        self.decision(supersedes=["shared:DEC-0003"])
        self.component(requirements=["REQ-0001"], depends_on=["shared:COMP-0042"])
        self.index({**CONTRACT, "schema": "spec/interface.json", "requirements": ["REQ-0001", "shared:REQ-0042"]})
        self.write("why/contracts/example/spec/interface.md", "# Interface\n")
        self.write("why/contracts/example/spec/interface.json", {"type": "object"})
        self.verdict(0, "5 Why artifact IDs")

    def test_schema_violation_and_wrong_reference_kind_fail(self):
        self.requirement(description=[], decisions=["shared:REQ-0001"])
        self.verdict(1, ".description: expected string", ".decisions[0]: does not match")

    def test_id_must_match_filename(self):
        self.requirement(id="REQ-0002")
        self.verdict(1, "ID REQ-0002 does not match the file name")

    def test_duplicate_ids_fail_across_indices_and_inside_one_index(self):
        self.requirement()
        self.index(CONTRACT)
        self.index(CONTRACT, CONTRACT, interface="other")
        for interface in ("example", "other"):
            self.write(f"why/contracts/{interface}/spec/interface.md", "# Interface\n")
        self.verdict(1, "duplicate ID API-0001", "duplicate ID CON-0001")

    def test_local_reference_fields_must_resolve(self):
        self.requirement(decisions=["DEC-0002"])
        self.decision(supersedes=["DEC-0003"])
        self.component(
            depends_on=["COMP-0002"],
            out_of_scope=[{"description": "Another responsibility.", "owner": "COMP-0003"}],
            requirements=["REQ-0002"],
        )
        self.index({**CONTRACT, "requirements": ["REQ-0003"]})
        self.write("why/contracts/example/spec/interface.md", "# Interface\n")
        self.verdict(
            1,
            ".decisions[0]: unknown local reference DEC-0002",
            ".supersedes[0]: unknown local reference DEC-0003",
            ".depends_on[0]: unknown local reference COMP-0002",
            ".out_of_scope[0].owner: unknown local reference COMP-0003",
            ".requirements[0]: unknown local reference REQ-0002",
            ".contracts[0].requirements[0]: unknown local reference REQ-0003",
        )

    def test_missing_wrong_type_and_symlink_resource_fail(self):
        self.requirement()
        self.index(CONTRACT)
        self.verdict(1, "CON-0001.document must name a local .md file")

        self.index({**CONTRACT, "document": "spec/interface.txt", "schema": "spec/interface.json"})
        document = self.write("why/contracts/example/spec/interface.txt", "# Interface\n")
        self.write("why/contracts/example/spec/interface.json", "[]")
        self.verdict(1, "CON-0001.document", "CON-0001.schema")

        self.index(CONTRACT)
        document.with_suffix(".md").symlink_to(document.name)
        self.verdict(1, "CON-0001.document")

        resources = document.parent.rename(self.root / "resources")
        document.parent.symlink_to(resources, target_is_directory=True)
        (resources / "interface.md").unlink()
        (resources / "interface.md").write_text("# Interface\n", encoding="utf-8")
        self.verdict(1, "CON-0001.document")

    def test_contract_resource_path_cannot_leave_its_index(self):
        self.requirement()
        self.index({**CONTRACT, "document": "../outside.md"})
        self.write("why/contracts/outside.md", "# Interface\n")
        self.verdict(1, ".document: does not match")

    def test_repository_without_why_passes_without_yq(self):
        self.verdict(0, env={**os.environ, "PATH": str(self.root)})

    def test_other_kind_and_repository_declaration_are_ignored(self):
        self.write("why/actions/example.yaml", "[malformed")
        self.write("why/repositories.yaml", "[malformed")
        self.verdict(0)

    def test_invalid_yaml_is_error_even_with_a_schema_violation(self):
        self.requirement(description=[])
        self.write("why/decisions/DEC-0001.yaml", "[malformed")
        self.verdict(2, "yq could not parse YAML")

    def test_multiple_yaml_documents_and_duplicate_keys_are_errors(self):
        for invalid in ("name: first\nname: second\n", "---\nfirst: 1\n---\nsecond: 2\n"):
            with self.subTest(invalid=invalid):
                self.write("why/requirements/REQ-0001.yaml", invalid)
                self.verdict(2, "expected a single YAML document with unique mapping keys")

    def test_missing_yq_is_error_when_artifacts_exist(self):
        self.requirement()
        self.verdict(2, "missing file or tool: yq", env={**os.environ, "PATH": str(self.root)})


if __name__ == "__main__":
    unittest.main()
