#!/usr/bin/env python3
"""
Lint schemas — enforce invariants per BUILD_PLAN.md.

F3.1 (finding 10): No free-text string fields in structure-route schemas.
Only an explicit allowlist of provenance fields is permitted.

Usage: python tools/lint_schemas.py
"""

import json
import sys
from pathlib import Path

# Schemas that sit on a structure route (must have no free-text fields)
STRUCTURE_ROUTE_SCHEMAS = [
    "schemas/classification/classification.schema.json",
]

# Allowlisted field names that may be string-typed in structure-route schemas.
# These are provenance fields that *our code* writes, not the model.
ALLOWLIST = {
    "sourceRef",
    "schema",
    # modelInfo provenance
    "modelId",
    "promptVersion",
    "schemaVersion",
    "cacheHit",
    "costUsd",
}


def find_string_fields(schema: dict, path: str = "#") -> list[str]:
    """Find all string-typed properties in a JSON Schema (recursive).
    
    Excludes enums (closed values, not free-text) and $ref references.
    """
    fields: list[str] = []
    
    # Check properties
    if "properties" in schema:
        for prop_name, prop_schema in schema["properties"].items():
            prop_path = f"{path}.properties.{prop_name}"
            if isinstance(prop_schema, dict):
                # Skip enums — closed values are not free-text
                if prop_schema.get("type") == "string" and "enum" not in prop_schema:
                    if prop_name not in ALLOWLIST:
                        fields.append(f"{prop_path} (type=string)")
                # Recurse into nested objects and oneOf/anyOf/allOf
                if "properties" in prop_schema:
                    fields.extend(find_string_fields(prop_schema, prop_path))
    
    # Check $defs
    if "$defs" in schema:
        for def_name, def_schema in schema["$defs"].items():
            def_path = f"{path}.$defs.{def_name}"
            fields.extend(find_string_fields(def_schema, def_path))
    
    # Check oneOf
    if "oneOf" in schema:
        for i, variant in enumerate(schema["oneOf"]):
            fields.extend(find_string_fields(variant, f"{path}.oneOf[{i}]"))
    
    return fields


def main():
    repo_root = Path(__file__).resolve().parent.parent
    violations: list[str] = []
    
    for rel_path in STRUCTURE_ROUTE_SCHEMAS:
        schema_path = repo_root / rel_path
        if not schema_path.exists():
            violations.append(f"MISSING: {rel_path} (not found)")
            continue
        
        try:
            schema = json.loads(schema_path.read_bytes())
        except json.JSONDecodeError as e:
            violations.append(f"INVALID: {rel_path}: {e}")
            continue
        
        string_fields = find_string_fields(schema)
        for field in string_fields:
            violations.append(f"FREE_TEXT: {rel_path}: {field}")
    
    if violations:
        print(f"Schema lint FAILED: {len(violations)} violation(s)")
        for v in violations:
            print(f"  {v}")
        print()
        print("Note: free-text fields in structure-route schemas are banned by")
        print("BUILD_PLAN.md §3.16. Use services/alttext for text-generating routes.")
        return 1
    else:
        print("Schema lint PASSED — no free-text violations")
        return 0


if __name__ == "__main__":
    sys.exit(main())
