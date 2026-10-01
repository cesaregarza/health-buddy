"""One bounded recent worker failure per extension, without inputs or output."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from health_buddy.core.config import Config
from health_buddy.core.domain import encode
from health_buddy.core.durability import atomic_bytes
from health_buddy.core.files import extension_id, private_directory, read_json
from health_buddy.core.service_api import JSON, ServiceError
from health_buddy.extension.runner import call

FAILURES = frozenset(
    {
        "extension_execution_failed",
        "extension_timeout",
        "extension_output_invalid",
        "extension_input_too_large",
    }
)


def failure_path(config: Config, name: str) -> Path:
    return config.path(f"operations/operator-diagnostics/{extension_id(name)}.json")


def observed_call(
    config: Config, name: str, entrypoint: str, root: Path, payload: dict[str, JSON]
) -> JSON:
    try:
        return call(entrypoint, root, payload)
    except ServiceError as exc:
        if exc.code in FAILURES:
            try:
                path = failure_path(config, name)
                private_directory(path.parent, create=True)
                atomic_bytes(
                    path,
                    encode(
                        {
                            "schemaVersion": 1,
                            "code": exc.code,
                            "observedAt": datetime.now(UTC).isoformat(),
                        }
                    ),
                )
            except (OSError, ValueError, ServiceError):
                # Diagnostic retention must never replace the original error.
                pass
        raise


def recent_failure(config: Config, name: str) -> JSON:
    path = failure_path(config, name)
    try:
        value = read_json(path, 1024)
        if (
            not isinstance(value, dict)
            or set(value) != {"schemaVersion", "code", "observedAt"}
            or value["schemaVersion"] != 1
            or not isinstance(value["code"], str)
            or value["code"] not in FAILURES
            or not isinstance(value["observedAt"], str)
        ):
            return {"state": "unavailable"}
        stamp = datetime.fromisoformat(value["observedAt"])
        if stamp.tzinfo is None:
            return {"state": "unavailable"}
        return {
            "state": "recorded",
            "code": value["code"],
            "observedAt": stamp.isoformat(),
        }
    except FileNotFoundError:
        return {"state": "unknown", "history": "not_proof_of_success"}
    except (OSError, ValueError, TypeError, ServiceError):
        return {"state": "unavailable"}
