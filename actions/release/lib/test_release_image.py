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
    """Writes a synthetic OCI layout archive shaped like BuildKit's output of one platform with attestations."""

    def __init__(
        self, platforms=("amd64",), version="v4.4.0", sha=SHA, predicates=("https://slsa.dev/provenance/v1", "https://spdx.dev/Document")
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
            layer = self.add(f"layer-{architecture}".encode())
            manifest = self.manifest(
                config, [layer], "application/vnd.oci.image.config.v1+json", "application/vnd.oci.image.layer.v1.tar+gzip"
            )
            self.manifests.append({**manifest, "platform": {"os": "linux", "architecture": architecture}})
            predicate_layers = [
                {
                    **self.add(f"{predicate}-{architecture}".encode()),
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
            "GITHUB_REPOSITORY": "christianpflugradt/HIITBuddy",
            "GITHUB_REPOSITORY_OWNER": "ChristianPflugradt",
            "RELEASE_IMAGES": "hiitbuddy",
            "GITHUB_REF": "refs/heads/main",
        }
        self.environment = patch.dict(os.environ, self.env)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.write_platforms()

    def write_platforms(self, **overrides):
        for target, architecture in (("linux-amd64", "amd64"), ("linux-arm64", "arm64")):
            Builder(platforms=(architecture,), **overrides).write(self.directory / f"hiitbuddy-{target}.tar")

    def expected(self):
        return artifacts.binding()

    def test_images_are_listed_one_per_line_and_validated(self):
        with patch.dict(os.environ, {"RELEASE_IMAGES": "pumpbuddy-backend\n\n pumpbuddy-renderer \n"}):
            self.assertEqual(artifacts.components(), ["pumpbuddy-backend", "pumpbuddy-renderer"])
        for invalid in ("", "Upper", "a\na", "../x"):
            with patch.dict(os.environ, {"RELEASE_IMAGES": invalid}), self.assertRaises(ValueError):
                artifacts.components()

    def test_registry_owner_is_lowercase(self):
        self.assertEqual(artifacts.owner(), "christianpflugradt")

    def test_binding_accepts_a_zero_major_version_and_rejects_malformed_ones(self):
        for version, valid in (("0.1.0", True), ("1.0.0", True), ("01.0.0", False), ("1.2", False)):
            with patch.dict(os.environ, {"EXPECTED_RELEASE_VERSION": version}):
                if valid:
                    self.assertEqual(artifacts.binding()["version"], version)
                else:
                    with self.assertRaises(ValueError):
                        artifacts.binding()

    def test_release_requires_main(self):
        with patch.dict(os.environ, {"GITHUB_REF": "refs/heads/other"}), self.assertRaisesRegex(ValueError, "main"):
            artifacts.binding()

    def test_each_platform_candidate_is_checked_for_its_own_target(self):
        for target in artifacts.TARGETS:
            archives = [path for path in self.directory.iterdir() if target not in path.name]
            staging = self.directory / target
            staging.mkdir()
            for path in self.directory.glob(f"*-{target}.tar"):
                shutil.copy(path, staging)
            with patch.dict(os.environ, {"RELEASE_TARGET": target}):
                artifacts.check(staging)
            self.assertTrue(archives)

    def test_a_candidate_of_another_platform_is_rejected_for_the_target(self):
        shutil.copy(self.directory / "hiitbuddy-linux-arm64.tar", self.directory / "wrong-linux-amd64.tar")
        (self.directory / "hiitbuddy-linux-amd64.tar").unlink()
        shutil.move(self.directory / "wrong-linux-amd64.tar", self.directory / "hiitbuddy-linux-amd64.tar")
        (self.directory / "hiitbuddy-linux-arm64.tar").unlink()
        with patch.dict(os.environ, {"RELEASE_TARGET": "linux-amd64"}), self.assertRaisesRegex(ValueError, "platforms differ"):
            artifacts.check(self.directory)

    def test_check_requires_exactly_the_archives_of_the_target_and_a_known_target(self):
        with patch.dict(os.environ, {"RELEASE_TARGET": "linux-amd64"}), self.assertRaisesRegex(ValueError, "exactly"):
            artifacts.check(self.directory)
        with patch.dict(os.environ, {"RELEASE_TARGET": "windows"}), self.assertRaisesRegex(ValueError, "RELEASE_TARGET"):
            artifacts.check(self.directory)

    def test_the_platform_candidates_assemble_into_one_deterministic_index(self):
        first = artifacts.verify(self.directory)
        self.assertEqual(first, artifacts.verify(self.directory))
        self.assertRegex(first["hiitbuddy"], r"^sha256:[0-9a-f]{64}$")
        indexes = [artifacts.archive_index(self.directory / f"hiitbuddy-{t}.tar", p, self.expected()) for t, p in artifacts.TARGETS.items()]
        document, content, digest = artifacts.merge_index(list(reversed(indexes)))
        self.assertEqual(digest, first["hiitbuddy"])
        self.assertEqual([d["platform"]["architecture"] for d in document["manifests"]], ["amd64", "arm64", "unknown", "unknown"])

    def test_verification_does_not_run_tools_or_write_files(self):
        before = sorted(path.name for path in self.directory.iterdir())
        with patch.object(artifacts.urllib.request, "urlopen", side_effect=AssertionError("verification must stay offline")):
            artifacts.verify(self.directory)
        self.assertEqual(before, sorted(path.name for path in self.directory.iterdir()))

    def test_the_candidate_must_hold_one_archive_per_image_and_platform(self):
        (self.directory / "hiitbuddy-linux-arm64.tar").unlink()
        with self.assertRaisesRegex(ValueError, "one archive per image and platform"):
            artifacts.verify(self.directory)

    def test_an_extra_file_in_the_candidate_is_rejected(self):
        (self.directory / "extra").write_text("unexpected")
        with self.assertRaisesRegex(ValueError, "one archive per image and platform"):
            artifacts.verify(self.directory)

    def test_image_version_and_source_are_checked_independently(self):
        self.write_platforms(version="v9.9.9")
        with self.assertRaisesRegex(ValueError, "version"):
            artifacts.verify(self.directory)
        self.write_platforms(sha="b" * 40)
        with self.assertRaisesRegex(ValueError, "source"):
            artifacts.verify(self.directory)

    def test_provenance_and_sbom_attestations_are_required(self):
        for predicates in (("https://slsa.dev/provenance/v1",), ("https://spdx.dev/Document",)):
            self.write_platforms(predicates=predicates)
            with self.assertRaisesRegex(ValueError, "lacks provenance or SBOM"):
                artifacts.verify(self.directory)

    def test_a_missing_attestation_manifest_is_rejected(self):
        builder = Builder()
        builder.manifests = builder.manifests[:1]
        builder.write(self.directory / "hiitbuddy-linux-amd64.tar")
        with self.assertRaisesRegex(ValueError, "attestation"):
            artifacts.verify(self.directory)

    def test_tampered_blobs_symlinks_and_traversal_are_rejected(self):
        path = self.directory / "hiitbuddy-linux-amd64.tar"
        tampered = self.directory / "tampered.tar"
        with tarfile.open(path) as old, tarfile.open(tampered, "w") as new:
            for member in old:
                content = old.extractfile(member).read() if member.isfile() else b""
                if member.name.endswith(hashlib.sha256(b"layer-amd64").hexdigest()):
                    content = b"forged"
                    member.size = len(content)
                new.addfile(member, io.BytesIO(content))
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            artifacts.archive_index(tampered, "linux/amd64", self.expected())
        with tarfile.open(path, "a") as archive:
            archive.addfile(tarfile.TarInfo("../escape"))
        with self.assertRaisesRegex(ValueError, "archive entry"):
            artifacts.archive_index(path, "linux/amd64", self.expected())

    def test_push_copies_every_blob_and_manifest_and_checks_the_published_digest(self):
        registry = FakeRegistry()
        with __import__("contextlib").ExitStack() as stack:
            assembled = artifacts.Assembled(self.directory, "hiitbuddy", self.expected(), stack)
            artifacts.push_image(assembled, registry, ["v4.4.0", "latest"])
            expected_blobs = set(assembled.layout.blobs)
        self.assertEqual(registry.manifest_digest("v4.4.0"), assembled.digest)
        self.assertEqual(registry.manifest_digest("latest"), assembled.digest)
        copied = {artifacts.blob_name(d) for d in registry.blobs}
        manifests = {artifacts.blob_name(r) for r in registry.manifests if r.startswith("sha256:")}
        indexes = {artifacts.blob_name(d["digest"]) for index in assembled.indexes for d in index["manifests"]}
        self.assertEqual(manifests, indexes)
        roots = {artifacts.blob_name(root["digest"]) for root in assembled.layout.roots}
        self.assertEqual(copied | manifests | roots, expected_blobs)

    def test_push_fails_when_the_registry_returns_another_digest(self):
        registry = FakeRegistry()
        registry.manifest_digest = lambda reference: "sha256:" + "c" * 64
        with __import__("contextlib").ExitStack() as stack:
            assembled = artifacts.Assembled(self.directory, "hiitbuddy", self.expected(), stack)
            with self.assertRaisesRegex(ValueError, "differs from the verified candidate"):
                artifacts.push_image(assembled, registry, ["latest"])

    def test_outputs_name_the_registry_subject_and_index_digest_of_each_image(self):
        with patch("builtins.print") as printed:
            artifacts.outputs(self.directory)
        lines = printed.call_args.args[0].split("\n")
        self.assertEqual(lines[:3], ["names<<RELEASE_IMAGE_NAMES_END", "hiitbuddy", "RELEASE_IMAGE_NAMES_END"])
        self.assertEqual(lines[3], "subject_1=ghcr.io/christianpflugradt/hiitbuddy")
        self.assertEqual(lines[4], "digest_1=" + artifacts.verify(self.directory)["hiitbuddy"])

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
    def test_real_buildkit_output_of_each_platform_assembles(self):
        context = self.directory / "context"
        context.mkdir()
        (context / "marker").write_text("offline fixture\n")
        (context / "Dockerfile").write_text(
            f"FROM scratch\nLABEL org.opencontainers.image.version=v4.4.0\nLABEL org.opencontainers.image.revision={SHA}\nCOPY marker /offline-fixture\n"
        )
        real = self.directory / "real"
        real.mkdir()
        for target, platform in artifacts.TARGETS.items():
            subprocess.run(
                [
                    "docker", "buildx", "build", "--platform", platform, "--network", "none", "--pull=false",
                    "--provenance=mode=max", "--sbom=true", "--output", f"type=oci,dest={real / f'hiitbuddy-{target}.tar'}", str(context),
                ],
                capture_output=True,
                check=True,
            )  # fmt: skip
        self.assertRegex(artifacts.verify(real)["hiitbuddy"], r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
