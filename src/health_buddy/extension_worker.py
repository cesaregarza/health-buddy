"""Maintained one-shot child protocol, launched by extension_runner only.

This file intentionally uses no health_buddy imports: -I plus this exact release
file works from a source checkout as well as an installed package. Owner code
is trusted; resource limits and owned-group cleanup are failure isolation only.
"""

from __future__ import annotations

import importlib.util
import json
import math
import resource
import sys
from pathlib import Path
from typing import Any


def finite(value: Any, depth: int = 0) -> None:
    if depth > 24:
        raise ValueError("depth")
    if value is None or type(value) in (bool, int, str):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, list) and len(value) <= 1000:
        for item in value:
            finite(item, depth + 1)
        return
    if isinstance(value, dict) and len(value) <= 128:
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("key")
            finite(item, depth + 1)
        return
    raise ValueError("shape")


def schema_refs(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"$id", "$dynamicRef", "$recursiveRef"}:
                raise ValueError("reference")
            if key == "$ref" and (
                not isinstance(item, str) or not item.startswith("#/")
            ):
                raise ValueError("reference")
            schema_refs(item)
    elif isinstance(value, list):
        for item in value:
            schema_refs(item)


def main() -> int:
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    resource.setrlimit(resource.RLIMIT_AS, (268_435_456, 268_435_456))
    resource.setrlimit(resource.RLIMIT_FSIZE, (65_536, 65_536))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    try:
        raw = sys.stdin.buffer.read(1_048_577)
        if len(raw) > 1_048_576:
            return 2
        request = json.loads(raw)
        finite(request)
        if request["mode"] == "schema":
            from jsonschema import Draft202012Validator
            from referencing import Registry

            try:
                schema = request["schema"]
                schema_refs(schema)
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema, registry=Registry()).validate(request["input"])
                result: Any = {"valid": True}
            except Exception:
                result = {"valid": False}
        elif request["mode"] == "call":
            path = Path(request["file"])
            specification = importlib.util.spec_from_file_location("owner_extension", path)
            if specification is None or specification.loader is None:
                return 2
            module = importlib.util.module_from_spec(specification)
            # Only the selected reviewed src directory is added. No workspace
            # root, caller cwd, credentials or Operations instance is supplied.
            sys.path.insert(0, str(path.parent))
            specification.loader.exec_module(module)
            function = getattr(module, request["function"])
            result = function(request["input"])
        else:
            return 2
        finite(result)
        output = json.dumps(result, allow_nan=False, separators=(",", ":")).encode()
        if len(output) > 65_536:
            return 2
        sys.stdout.buffer.write(output)
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        # Never expose owner code/record values, exception text or tracebacks.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
