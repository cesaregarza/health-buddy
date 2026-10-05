"""Fixed data-only release fetch worker; never a downloaded executable."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from health_buddy.core.service_api import ServiceError
from health_buddy.install.acquire import download
from health_buddy.install.errors import store_retry_refusal


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise ValueError("bounded request")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("data-only request")
        download(value)
    except ServiceError as error:
        print(json.dumps({"code": error.code, **store_retry_refusal(error)}))
        return 1
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"code": "install_acquire_transport_failed"}))
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
