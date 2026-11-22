"""Validate artifacts in the consumer's Why against the bundled standard."""

import json
import re
import stat
import subprocess
import sys
from pathlib import Path

SCHEMAS = Path(__file__).resolve().parent / "schemas"
ARTIFACTS = {
    "requirements": "requirement",
    "decisions": "decision",
    "components": "component",
    "contracts": "contract-index",
}
JSON_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}
SCHEMA_KEYWORDS = frozenset(
    [
        "$schema",
        "$defs",
        "$ref",
        "title",
        "description",
        "examples",
        "type",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "minItems",
        "uniqueItems",
        "minLength",
        "pattern",
        "const",
        "minimum",
    ]
)


def mode(path):
    try:
        return path.lstat().st_mode
    except FileNotFoundError:
        return 0


def unique_keys(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate mapping key")
    return result


def load_yaml(path):
    result = subprocess.run(["yq", "-o=json", "-I=0", ".", "-"], input=path.read_bytes(), capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"{path}: yq could not parse YAML")
    try:
        return json.loads(result.stdout, object_pairs_hook=unique_keys)
    except ValueError:
        raise ValueError(f"{path}: expected a single YAML document with unique mapping keys") from None


class Standard:
    """Evaluate the keywords used by the pinned artifact schemas, with local references only."""

    def __init__(self):
        self.schemas = {
            name: json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8")) for name in (*ARTIFACTS.values(), "reference")
        }

    def validate(self, schema, value, location, errors, references):
        unsupported = schema.keys() - SCHEMA_KEYWORDS
        if unsupported:
            raise ValueError(f"unsupported bundled schema keywords: {', '.join(sorted(unsupported))}")
        if "$ref" in schema:
            filename, fragment = schema["$ref"].split("#/$defs/", 1)
            if filename != "reference.schema.json":
                raise ValueError("unsupported bundled schema reference")
            target = self.schemas["reference"]["$defs"][fragment]
            if self.validate(target, value, location, errors, references) and ":" not in value:
                references.append((location, value))
        if "type" not in schema:
            return False
        kind = schema["type"]
        if kind == "integer":
            matches = type(value) is int or (type(value) is float and value.is_integer())
        else:
            matches = isinstance(value, JSON_TYPES[kind])
        if not matches:
            errors.append(f"{location}: expected {kind}")
            return False

        before = len(errors)
        if kind == "object":
            properties = schema.get("properties", {})
            for key in sorted(set(schema.get("required", ())) - value.keys()):
                errors.append(f"{location}: missing {key}")
            if schema.get("additionalProperties") is False and value.keys() - properties.keys():
                errors.append(f"{location}: contains unknown properties")
            for key, property_schema in properties.items():
                if key in value:
                    self.validate(property_schema, value[key], f"{location}.{key}", errors, references)
        elif kind == "array":
            if len(value) < schema.get("minItems", 0):
                errors.append(f"{location}: requires at least {schema['minItems']} entries")
            if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
                errors.append(f"{location}: entries must be unique")
            for index, item in enumerate(value):
                self.validate(schema["items"], item, f"{location}[{index}]", errors, references)
        elif kind == "string":
            if len(value) < schema.get("minLength", 0):
                errors.append(f"{location}: must not be empty")
            if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
                errors.append(f"{location}: does not match {schema['pattern']}")
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{location}: must be at least {schema['minimum']}")
        if "const" in schema and value != schema["const"]:
            errors.append(f"{location}: must equal {schema['const']!r}")
        return len(errors) == before


def artifact_paths(directory, kind):
    if kind != "contract-index":
        yield from sorted(path for path in directory.iterdir() if path.suffix == ".yaml")
        return
    for interface in sorted(directory.iterdir()):
        if stat.S_ISDIR(mode(interface)):
            index = interface / "api.yaml"
            if mode(index):
                yield index


def check_resource(directory, name, suffix):
    parts = Path(name).parts
    if Path(name).suffix != suffix or any(not stat.S_ISDIR(mode(directory.joinpath(*parts[:i]))) for i in range(1, len(parts))):
        return False
    resource = directory / name
    if not stat.S_ISREG(mode(resource)):
        return False
    if suffix == ".json":
        try:
            return isinstance(json.loads(resource.read_text(encoding="utf-8")), (dict, bool))
        except ValueError:
            return False
    return True


def check():
    root = Path("why")
    if not mode(root):
        return [], 0
    if not stat.S_ISDIR(mode(root)):
        return ["why: expected a local directory"], 0

    standard = Standard()
    identifiers = {}
    errors, references = [], []
    for folder, kind in ARTIFACTS.items():
        directory = root / folder
        if not mode(directory):
            continue
        if not stat.S_ISDIR(mode(directory)):
            errors.append(f"{directory}: expected a local directory")
            continue
        for path in artifact_paths(directory, kind):
            if not stat.S_ISREG(mode(path)):
                errors.append(f"{path}: expected a local YAML file")
                continue
            artifact = load_yaml(path)
            if not standard.validate(standard.schemas[kind], artifact, str(path), errors, references):
                continue
            identifier = artifact["id"]
            entries = [artifact]
            if kind == "contract-index":
                entries.extend(artifact["contracts"])
                for contract in artifact["contracts"]:
                    for field, suffix in (("document", ".md"), ("schema", ".json")):
                        if field in contract and not check_resource(path.parent, contract[field], suffix):
                            errors.append(f"{path}: {contract['id']}.{field} must name a local {suffix} file")
            elif path.stem != identifier:
                errors.append(f"{path}: ID {identifier} does not match the file name")
            for entry in entries:
                identifier = entry["id"]
                if identifier in identifiers:
                    errors.append(f"{path}: duplicate ID {identifier} (also in {identifiers[identifier]})")
                identifiers.setdefault(identifier, path)
    for location, reference in references:
        if reference not in identifiers:
            errors.append(f"{location}: unknown local reference {reference}")
    return errors, len(identifiers)


def main():
    try:
        errors, count = check()
        if errors:
            for error in errors:
                print(f"FAIL: {error}", file=sys.stderr)
            return 1
        print(f"PASS: validated {count} Why artifact IDs")
        return 0
    except FileNotFoundError as error:
        print(f"ERROR: missing file or tool: {error.filename}", file=sys.stderr)
    except (RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
    except Exception as error:  # noqa: BLE001 - Technical errors must return 2.
        print(f"ERROR: unexpected {type(error).__name__}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
