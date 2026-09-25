#!/usr/bin/env python3
"""Validate contracts/: every schema is valid JSON Schema (2020-12), every example
validates against its schema, and every endpoint schema has at least one example.

Examples live at contracts/examples/<schema path without .json>/<name>.json, so
examples/worker/active_targets.response/current.json is checked against
schemas/worker/active_targets.response.json.

Requires: jsonschema[format] >= 4.18 (see contracts/python's dev extra).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent.parent / "contracts"
SCHEMAS = ROOT / "schemas"
EXAMPLES = ROOT / "examples"

# Schemas that only hold shared definitions or are referenced by another schema,
# so they need no example of their own.
DEFINITION_ONLY = {"shared/common.json", "processing/command.json"}


def load_registry() -> tuple[Registry, dict[str, dict]]:
    schemas: dict[str, dict] = {}
    resources = []
    for path in sorted(SCHEMAS.rglob("*.json")):
        rel = path.relative_to(SCHEMAS).as_posix()
        schema = json.loads(path.read_text())
        schemas[rel] = schema
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources), schemas


def main() -> int:
    registry, schemas = load_registry()
    errors: list[str] = []

    for rel, schema in schemas.items():
        expected_id = f"https://observatory.local/contracts/{rel}"
        if schema.get("$id") != expected_id:
            errors.append(f"schemas/{rel}: $id must be {expected_id}")
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:  # noqa: BLE001 - report every broken schema
            errors.append(f"schemas/{rel}: invalid schema: {exc}")

    covered: set[str] = set()
    for example in sorted(EXAMPLES.rglob("*.json")):
        rel_dir = example.parent.relative_to(EXAMPLES).as_posix()
        schema_rel = f"{rel_dir}.json"
        name = example.relative_to(EXAMPLES).as_posix()
        if schema_rel not in schemas:
            errors.append(f"examples/{name}: no schema at schemas/{schema_rel}")
            continue
        covered.add(schema_rel)
        validator = Draft202012Validator(
            schemas[schema_rel],
            registry=registry,
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        )
        for err in validator.iter_errors(json.loads(example.read_text())):
            path = "/".join(str(p) for p in err.absolute_path) or "(root)"
            errors.append(f"examples/{name}: {path}: {err.message}")

    for rel in sorted(set(schemas) - covered - DEFINITION_ONLY):
        errors.append(f"schemas/{rel}: has no example under examples/{rel.removesuffix('.json')}/")

    for line in errors:
        print(line, file=sys.stderr)
    print(f"{len(schemas)} schemas, {sum(1 for _ in EXAMPLES.rglob('*.json'))} examples, {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
