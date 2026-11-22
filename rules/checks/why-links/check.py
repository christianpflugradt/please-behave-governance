"""Check repository declarations and documentation links in the consumer working tree."""

import json
import os
import posixpath
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

SCHEMAS = Path(__file__).resolve().parent / "schemas"
DECLARATION = Path("why/repositories.yaml")
MARKDOWN = {".md", ".markdown"}
ARTIFACT = re.compile(r"why/(?:(?:requirements|decisions|components)/[^/]+\.yaml|contracts/[^/]+/api\.yaml)")
JSON_TYPES = {"object": dict, "array": list, "string": str}


class InvalidDeclaration(ValueError):
    pass


def load_schema(name):
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))


def unique_keys(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate YAML key")
    return result


def load_yaml(path, *, declaration=False):
    result = subprocess.run(["yq", "-o=json", "-I=0", ".", "-"], input=path.read_bytes(), capture_output=True, check=False)
    if result.returncode:
        if declaration and result.returncode == 1 and b"bad file '-':" in result.stderr:
            raise InvalidDeclaration("invalid YAML")
        raise RuntimeError(f"{path}: yq could not parse YAML")
    try:
        return json.loads(result.stdout, object_pairs_hook=unique_keys)
    except ValueError:
        if declaration:
            raise InvalidDeclaration("expected one YAML document with unique keys") from None
        raise RuntimeError(f"{path}: expected one YAML document with unique keys") from None


def validate(schema, value, location, definitions):
    """Validate the object, array and pattern constraints of the pinned declaration schema."""
    if "$ref" in schema:
        validate(definitions[schema["$ref"].removeprefix("#/$defs/")], value, location, definitions)
        return
    kind = schema["type"]
    if not isinstance(value, JSON_TYPES[kind]):
        raise InvalidDeclaration(f"{location}: expected {kind}")
    if kind == "object":
        properties = schema["properties"]
        if set(schema["required"]) - value.keys():
            raise InvalidDeclaration(f"{location}: missing required properties")
        if schema.get("additionalProperties") is False and value.keys() - properties.keys():
            raise InvalidDeclaration(f"{location}: contains unknown properties")
        for key, item in value.items():
            validate(properties[key], item, f"{location}.{key}", definitions)
    elif kind == "array":
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise InvalidDeclaration(f"{location}: entries must be unique")
        for index, item in enumerate(value):
            validate(schema["items"], item, f"{location}[{index}]", definitions)
    elif not re.fullmatch(schema["pattern"], value):
        raise InvalidDeclaration(f"{location}: invalid format")


def read_declaration():
    schema = load_schema("repositories")
    declaration = load_yaml(DECLARATION, declaration=True)
    validate(schema, declaration, str(DECLARATION), schema["$defs"])
    own = declaration["repository"]
    linked = {}
    for entry in declaration["linked"]:
        alias = entry["alias"]
        if alias == own["alias"] or alias in linked:
            raise InvalidDeclaration(f"{DECLARATION}: duplicate alias {alias}")
        linked[alias] = entry["source"].removeprefix("github:").lower()
    return own["source"].removeprefix("github:").lower(), linked


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.anchors = set()
        self.links = []
        self.text = []
        self.in_pre = False
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self.in_pre = True
        if self.in_pre:
            return
        attrs = dict(attrs)
        if attrs.get("id"):
            self.anchors.add(attrs["id"])
        if tag == "a" and attrs.get("name"):
            self.anchors.add(attrs["name"])
        link = attrs.get({"a": "href", "img": "src"}.get(tag))
        if link:
            self.links.append(link)

    def handle_endtag(self, tag):
        if tag == "pre":
            self.in_pre = False

    def handle_data(self, data):
        if not self.in_pre:
            self.text.append(data)


def markdown(path):
    result = subprocess.run(
        ["pandoc", "--sandbox", "--from=gfm", "--to=html", "--syntax-highlighting=none", "--wrap=none"],
        input=path.read_bytes(),
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"{path}: pandoc could not parse Markdown")
    return Document(result.stdout.decode("utf-8"))


def snapshot():
    listing = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], capture_output=True, check=False)
    if listing.returncode:
        raise RuntimeError("git could not list the repository files")
    root = Path.cwd()
    paths = {Path(os.fsdecode(name)) for name in listing.stdout.split(b"\0") if name}
    return {path for path in paths if not path.is_symlink() and path.resolve().is_relative_to(root) and path.exists()}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def repository(url):
    if url.scheme not in {"", "http", "https"} or url.hostname not in {"github.com", "www.github.com"}:
        return None
    parts = unquote(url.path).lower().split("/")
    if len(parts) >= 3 and parts[1] and parts[2]:
        return f"{parts[1]}/{parts[2].removesuffix('.git')}"
    return None


def local_target(source, url):
    path = unquote(url.path)
    if not path:
        return source
    if path.startswith("/"):
        return Path(posixpath.normpath(path.lstrip("/")))
    return Path(posixpath.normpath(source.parent / path))


def check_links(documents, paths, own, linked):
    locations = paths | {parent for path in paths for parent in path.parents}
    used = set()
    errors = []
    html = {}
    for source, document in documents.items():
        for link in document.links:
            try:
                url = urlsplit(link)
                repo = repository(url)
            except ValueError:
                errors.append(f"{source}: malformed link {link!r}")
                continue
            if url.scheme or url.netloc:
                if repo:
                    used.add(repo)
                if own and repo and repo.partition("/")[0] == own.partition("/")[0] and repo != own and repo not in linked.values():
                    errors.append(f"{source}: undeclared GitHub repository {repo}")
                continue
            target = local_target(source, url)
            if target not in locations or (url.path.endswith("/") and not target.is_dir()):
                errors.append(f"{source}: missing local target {link!r}")
                continue
            if not url.fragment:
                continue
            if target.suffix.lower() in {".html", ".htm"} and target.is_file() and target not in html:
                html[target] = Document(target.read_text(encoding="utf-8"))
            anchored = documents.get(target) or html.get(target)
            if not anchored or unquote(url.fragment) not in anchored.anchors:
                errors.append(f"{source}: missing anchor {link!r}")
    return errors, used


def check():
    paths = snapshot()
    own, linked = None, {}
    if DECLARATION in paths:
        try:
            own, linked = read_declaration()
        except InvalidDeclaration as error:
            return [str(error)], 0
    elif DECLARATION.is_symlink():
        return [f"{DECLARATION}: expected a local YAML file"], 0

    documents = {path: markdown(path) for path in sorted(paths) if path.suffix.lower() in MARKDOWN and path.is_file()}
    errors, used_sources = check_links(documents, paths, own, linked)
    texts = {path: document.text for path, document in documents.items()}
    for path in sorted(paths):
        if ARTIFACT.fullmatch(path.as_posix()) and path.is_file():
            texts[path] = strings(load_yaml(path))

    pattern = load_schema("reference")["pattern"]
    reference = re.compile(rf"(?<![\w:/-])(?:{pattern[1:-1]})(?![\w-])")
    used_aliases = set()
    for path, parts in texts.items():
        for part in parts:
            for match in reference.finditer(part):
                alias, separator, _ = match[0].partition(":")
                if not separator:
                    continue
                if alias not in linked:
                    errors.append(f"{path}: undeclared Why alias {alias} in {match[0]}")
                used_aliases.add(alias)
    for alias, source in linked.items():
        if alias not in used_aliases and source not in used_sources:
            errors.append(f"{DECLARATION}: unused linked repository {alias} ({source})")
    return errors, len(documents)


def main():
    try:
        errors, count = check()
        if errors:
            for error in errors:
                print(f"FAIL: {error}", file=sys.stderr)
            return 1
        print(f"PASS: validated Why links in {count} Markdown files")
        return 0
    except FileNotFoundError as error:
        print(f"ERROR: missing file or tool: {error.filename}", file=sys.stderr)
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
    except Exception as error:  # noqa: BLE001 - Technical errors must return 2.
        print(f"ERROR: unexpected {type(error).__name__}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
