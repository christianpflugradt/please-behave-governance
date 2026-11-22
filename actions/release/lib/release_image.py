"""Check, assemble and publish the exact multi-architecture image candidates; publication never builds images.

The images are listed in RELEASE_IMAGES, one name per line. Each name is the repository name below ghcr.io/<owner>. Every platform
builds its own candidate on a native runner: an OCI archive `dist/<name>-<target>.tar` (an image index with one platform image and its
provenance and SBOM attestations, labeled with `version` (v<version>) and `revision`). Publication assembles the archives of all
platforms into one deterministic index, so the published digest is the digest of exactly the verified blobs and manifests."""

import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request


DIST = Path("dist")
REGISTRY = "https://ghcr.io"
TARGETS = {"linux-amd64": "linux/amd64", "linux-arm64": "linux/arm64"}
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


def binding():
    version = os.environ["EXPECTED_RELEASE_VERSION"]
    source = os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
        raise ValueError("Expected a stable-series semantic release version")
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("Invalid source SHA")
    if not re.fullmatch(r"(?:please-behave|christianpflugradt)/[A-Za-z0-9_.-]+", os.environ.get("GITHUB_REPOSITORY", "")):
        raise ValueError("Unexpected release repository")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Release requires main")
    return {"version": version, "source_sha": source}


def blob_name(digest_value):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest_value):
        raise ValueError("Invalid image digest")
    return "blobs/sha256/" + digest_value.removeprefix("sha256:")


class Layout:
    """A verified read-only view of one or more OCI image layout archives; blobs are addressed by their digest."""

    def __init__(self, archives):
        self.blobs, self.roots = {}, []
        for archive in archives:
            names = set()
            for member in archive:
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts or member.name in names or not (member.isfile() or member.isdir()):
                    raise ValueError("Unexpected archive entry")
                names.add(member.name)
                if member.name.startswith("blobs/sha256/") and member.isfile():
                    with archive.extractfile(member) as stream:
                        if hashlib.file_digest(stream, "sha256").hexdigest() != member.name.removeprefix("blobs/sha256/"):
                            raise ValueError("Image blob digest mismatch")
                    self.blobs[member.name] = (archive, member)
            with archive.extractfile("oci-layout") as stream:
                if json.load(stream).get("imageLayoutVersion") != "1.0.0":
                    raise ValueError("Unsupported OCI layout")
            with archive.extractfile("index.json") as stream:
                roots = json.load(stream)["manifests"]
            if len(roots) != 1:
                raise ValueError("Unexpected image set")
            self.roots.append(roots[0])

    def member(self, descriptor):
        entry = self.blobs.get(blob_name(descriptor["digest"]))
        if entry is None or entry[1].size != descriptor["size"]:
            raise ValueError("Image blob missing or of unexpected size")
        return entry

    def read(self, descriptor):
        archive, member = self.member(descriptor)
        with archive.extractfile(member) as stream:
            return stream.read()

    def json(self, descriptor, media_type):
        if descriptor["mediaType"] != media_type:
            raise ValueError("Unexpected image media type")
        return json.loads(self.read(descriptor))


def check_index(layout, index, platforms, expected):
    """Verifies that the index holds exactly the platform images with the planned labels and their attestations."""
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
    if set(images) != platforms:
        raise ValueError("Candidate platforms differ from the expected " + ", ".join(sorted(platforms)))
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
            layout.member(layer)
        attestation = layout.json(attestations[descriptor["digest"]], MANIFEST)
        layout.member(attestation["config"])
        predicates = {layer.get("annotations", {}).get("in-toto.io/predicate-type", "") for layer in attestation["layers"]}
        for layer in attestation["layers"]:
            layout.member(layer)
        if not all(any(found.startswith(required) for found in predicates) for required in ATTESTATION_TYPES):
            raise ValueError("Image attestation lacks provenance or SBOM")


def archive_index(path, platform, expected):
    """Verifies the candidate archive of one platform and returns its image index."""
    with tarfile.open(path, "r:") as archive:
        layout = Layout([archive])
        index = layout.json(layout.roots[0], INDEX)
        check_index(layout, index, {platform}, expected)
        return index


def merge_index(indexes):
    """The deterministic index of all platforms: images first, then their attestations, each in platform order."""
    manifests = [d for index in indexes for d in index["manifests"]]
    platform_of = {
        d["digest"]: (d["platform"]["os"], d["platform"]["architecture"])
        for d in manifests
        if "platform" in d and d["platform"]["os"] != "unknown"
    }

    def order(descriptor):
        annotations = descriptor.get("annotations", {})
        if annotations.get("vnd.docker.reference.type") == "attestation-manifest":
            return (True, platform_of[annotations["vnd.docker.reference.digest"]])
        return (False, platform_of[descriptor["digest"]])

    document = {"schemaVersion": 2, "mediaType": INDEX, "manifests": sorted(manifests, key=order)}
    content = json.dumps(document, separators=(",", ":"), sort_keys=True).encode()
    return document, content, "sha256:" + hashlib.sha256(content).hexdigest()


class Assembled:
    """The platform candidates of one image, merged into the index that is published."""

    def __init__(self, directory, name, expected, stack):
        paths = [directory / f"{name}-{target}.tar" for target in TARGETS]
        self.indexes = [archive_index(path, platform, expected) for path, platform in zip(paths, TARGETS.values())]
        self.layout = Layout([stack.enter_context(tarfile.open(path, "r:")) for path in paths])
        self.document, self.content, self.digest = merge_index(self.indexes)
        check_index(self.layout, self.document, set(TARGETS.values()), expected)


def candidate_names(directory):
    """The image names of the candidate: every platform archive pairs a name with each target."""
    files = sorted(path.name for path in directory.iterdir())
    names = sorted({file.removesuffix(f"-{target}.tar") for file in files for target in TARGETS if file.endswith(f"-{target}.tar")})
    if not names or files != sorted(f"{name}-{target}.tar" for name in names for target in TARGETS):
        raise ValueError("The candidate must hold exactly one archive per image and platform")
    if not all(re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name) for name in names):
        raise ValueError("Invalid image name in the candidate")
    return names


def check(directory=DIST):
    """Candidate job of one platform: the archives of the target hold exactly the planned single-platform images."""
    expected = binding()
    target = os.environ.get("RELEASE_TARGET", "")
    if target not in TARGETS:
        raise ValueError("RELEASE_TARGET must be one of " + ", ".join(TARGETS))
    if {path.name for path in directory.iterdir()} != {f"{name}-{target}.tar" for name in components()}:
        raise ValueError("Candidate output must hold exactly the image archives of the target")
    for name in components():
        archive_index(directory / f"{name}-{target}.tar", TARGETS[target], expected)


def verify(directory=DIST):
    """Publication: the platform archives assemble into valid indexes. Nothing is written or published."""
    expected = binding()
    with contextlib.ExitStack() as stack:
        return {name: Assembled(directory, name, expected, stack).digest for name in candidate_names(directory)}


def outputs(directory=DIST):
    """Names, registry subjects and index digests for the steps after publication."""
    digests = verify(directory)
    if len(digests) > 4:
        raise ValueError("At most four images can be attested per release")
    lines = ["names<<RELEASE_IMAGE_NAMES_END", *digests, "RELEASE_IMAGE_NAMES_END"]
    for number, (name, index_digest) in enumerate(digests.items(), 1):
        lines += [f"subject_{number}=ghcr.io/{owner()}/{name}", f"digest_{number}={index_digest}"]
    print("\n".join(lines))


class RegistryError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


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
        try:
            return urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers, method=method))
        except urllib.error.HTTPError as error:
            raise RegistryError(
                error.code, f"The registry answered HTTP {error.code} to {method} {urllib.parse.urlsplit(url).path}"
            ) from None

    def has_blob(self, digest_value):
        try:
            self.open("HEAD", f"{self.base}/v2/{self.repository}/blobs/{digest_value}").close()
            return True
        except RegistryError as error:
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


def push_image(assembled, registry, tags):
    """Copies the blobs and manifests of the verified candidate unchanged, so the pushed index has the assembled digest."""
    manifests = {descriptor["digest"]: descriptor for descriptor in assembled.document["manifests"]}
    for name, (archive, member) in assembled.layout.blobs.items():
        blob_digest = "sha256:" + name.removeprefix("blobs/sha256/")
        if blob_digest not in manifests:
            with archive.extractfile(member) as stream:
                registry.upload_blob(blob_digest, stream, member.size)
    for descriptor in manifests.values():
        registry.put_manifest(descriptor["digest"], descriptor["mediaType"], assembled.layout.read(descriptor))
    for tag in tags:
        registry.put_manifest(tag, INDEX, assembled.content)
    for tag in tags:
        if registry.manifest_digest(tag) != assembled.digest:
            raise ValueError("Published image differs from the verified candidate")


def push(directory=DIST):
    expected = binding()
    with contextlib.ExitStack() as stack:
        candidates = {name: Assembled(directory, name, expected, stack) for name in candidate_names(directory)}
        for name, assembled in candidates.items():
            registry = Registry(REGISTRY, f"{owner()}/{name}", credentials())
            push_image(assembled, registry, [f"v{expected['version']}", "latest"])


def main():
    commands = {"check": check, "verify": verify, "push": push, "outputs": outputs}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        raise SystemExit("Usage: release_image.py check|verify|push|outputs")
    try:
        commands[sys.argv[1]]()
    except (OSError, KeyError, ValueError) as error:
        print(f"ERROR: image candidate could not be established: {error}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
