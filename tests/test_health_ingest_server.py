from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from health_ingest.server import HealthIngestServer, create_server
from health_ingest.storage import HealthRepository
from tests.test_health_ingest_models import batch_payload


@contextmanager
def running_server(tmp_path: Path) -> Iterator[tuple[str, str, str]]:
    repository = HealthRepository(tmp_path / "health.db")
    device_id = str(uuid4())
    repository.migrate()
    token = repository.issue_device(device_id, "test phone")
    server: HealthIngestServer = create_server("127.0.0.1", 0, repository)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", device_id, token
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    device_id: str | None = None,
    token: str | None = None,
) -> tuple[int, dict[str, Any]]:
    headers: dict[str, str] = {}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if device_id is not None:
        headers["X-Health-Device-ID"] = device_id
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(  # noqa: S310
        url, data=body, headers=headers, method=method
    )
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_upload_status_and_no_read_endpoint(tmp_path: Path) -> None:
    with running_server(tmp_path) as (base_url, device_id, token):
        payload = batch_payload(device_id)
        status, result = request_json(
            f"{base_url}/v1/healthkit/batches",
            method="POST",
            payload=payload,
            device_id=device_id,
            token=token,
        )
        assert status == 200
        assert result["recordsAccepted"] == 1

        status, result = request_json(
            f"{base_url}/v1/status", device_id=device_id, token=token
        )
        assert status == 200
        assert result["recordCount"] == 1

        status, _ = request_json(
            f"{base_url}/v1/records", device_id=device_id, token=token
        )
        assert status == 404


def test_token_cannot_upload_for_another_device(tmp_path: Path) -> None:
    with running_server(tmp_path) as (base_url, device_id, token):
        status, _ = request_json(
            f"{base_url}/v1/healthkit/batches",
            method="POST",
            payload=batch_payload(str(uuid4())),
            device_id=device_id,
            token=token,
        )
        assert status == 403


def test_bad_token_is_rejected_before_payload(tmp_path: Path) -> None:
    with running_server(tmp_path) as (base_url, device_id, _):
        status, result = request_json(
            f"{base_url}/v1/healthkit/batches",
            method="POST",
            payload=batch_payload(device_id),
            device_id=device_id,
            token="wrong",
        )
        assert status == 401
        assert result == {"error": "invalid device credentials"}
