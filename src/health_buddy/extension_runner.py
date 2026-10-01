"""Bounded owned child for schema checks and reviewed native pure entrypoints."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from health_buddy.core.domain import decode, encode
from health_buddy.core.extension_api import MAX_RESULT_BYTES, RUN_SECONDS
from health_buddy.core.service_api import JSON, ServiceError


def invoke(request: dict[str, JSON]) -> JSON:
    raw = encode(request)
    if len(raw) > 1_048_576:
        raise ServiceError(413, "extension_input_too_large")
    worker = Path(__file__).with_name("extension_worker.py")
    # No inherited environment secrets, PYTHONPATH, user site, cwd imports,
    # shell command, or unbounded captured stderr. This is not a sandbox.
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(  # noqa: S603 - Fixed maintained worker, no shell.
            [sys.executable, "-I", "-B", str(worker)],
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=subprocess.DEVNULL,
            cwd=worker.parent,
            env={"LANG": "C.UTF-8"},
            start_new_session=True,
        )
        try:
            process.communicate(raw, timeout=RUN_SECONDS)
            if process.returncode != 0:
                raise ServiceError(503, "extension_execution_failed")
        except subprocess.TimeoutExpired:
            raise ServiceError(503, "extension_timeout") from None
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
        output.seek(0)
        try:
            return decode(output.read(MAX_RESULT_BYTES + 1), limit=MAX_RESULT_BYTES)
        except ServiceError:
            raise ServiceError(503, "extension_output_invalid") from None


def validate_config(schema: JSON, config: JSON) -> None:
    result = invoke({"mode": "schema", "schema": schema, "input": config})
    if result != {"valid": True}:
        raise ServiceError(422, "extension_config_invalid")


def call(entrypoint: str, root: Path, payload: dict[str, JSON]) -> JSON:
    name, function = entrypoint.split(":", 1)
    return invoke(
        {
            "mode": "call",
            "file": str(root / name),
            "function": function,
            "input": payload,
        }
    )
