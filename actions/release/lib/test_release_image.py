import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("artifacts", Path(__file__).with_name("release_image.py"))
artifacts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifacts)

SHA = "a" * 40


def blob_of(content):
    return {"digest": "sha256:" + hashlib.sha256(content).hexdigest(), "size": len(content)}


class Builder:
    """Writes a synthetic OCI layout archive shaped like BuildKit's multi-platform output with attestations."""

    def __init__(
        self,
        platforms=("amd64", "arm64"),
        version="v4.4.0",
        sha=SHA,
        predicates=("https://slsa.dev/provenance/v1", "https://spdx.dev/Document"),
    ):
        self.blobs, self.manifests = {}, []
        for architecture in platforms:
            config = self.add(
                json.dumps(
                    {
                        "os": "linux",
                        "architecture": architecture,
                        "config": {"Labels": {"org.opencontainers.image.version": version, "org.opencontainers.image.revision": sha}},
                    }
                ).encode()
            )
            layer = self.add(b"layer")
            manifest = self.manifest(
                config, [layer], "application/vnd.oci.image.config.v1+json", "application/vnd.oci.image.layer.v1.tar+gzip"
            )
            self.manifests.append({**manifest, "platform": {"os": "linux", "architecture": architecture}})
            predicate_layers = [
                {
                    **self.add(predicate.encode()),
                    "mediaType": "application/vnd.in-toto+json",
                    "annotations": {"in-toto.io/predicate-type": predicate},
                }
                for predicate in predicates
            ]
            attestation = self.manifest(self.add(b"{}"), predicate_layers, "application/vnd.oci.image.config.v1+json", None)
            self.manifests.append(
                {
                    **attestation,
                    "platform": {"os": "unknown", "architecture": "unknown"},
                    "annotations": {"vnd.docker.reference.type": "attestation-manifest", "vnd.docker.reference.digest": manifest["digest"]},
                }
            )

    def add(self, content):
        self.blobs[blob_of(content)["digest"]] = content
        return blob_of(content)

    def manifest(self, config, layers, config_type, layer_type):
        body = {
            "schemaVersion": 2,
            "mediaType": artifacts.MANIFEST,
            "config": {**config, "mediaType": config_type},
            "layers": [{**layer, "mediaType": layer.get("mediaType", layer_type)} for layer in layers],
        }
        return {**self.add(json.dumps(body).encode()), "mediaType": artifacts.MANIFEST}

    def write(self, path):
        index = {"schemaVersion": 2, "mediaType": artifacts.INDEX, "manifests": self.manifests}
        root = {**self.add(json.dumps(index).encode()), "mediaType": artifacts.INDEX}
        files = {
            "oci-layout": json.dumps({"imageLayoutVersion": "1.0.0"}).encode(),
            "index.json": json.dumps({"schemaVersion": 2, "manifests": [root]}).encode(),
        }
        files |= {artifacts.blob_name(digest): content for digest, content in self.blobs.items()}
        with tarfile.open(path, "w") as archive:
            for name, content in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        return root["digest"]


class FakeRegistry:
    def __init__(self):
        self.blobs, self.manifests = {}, {}

    def upload_blob(self, digest, stream, size):
        self.blobs[digest] = stream.read()

    def put_manifest(self, reference, media_type, content):
        self.manifests[reference] = content

    def manifest_digest(self, reference):
        return "sha256:" + hashlib.sha256(self.manifests[reference]).hexdigest()


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.env = {
            "EXPECTED_RELEASE_VERSION": "4.4.0",
            "GITHUB_SHA": SHA,
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_REPOSITORY": "christianpflugradt/HIITBuddy",
            "GITHUB_REPOSITORY_OWNER": "christianpflugradt",
            "RELEASE_IMAGES": "hiitbuddy",
            "GITHUB_REF": "refs/heads/main",
        }
        self.environment = patch.dict(os.environ, self.env)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.index_digest = Builder().write(self.directory / "hiitbuddy.tar")
        self.metadata = {
            "schema_version": 2,
            "binding": artifacts.binding(),
            "images": {"hiitbuddy": {"sha256": artifacts.digest(self.directory / "hiitbuddy.tar"), "index_digest": self.index_digest}},
        }
        self.save()

    def save(self):
        (self.directory / "metadata.json").write_text(json.dumps(self.metadata))

    def check(self, builder, message=None):
        path = self.directory / "other.tar"
        builder.write(path)
        if message:
            with self.assertRaisesRegex(ValueError, message):
                artifacts.archive_image(path, artifacts.binding())
        else:
            artifacts.archive_image(path, artifacts.binding())

    def test_images_are_listed_one_per_line_and_validated(self):
        with patch.dict(os.environ, {"RELEASE_IMAGES": "pumpbuddy-backend\n\n pumpbuddy-renderer \n"}):
            self.assertEqual(artifacts.components(), ["pumpbuddy-backend", "pumpbuddy-renderer"])
        for invalid in ("", "Upper", "a\na", "../x"):
            with patch.dict(os.environ, {"RELEASE_IMAGES": invalid}), self.assertRaises(ValueError):
                artifacts.components()

    def test_registry_owner_is_lowercase(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY_OWNER": "ChristianPflugradt"}):
            self.assertEqual(artifacts.owner(), "christianpflugradt")

    def test_complete_bound_candidate_is_accepted_without_tools(self):
        with patch.object(artifacts.subprocess, "check_output", side_effect=AssertionError("verification must not run tools")):
            self.assertEqual(artifacts.verify(self.directory), self.metadata)

    def test_source_version_and_run_attempt_are_bound(self):
        for field in ["source_sha", "version", "run_id", "attempt"]:
            with self.subTest(field=field):
                original = copy.deepcopy(self.metadata)
                self.metadata["binding"][field] = "changed"
                self.save()
                with self.assertRaises(ValueError):
                    artifacts.verify(self.directory)
                self.metadata = original

    def test_binding_accepts_a_zero_major_version_and_rejects_malformed_ones(self):
        for version, valid in (("0.1.0", True), ("1.0.0", True), ("01.0.0", False), ("1.2", False)):
            with patch.dict(os.environ, self.env | {"EXPECTED_RELEASE_VERSION": version}):
                if valid:
                    self.assertEqual(artifacts.binding()["version"], version)
                else:
                    with self.assertRaises(ValueError):
                        artifacts.binding()

    def test_missing_or_extra_artifacts_are_rejected(self):
        (self.directory / "extra").write_text("unexpected")
        with self.assertRaises(ValueError):
            artifacts.verify(self.directory)
        (self.directory / "extra").unlink()
        (self.directory / "hiitbuddy.tar").unlink()
        with self.assertRaises(ValueError):
            artifacts.verify(self.directory)

    def test_corruption_and_forged_image_receipts_are_rejected(self):
        self.metadata["images"]["hiitbuddy"]["index_digest"] = "sha256:" + "b" * 64
        self.save()
        with self.assertRaises(ValueError):
            artifacts.verify(self.directory)
        with (self.directory / "hiitbuddy.tar").open("ab") as stream:
            stream.write(b"corruption")
        with self.assertRaises(ValueError):
            artifacts.verify(self.directory)

    def test_image_version_and_source_are_checked_independently(self):
        self.check(Builder(version="v9.9.9"), "version")
        self.check(Builder(sha="b" * 40), "source")

    def test_both_platforms_are_required_and_nothing_else(self):
        self.check(Builder(platforms=("amd64",)), "platforms")
        self.check(Builder(platforms=("amd64", "arm64", "arm")), "platforms")

    def test_provenance_and_sbom_attestations_are_required(self):
        self.check(Builder(predicates=("https://slsa.dev/provenance/v1",)), "lacks provenance or SBOM")
        self.check(Builder(predicates=("https://spdx.dev/Document",)), "lacks provenance or SBOM")

    def test_a_missing_attestation_manifest_is_rejected(self):
        builder = Builder()
        builder.manifests = [descriptor for descriptor in builder.manifests if descriptor["platform"]["os"] != "unknown"][:2]
        self.check(builder, "attestation")

    def test_tampered_blobs_symlinks_and_traversal_are_rejected(self):
        path = self.directory / "hiitbuddy.tar"
        tampered = self.directory / "tampered.tar"
        with tarfile.open(path) as old, tarfile.open(tampered, "w") as new:
            for member in old:
                content = old.extractfile(member).read() if member.isfile() else b""
                if member.name.endswith(hashlib.sha256(b"layer").hexdigest()):
                    content = b"forged"
                    member.size = len(content)
                new.addfile(member, io.BytesIO(content))
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            artifacts.archive_image(tampered, artifacts.binding())
        with tarfile.open(path, "a") as archive:
            archive.addfile(tarfile.TarInfo("../escape"))
        with self.assertRaisesRegex(ValueError, "archive entry"):
            artifacts.archive_image(path, artifacts.binding())

    def test_push_copies_every_blob_and_manifest_and_checks_the_published_digest(self):
        registry = FakeRegistry()
        artifacts.push_archive(self.directory / "hiitbuddy.tar", registry, ["v4.4.0", "latest"])
        self.assertEqual(registry.manifest_digest("v4.4.0"), self.index_digest)
        self.assertEqual(registry.manifest_digest("latest"), self.index_digest)
        with tarfile.open(self.directory / "hiitbuddy.tar") as archive:
            blobs = {name for name in archive.getnames() if name.startswith("blobs/sha256/")}
        copied = {artifacts.blob_name(digest) for digest in registry.blobs}
        manifests = {artifacts.blob_name(reference) for reference in registry.manifests if reference.startswith("sha256:")}
        self.assertEqual(copied | manifests | {artifacts.blob_name(self.index_digest)}, blobs)

    def test_push_fails_when_the_registry_returns_another_digest(self):
        registry = FakeRegistry()
        registry.manifest_digest = lambda reference: "sha256:" + "c" * 64
        with self.assertRaisesRegex(ValueError, "differs from the verified candidate"):
            artifacts.push_archive(self.directory / "hiitbuddy.tar", registry, ["latest"])

    def test_a_rejected_registry_request_names_the_request_without_its_credentials(self):
        error = artifacts.urllib.error.HTTPError("https://ghcr.io/v2/o/n/blobs/uploads/?secret=1", 403, "Forbidden", {}, None)
        with patch.object(artifacts.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(artifacts.RegistryError, r"HTTP 403 to POST /v2/o/n/blobs/uploads/$"):
                artifacts.Registry("https://ghcr.io", "o/n", None).open("POST", "https://ghcr.io/v2/o/n/blobs/uploads/?secret=1")

    def test_a_missing_blob_is_not_an_error(self):
        error = artifacts.urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with patch.object(artifacts.urllib.request, "urlopen", side_effect=error):
            self.assertFalse(artifacts.Registry("https://ghcr.io", "o/n", None).has_blob("sha256:" + "a" * 64))

    @unittest.skipUnless(shutil.which("docker"), "docker is not available")
    def test_real_buildkit_output_is_accepted(self):
        context = self.directory / "context"
        context.mkdir()
        (context / "marker").write_text("offline fixture\n")
        (context / "Dockerfile").write_text(
            f"FROM scratch\nLABEL org.opencontainers.image.version=v4.4.0\nLABEL org.opencontainers.image.revision={SHA}\nCOPY marker /offline-fixture\n"
        )
        output = self.directory / "real.tar"
        subprocess.run(
            [
                "docker",
                "buildx",
                "build",
                "--platform",
                "linux/amd64,linux/arm64",
                "--network",
                "none",
                "--pull=false",
                "--provenance=mode=max",
                "--sbom=true",
                "--output",
                f"type=oci,dest={output}",
                str(context),
            ],
            capture_output=True,
            check=True,
        )
        self.assertRegex(artifacts.archive_image(output, artifacts.binding()), r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
