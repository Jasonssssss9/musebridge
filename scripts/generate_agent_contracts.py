"""Generate boundary types and the Go embedded schema from the shared v2 schema.

Run with --check in CI. Validation remains authoritative: generated union
containers represent wire fields, while the schema selects legal variants.
Only the Python standard library and gofmt are required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/contracts/agent-v2.schema.json"
DOCUMENT = json.loads(SOURCE.read_text())
DEFINITIONS = DOCUMENT["$defs"]
GO_DECLARATIONS: dict[str, str] = {}
PY_DECLARATIONS: dict[str, str] = {}


def title(value: str) -> str:
    initials = {"id": "ID", "ids": "IDs", "url": "URL", "uuid": "UUID"}
    return "".join(initials.get(word, word[:1].upper() + word[1:])
                   for word in value.split("_"))


def object_shape(node: dict) -> dict | None:
    if "$ref" in node:
        return object_shape(DEFINITIONS[node["$ref"].split("/")[-1]])
    if "allOf" in node:
        shapes = ([node] if node.get("type") == "object" else []) + [shape for part in node["allOf"]
                  if (shape := object_shape(part)) is not None]
        if not shapes:
            return None
        result = {"type": "object", "properties": {}, "required": []}
        for shape in shapes:
            result["properties"].update(shape.get("properties", {}))
            result["required"] += shape.get("required", [])
        return result
    if node.get("type") == "object":
        return node
    variants = node.get("oneOf", node.get("anyOf"))
    if variants:
        shapes = [object_shape(part) for part in variants]
        if any(shape is None for shape in shapes):
            return None
        properties: dict[str, list[dict]] = {}
        required = set(shapes[0].get("required", []))
        for shape in shapes:
            required &= set(shape.get("required", []))
            for key, value in shape.get("properties", {}).items():
                if value not in properties.setdefault(key, []):
                    properties[key].append(value)
        return {"type": "object", "required": sorted(required),
                "properties": {key: values[0] if len(values) == 1 else {"anyOf": values}
                               for key, values in properties.items()}}
    return None


def go_type(node: dict, name: str) -> str:
    if "$ref" in node:
        return node["$ref"].split("/")[-1]
    for part in node.get("allOf", []):
        if "$ref" in part:
            # Additional allOf clauses restrict the referenced wire fields.
            base = object_shape(part)
            shape = object_shape(node)
            if base and shape and set(shape["properties"]) <= set(base["properties"]):
                return go_type(part, name)
    if (shape := object_shape(node)) is not None:
        if name not in GO_DECLARATIONS:
            GO_DECLARATIONS[name] = ""
            fields = []
            for key, value in shape["properties"].items():
                kind = go_type(value, name + title(key))
                optional = key not in shape.get("required", [])
                if optional:
                    kind = f"Optional[{kind}]"
                tag = key + (",omitzero" if optional else "")
                fields.append(f'\t{title(key)} {kind} `json:"{tag}"`')
            GO_DECLARATIONS[name] = f"type {name} struct {{\n" + "\n".join(fields) + "\n}\n"
        return name
    variants = node.get("oneOf", node.get("anyOf"))
    if variants:
        nullable = any(part.get("type") == "null" for part in variants)
        kinds = list(dict.fromkeys(go_type(part, name) for part in variants
                                   if part.get("type") != "null"))
        if len(kinds) > 1 and "[]any" in kinds:
            kinds.remove("[]any")
        if len(kinds) != 1:
            raise ValueError(f"Unsupported wire union {name}: {kinds}")
        return ("*" if nullable else "") + kinds[0]
    kind = node.get("type")
    if kind is None and "const" in node:
        kind = {str: "string", int: "integer", bool: "boolean"}[type(node["const"])]
    if kind == "array":
        return "[]" + go_type(node["items"], name + "Item") if "items" in node else "[]any"
    return {"string": "string", "integer": "int64", "number": "float64",
            "boolean": "bool", "null": "any"}[kind]


def py_type(node: dict, name: str) -> str:
    if "$ref" in node:
        return node["$ref"].split("/")[-1]
    if node.get("type") == "object" or ("allOf" in node and object_shape(node) is not None):
        shape = object_shape(node)
        if shape is None:
            raise ValueError(name)
        if name not in PY_DECLARATIONS:
            PY_DECLARATIONS[name] = ""
            fields = []
            for key, value in shape["properties"].items():
                kind = py_type(value, name + title(key))
                if key not in shape.get("required", []):
                    kind = f"NotRequired[{kind}]"
                fields.append(f"    {key}: {kind}")
            PY_DECLARATIONS[name] = f"class {name}(TypedDict):\n" + "\n".join(fields or ["    pass"]) + "\n"
        return name
    variants = node.get("oneOf", node.get("anyOf"))
    if variants:
        # Separate variants retain discriminators and required nullable fields.
        kinds = [py_type(part, name + f"Variant{i}") for i, part in enumerate(variants, 1)]
        return "Union[" + ", ".join(repr(k) if re.fullmatch(r"\w+", k) and k != "None" else k
                                     for k in kinds) + "]"
    if "const" in node:
        return f"Literal[{node['const']!r}]"
    if "enum" in node:
        return "Literal[" + ", ".join(repr(v) for v in node["enum"]) + "]"
    kind = node["type"]
    if kind == "array":
        return "list[" + py_type(node["items"], name + "Item") + "]" if "items" in node else "list[object]"
    return {"string": "str", "integer": "int", "number": "float",
            "boolean": "bool", "null": "None"}[kind]


def outputs() -> dict[Path, str]:
    for name, node in DEFINITIONS.items():
        kind = go_type(node, name)
        if kind != name:
            GO_DECLARATIONS[name] = f"type {name} {kind}\n"
        kind = py_type(node, name)
        if kind != name:
            PY_DECLARATIONS[name] = f"{name}: TypeAlias = {kind}\n"
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    go = ("// Code generated by scripts/generate_agent_contracts.py; DO NOT EDIT.\n"
          "package agentcontract\n\nimport \"reflect\"\n\n"
          f'const SchemaSHA256 = "{digest}"\n\n' +
          "\n".join(GO_DECLARATIONS.values()) + "\nvar wireTypes = map[string]reflect.Type{\n" +
          "".join(f'\t"{name}": reflect.TypeFor[{name}](),\n' for name in DEFINITIONS) + "}\n")
    go = subprocess.run(["gofmt"], input=go, text=True, capture_output=True, check=True).stdout
    py = ('"""Generated from docs/contracts/agent-v2.schema.json; do not edit.\n\n'
          'TypedDicts provide static wire types. Use ContractCodec for runtime validation.\n"""\n'
          "from __future__ import annotations\n\n"
          "from typing import Literal, NotRequired, TypeAlias, TypedDict, Union\n\n"
          f'SCHEMA_SHA256 = "{digest}"\n\n' + "\n\n".join(PY_DECLARATIONS.values()))
    return {ROOT / "api/internal/agentcontract/types_generated.go": go,
            ROOT / "api/internal/agentcontract/schema_generated.json": SOURCE.read_text(),
            ROOT / "api/internal/agentcontract/errors_generated.json": (ROOT / "docs/contracts/agent-v2.errors.json").read_text(),
            ROOT / "agent/contracts/models.py": py}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, content in outputs().items():
        if args.check:
            if not path.exists() or path.read_text() != content:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    if stale:
        print("Generated contracts are stale: " + ", ".join(stale))
        return 1
    print("Generated contracts are current." if args.check else "Generated Go/Python v2 contracts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
