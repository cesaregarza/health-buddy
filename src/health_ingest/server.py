"""Minimal authenticated HTTP receiver for HealthKit upload batches."""

from __future__ import annotations

import json
import logging
import sqlite3
import zlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from health_ingest.models import BatchValidationError, parse_batch
from health_ingest.storage import (
    AuthenticationError,
    BatchConflictError,
    HealthRepository,
)

MAX_BODY_BYTES = 4 * 1024 * 1024
LOGGER = logging.getLogger("health_ingest.http")


class RequestError(RuntimeError):
    """An HTTP-safe request failure."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _gunzip_bounded(payload: bytes) -> bytes:
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        result = decompressor.decompress(payload, MAX_BODY_BYTES + 1)
        if decompressor.unconsumed_tail or len(result) > MAX_BODY_BYTES:
            raise RequestError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "decompressed request exceeds the size limit",
            )
        result += decompressor.flush(MAX_BODY_BYTES + 1 - len(result))
    except zlib.error as exc:
        raise RequestError(HTTPStatus.BAD_REQUEST, "invalid gzip body") from exc
    if len(result) > MAX_BODY_BYTES:
        raise RequestError(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            "decompressed request exceeds the size limit",
        )
    if not decompressor.eof:
        raise RequestError(HTTPStatus.BAD_REQUEST, "incomplete gzip body")
    return result


class HealthIngestServer(ThreadingHTTPServer):
    """HTTP server carrying only the repository required by request handlers."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        repository: HealthRepository,
    ) -> None:
        self.repository = repository
        super().__init__(server_address, HealthRequestHandler)


class HealthRequestHandler(BaseHTTPRequestHandler):
    """Receive uploads without offering an API for reading health values."""

    server: HealthIngestServer
    protocol_version = "HTTP/1.1"
    server_version = "HealthIngest/1"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        status = args[1] if len(args) > 1 else "unknown"
        LOGGER.info(
            "client=%s method=%s path=%s status=%s",
            self.client_address[0],
            self.command,
            urlsplit(self.path).path,
            status,
        )

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _credentials(self) -> str:
        device_id = self.headers.get("X-Health-Device-ID", "").strip()
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            raise RequestError(HTTPStatus.UNAUTHORIZED, "invalid device credentials")
        token = authorization.removeprefix("Bearer ").strip()
        try:
            self.server.repository.authenticate(device_id, token)
        except (AuthenticationError, ValueError) as exc:
            raise RequestError(
                HTTPStatus.UNAUTHORIZED, "invalid device credentials"
            ) from exc
        return device_id

    def _read_json_body(self) -> Any:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            raise RequestError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "Content-Type must be application/json",
            )
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise RequestError(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise RequestError(
                HTTPStatus.BAD_REQUEST, "invalid Content-Length"
            ) from exc
        if content_length < 0 or content_length > MAX_BODY_BYTES:
            raise RequestError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "request exceeds the size limit",
            )
        body = self.rfile.read(content_length)
        if len(body) != content_length:
            raise RequestError(HTTPStatus.BAD_REQUEST, "incomplete request body")
        encoding = self.headers.get("Content-Encoding", "identity").strip().lower()
        if encoding == "gzip":
            body = _gunzip_bounded(body)
        elif encoding not in {"", "identity"}:
            raise RequestError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "Content-Encoding must be identity or gzip",
            )
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, "invalid JSON body") from exc

    def do_GET(self) -> None:
        try:
            if self.path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok"})
                return
            if self.path == "/v1/status":
                device_id = self._credentials()
                status = self.server.repository.device_status(device_id)
                self._json(HTTPStatus.OK, {"status": "ok", **status})
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except RequestError as exc:
            self._json(exc.status, {"error": exc.message})
        except sqlite3.Error:
            LOGGER.exception("database error during status request")
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "internal server error"},
            )

    def do_POST(self) -> None:
        try:
            if self.path != "/v1/healthkit/batches":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            device_id = self._credentials()
            raw_batch = self._read_json_body()
            batch = parse_batch(raw_batch)
            if batch.device_id != device_id:
                raise RequestError(
                    HTTPStatus.FORBIDDEN,
                    "batch deviceId does not match authenticated device",
                )
            result = self.server.repository.ingest(batch)
            LOGGER.info(
                "accepted batch=%s device=%s records=%d deletions=%d duplicate=%s",
                result.batch_id,
                device_id,
                result.records_accepted,
                result.deletions_accepted,
                result.duplicate_batch,
            )
            self._json(
                HTTPStatus.OK,
                {
                    "batchId": result.batch_id,
                    "deletionsAccepted": result.deletions_accepted,
                    "duplicateBatch": result.duplicate_batch,
                    "recordsAccepted": result.records_accepted,
                    "status": "accepted",
                },
            )
        except RequestError as exc:
            self._json(exc.status, {"error": exc.message})
        except BatchValidationError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except BatchConflictError as exc:
            self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
        except sqlite3.Error:
            LOGGER.exception("database error during ingestion")
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "internal server error"},
            )

    def do_PUT(self) -> None:
        self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"})

    do_DELETE = do_PUT
    do_PATCH = do_PUT


def create_server(
    host: str,
    port: int,
    repository: HealthRepository,
) -> HealthIngestServer:
    """Create a receiver suitable for CLI use and integration tests."""

    repository.migrate()
    return HealthIngestServer((host, port), repository)
