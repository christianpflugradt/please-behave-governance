"""Offline candidate evidence checks without installing or invoking governance."""

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("evidence", Path(__file__).with_name("release_evidence.py"))
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.head = "a" * 40
        (self.root / "policy.json").write_text(
            json.dumps(dict(root="dist", targets={"macos": ["App-{version}.zip", "metadata.json", "SHA256SUMS"]}))
        )
        self.env = dict(
            EXPECTED_RELEASE_VERSION="1.3.0",
            GITHUB_SHA=self.head,
            GITHUB_REF="refs/heads/main",
            GITHUB_REPOSITORY="christianpflugradt/PassingBy",
            GITHUB_WORKFLOW_REF="christianpflugradt/PassingBy/.github/workflows/release.yml@refs/heads/main",
            GITHUB_RUN_ID="123",
            GITHUB_RUN_ATTEMPT="1",
        )
        self.policy = evidence.policy_from(self.root / "policy.json", self.env)
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        for name in set(sum(self.policy["targets"].values(), [])):
            path = self.root / self.policy["root"] / name.replace("{version}", "1.3.0")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("tested source")
        for target in self.policy["targets"]:
            evidence.record(self.policy, dict(self.env, RELEASE_TARGET=target), self.head)

    def test_policy_takes_identity_from_the_context_and_rejects_unknown_fields(self):
        self.assertEqual(self.policy["repository"], "christianpflugradt/PassingBy")
        self.assertEqual(self.policy["workflow"], ".github/workflows/release.yml")
        path = self.root / "policy.json"
        path.write_text(json.dumps(dict(root="dist", targets={"macos": ["a"]}, schema_version=1)))
        with self.assertRaises(ValueError):
            evidence.policy_from(path, self.env)
        path.write_text(json.dumps(dict(root="dist", targets={"macos": ["a"]})))
        with self.assertRaises(ValueError):
            evidence.policy_from(path, dict(self.env, GITHUB_REPOSITORY="attacker/repo"))

    def test_all_candidates_verify_without_running_governance(self):
        with patch.object(evidence.subprocess, "run", side_effect=AssertionError("No governance invocation")):
            evidence.verify(self.policy, self.env, self.head)
        self.assertFalse((self.root / ".git/hooks").exists())

    def test_missing_extra_mismatched_or_modified_evidence_is_rejected(self):
        path = next((self.root / "release-evidence").iterdir())
        original = path.read_text()
        for field, value in (
            ("version", "9.9.9"),
            ("source_sha", "b" * 40),
            ("run_id", "456"),
            ("run_attempt", "2"),
            ("repository", "other/repository"),
            ("target", "other"),
            ("workflow", ".github/workflows/other.yml"),
            ("artifacts", {}),
        ):
            with self.subTest(field=field):
                data = json.loads(original)
                data[field] = value
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    evidence.verify(self.policy, self.env, self.head)
                path.write_text(original)
        path.unlink()
        with self.assertRaises(ValueError):
            evidence.verify(self.policy, self.env, self.head)
        path.write_text(original)
        extra = path.with_name("unexpected.json")
        extra.write_text(original)
        with self.assertRaises(ValueError):
            evidence.verify(self.policy, self.env, self.head)
        extra.unlink()

    def archive_path(self):
        return self.root / "release-candidate" / "macos.tar"

    def rewrite_archive(self, change):
        """Rebuild the candidate archive after `change` edited its members as (TarInfo, bytes) pairs."""
        path = self.archive_path()
        with tarfile.open(path, "r:") as archive:
            members = [(member, archive.extractfile(member).read()) for member in archive.getmembers()]
        members = change(members)
        with tarfile.open(path, "w") as archive:
            for info, data in members:
                archive.addfile(info, io.BytesIO(data) if info.isreg() else None)

    def test_publish_restores_the_candidate_from_the_archives(self):
        names = [name.replace("{version}", "1.3.0") for name in self.policy["targets"]["macos"]]
        for name in names:
            (self.root / self.policy["root"] / name).chmod(0o755)
        self.archive_path().unlink()
        evidence.package_candidate(self.policy, "macos", "1.3.0")
        fresh = self.root.parent / (self.root.name + "-publish")
        fresh.mkdir()
        shutil.copytree(self.root / "release-evidence", fresh / "release-evidence")
        shutil.copytree(self.root / "release-candidate", fresh / "release-candidate")
        os.chdir(fresh)
        evidence.verify(self.policy, self.env, self.head)
        for name in names:
            restored = fresh / self.policy["root"] / name
            self.assertEqual(restored.read_text(), "tested source")
            self.assertEqual(restored.stat().st_mode & 0o777, 0o755)

    def test_modified_extra_missing_or_unsafe_archives_are_rejected(self):
        original = self.archive_path().read_bytes()

        def tampered(members):
            info, _ = members[0]
            return [(info, b"changed after testing")] + members[1:]

        def with_extra(members):
            extra = tarfile.TarInfo("unexpected.txt")
            extra.size = 1
            return members + [(extra, b"x")]

        def with_link(members):
            link = tarfile.TarInfo(members[0][0].name)
            link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
            return [(link, b"")] + members[1:]

        def traversal(members):
            info = tarfile.TarInfo("../outside")
            info.size = 1
            return [(info, b"x")] + members[1:]

        for change in (tampered, with_extra, with_link, traversal, lambda members: members[1:]):
            with self.subTest(change=getattr(change, "__name__", "dropped member")):
                self.rewrite_archive(change)
                with self.assertRaises(ValueError):
                    evidence.verify(self.policy, self.env, self.head)
                self.archive_path().write_bytes(original)
        self.archive_path().unlink()
        with self.assertRaises(ValueError):
            evidence.verify(self.policy, self.env, self.head)
        self.archive_path().write_bytes(original)
        evidence.verify(self.policy, self.env, self.head)


if __name__ == "__main__":
    unittest.main()
