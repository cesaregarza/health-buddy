"""Fixed isolated fetch worker; all URLs and local output pass bounded admission."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Fixed installed-source path, never a path from stdin or the owner workspace.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from health_buddy.runtime_inputs import _download_inputs, _wire_inputs
from health_buddy.runtime_manifest import ManifestError


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(262145)
        if len(raw) > 262144:
            raise ManifestError("input_worker_request_limit")
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"directory", "inputs"}:
            raise ManifestError("invalid_input_worker_request")
        directory = value["directory"]
        if not isinstance(directory, str) or not 1 <= len(directory) <= 4096:
            raise ManifestError("invalid_input_worker_request")
        _download_inputs(_wire_inputs(value["inputs"]), Path(directory))
    except (ManifestError, OSError, ValueError, TypeError, RecursionError):
        # Parent receives a fixed failure, never upstream headers, URLs or paths.
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
