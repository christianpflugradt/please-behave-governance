"""Verify and publish the exact multi-architecture image candidates; publication never builds images.

The images are listed in RELEASE_IMAGES, one name per line. Each name is the repository name below ghcr.io/<owner>. The
repository builds each candidate as an OCI archive `dist/<name>.tar` (an image index) for linux/amd64 and linux/arm64, with the
OCI labels `version` (v<version>) and `revision` and the provenance and SBOM attestations of BuildKit. The candidate is identified by
the digest of its index, and publication copies exactly the archived blobs and manifests, so the published digest is the verified one."""

import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request


DIST = Path("dist")
REGISTRY = "https://ghcr.io"
PLATFORMS = {"linux/amd64", "linux/arm64"}
INDEX = "application/vnd.oci.image.index.v1+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
ATTESTATION_TYPES = ("https://slsa.dev/provenance/", "https://spdx.dev/Document")


def components():
    names = [line.strip() for line in os.environ.get("RELEASE_IMAGES", "").splitlines() if line.strip()]
    if not names or len(set(names)) != len(names) or not all(re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name) for name in names):
        raise ValueError("RELEASE_IMAGES must list unique lowercase image names, one per line")
    return names


def owner():
    return os.environ.get("GITHUB_REPOSITORY_OWNER", os.environ.get("GITHUB_REPOSITORY", "").split("/")[0]).lower()


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def binding():
    version = os.environ["EXPECTED_RELEASE_VERSION"]
    source = os.environ["GITHUB_SHA"]
    run_id = os.environ["GITHUB_RUN_ID"]
    attempt = os.environ["GITHUB_RUN_ATTEMPT"]
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
        raise ValueError("Expected a stable-series semantic release version")
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("Invalid source SHA")
    if not run_id.isdecimal() or not attempt.isdecimal():
        raise ValueError("Invalid workflow provenance")
    if not re.fullmatch(r"(?:please-behave|christianpflugradt)/[A-Za-z0-9_.-]+", os.environ.get("GITHUB_REPOSITORY", "")):
        raise ValueError("Unexpected release repository")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Release requires main")
    return {"version": version, "source_sha": source, "run_id": run_id, "attempt": attempt}


def blob_name(digest_value):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest_value):
        raise ValueError("Invalid image digest")
    return "blobs/sha256/" + digest_value.removeprefix("sha256:")


class Layout:
    """A verified read-only view of an OCI image layout archive."""

    def __init__(self, archive):
        self.archive = archive
        self.members = {}
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or member.name in self.members or not (member.isfile() or member.isdir()):
                raise ValueError("Unexpected archive entry")
            self.members[member.name] = member
        for name, member in self.members.items():
            if name.startswith("blobs/sha256/") and member.isfile():
                with archive.extractfile(member) as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != name.removeprefix("blobs/sha256/"):
                        raise ValueError("Image blob digest mismatch")

    def blob(self, descriptor):
        member = self.members.get(blob_name(descriptor["digest"]))
        if member is None or not member.isfile() or member.size != descriptor["size"]:
            raise ValueError("Image blob missing or of unexpected size")
        return member

    def json(self, descriptor, media_type):
        if descriptor["mediaType"] != media_type:
            raise ValueError("Unexpected image media type")
        with self.archive.extractfile(self.blob(descriptor)) as stream:
            return json.load(stream)


def archive_image(path, expected):
    """Returns the digest of the image index after verifying the complete multi-architecture candidate."""
    with tarfile.open(path, "r:") as archive:
        layout = Layout(archive)
        with archive.extractfile(layout.members["oci-layout"]) as stream:
            if json.load(stream).get("imageLayoutVersion") != "1.0.0":
                raise ValueError("Unsupported OCI layout")
        with archive.extractfile(layout.members["index.json"]) as stream:
            roots = json.load(stream)["manifests"]
        if len(roots) != 1:
            raise ValueError("Unexpected image set")
        index = layout.json(roots[0], INDEX)
        images, attestations = {}, {}
        for descriptor in index["manifests"]:
            annotations = descriptor.get("annotations", {})
            if annotations.get("vnd.docker.reference.type") == "attestation-manifest":
                reference = annotations["vnd.docker.reference.digest"]
                if reference in attestations:
                    raise ValueError("Duplicate image attestation")
                attestations[reference] = descriptor
            else:
                platform = descriptor["platform"]
                key = f"{platform['os']}/{platform['architecture']}"
                if key in images:
                    raise ValueError("Duplicate image platform")
                images[key] = descriptor
        if set(images) != PLATFORMS:
            raise ValueError("Candidate platforms must be exactly linux/amd64 and linux/arm64")
        if set(attestations) != {descriptor["digest"] for descriptor in images.values()}:
            raise ValueError("Every platform image needs exactly one attestation manifest")
        for key, descriptor in images.items():
            manifest = layout.json(descriptor, MANIFEST)
            config = layout.json(manifest["config"], "application/vnd.oci.image.config.v1+json")
            if f"{config['os']}/{config['architecture']}" != key:
                raise ValueError("Image platform differs from its config")
            labels = config["config"]["Labels"]
            if labels["org.opencontainers.image.version"] != "v" + expected["version"]:
                raise ValueError("Image version differs from the planned release")
            if labels["org.opencontainers.image.revision"] != expected["source_sha"]:
                raise ValueError("Image source differs from the planned release")
            if not manifest["layers"]:
                raise ValueError("Image layers missing")
            for layer in manifest["layers"]:
                layout.blob(layer)
            attestation = layout.json(attestations[descriptor["digest"]], MANIFEST)
            layout.blob(attestation["config"])
            predicates = {layer.get("annotations", {}).get("in-toto.io/predicate-type", "") for layer in attestation["layers"]}
            for layer in attestation["layers"]:
                layout.blob(layer)
            if not all(any(found.startswith(required) for found in predicates) for required in ATTESTATION_TYPES):
                raise ValueError("Image attestation lacks provenance or SBOM")
        return roots[0]["digest"]


def verify(directory=DIST):
    expected = binding()
    required = {"metadata.json", *(f"{name}.tar" for name in components())}
    if {path.name for path in directory.iterdir()} != required:
        raise ValueError("Incomplete or unexpected candidate artifact set")
    if any(path.is_symlink() or not path.is_file() for path in directory.iterdir()):
        raise ValueError("Candidate artifacts must be regular files")
    metadata = json.loads((directory / "metadata.json").read_text())
    if metadata["schema_version"] != 2 or metadata["binding"] != expected:
        raise ValueError("Candidate source, version or workflow run/attempt changed")
    if set(metadata["images"]) != set(components()):
        raise ValueError("Incomplete image receipts")
    for name in components():
        receipt = metadata["images"][name]
        path = directory / f"{name}.tar"
        if receipt["sha256"] != digest(path):
            raise ValueError("Candidate archive checksum mismatch")
        if receipt["index_digest"] != archive_image(path, expected):
            raise ValueError("Candidate image identity mismatch")
    return metadata


def export(directory=DIST):
    expected = binding()
    if run("git", "rev-parse", "HEAD") != expected["source_sha"]:
        raise ValueError("Candidate checkout differs from dispatched source")
    if {path.name for path in directory.iterdir()} != {f"{name}.tar" for name in components()}:
        raise ValueError("Candidate output must hold exactly the image archives")
    images = {
        name: {"sha256": digest(directory / f"{name}.tar"), "index_digest": archive_image(directory / f"{name}.tar", expected)}
        for name in components()
    }
    (directory / "metadata.json").write_text(json.dumps({"schema_version": 2, "binding": expected, "images": images}, indent=2) + "\n")
    verify(directory)


class Registry:
    def __init__(self, base, repository, credentials):
        self.base, self.repository, self.token = base, repository, None
        if credentials:
            basic = base64.b64encode(":".join(credentials).encode()).decode()
            scope = f"repository:{repository}:push,pull"
            with self.open("GET", f"{base}/token?service=ghcr.io&scope={scope}", {"Authorization": f"Basic {basic}"}) as response:
                self.token = json.load(response)["token"]

    def open(self, method, url, headers=None, body=None, length=None):
        headers = dict(headers or {})
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            headers["Content-Length"] = str(length)
        return urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers, method=method))

    def has_blob(self, digest_value):
        try:
            self.open("HEAD", f"{self.base}/v2/{self.repository}/blobs/{digest_value}").close()
            return True
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return False
            raise

    def upload_blob(self, digest_value, stream, size):
        if self.has_blob(digest_value):
            return
        with self.open("POST", f"{self.base}/v2/{self.repository}/blobs/uploads/", body=b"", length=0) as response:
            location = response.headers["Location"]
        location = urllib.parse.urljoin(self.base + "/", location)
        separator = "&" if "?" in location else "?"
        headers = {"Content-Type": "application/octet-stream"}
        self.open("PUT", f"{location}{separator}digest={digest_value}", headers, stream, size).close()

    def put_manifest(self, reference, media_type, content):
        self.open(
            "PUT", f"{self.base}/v2/{self.repository}/manifests/{reference}", {"Content-Type": media_type}, content, len(content)
        ).close()

    def manifest_digest(self, reference):
        headers = {"Accept": INDEX}
        with self.open("HEAD", f"{self.base}/v2/{self.repository}/manifests/{reference}", headers) as response:
            return response.headers["Docker-Content-Digest"]


def credentials():
    return os.environ["GITHUB_ACTOR"], os.environ["GITHUB_TOKEN"]


def push_archive(path, registry, tags):
    """Copies the blobs and manifests of the verified archive unchanged, so the pushed index keeps its digest."""
    with tarfile.open(path, "r:") as archive:
        layout = Layout(archive)
        with archive.extractfile(layout.members["index.json"]) as stream:
            root = json.load(stream)["manifests"][0]
        index = layout.json(root, INDEX)
        manifests = {descriptor["digest"]: descriptor for descriptor in index["manifests"]}
        for name, member in layout.members.items():
            blob_digest = "sha256:" + name.removeprefix("blobs/sha256/")
            if name.startswith("blobs/sha256/") and member.isfile() and blob_digest not in manifests and blob_digest != root["digest"]:
                with archive.extractfile(member) as stream:
                    registry.upload_blob(blob_digest, stream, member.size)
        for descriptor in manifests.values():
            with archive.extractfile(layout.blob(descriptor)) as stream:
                registry.put_manifest(descriptor["digest"], descriptor["mediaType"], stream.read())
        with archive.extractfile(layout.blob(root)) as stream:
            content = stream.read()
        for tag in tags:
            registry.put_manifest(tag, INDEX, content)
        for tag in tags:
            if registry.manifest_digest(tag) != root["digest"]:
                raise ValueError("Published image differs from the verified candidate")


def push(directory=DIST):
    metadata = verify(directory)
    for name in components():
        repository = f"{owner()}/{name}"
        registry = Registry(REGISTRY, repository, credentials())
        tags = [f"v{metadata['binding']['version']}", "latest"]
        push_archive(directory / f"{name}.tar", registry, tags)


def main():
    commands = {"export": export, "verify": verify, "push": push}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        raise SystemExit("Usage: release_image.py export|verify|push")
    try:
        commands[sys.argv[1]]()
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
        print(f"ERROR: image candidate could not be established: {error}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
