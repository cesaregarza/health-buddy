"""Synthetic adapter checks. All execution belongs to the testing queue."""

from __future__ import annotations

import asyncio
import gzip
import json
import threading
import time
from dataclasses import replace

import pytest

from health_buddy.service_api import Principal, Response
from health_buddy.transport import ENDPOINTS, Transport, create_app
from health_buddy.transport_jobs import Jobs
from health_buddy.transport_limits import DEFAULT_LIMITS, EnvelopeError


class FakeOperations:
    def __init__(self):
        self.calls = []
        self.preflights = []
        self.denial = None
        self.result = Response(200, b'{"data":{"ok":true},"meta":{}}')

    def preflight(self, principal, operation):
        self.preflights.append((principal, operation))
        return self.denial

    def execute(self, principal, request):
        self.calls.append((principal, request))
        return self.result


async def exchange(app, method="GET", target="/v1/capabilities", body=b"", extra=(), *, receive=None):
    path, _, query = target.partition("?")
    values = [
        (b"host", b"127.0.0.1:8791"),
        (b"origin", b"http://127.0.0.1:8791"),
    ]
    if method in ("POST", "PUT"):
        values += [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    # Tests may replace a normal header, or deliberately repeat a supplied one.
    names = {key for key, _ in extra}
    values = [(key, value) for key, value in values if key not in names] + list(extra)
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": query.encode(),
        "root_path": "", "headers": values,
        "server": ("127.0.0.1", 8791), "client": ("127.0.0.1", 12345),
    }
    messages = []

    async def receive_once():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive or receive_once, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    raw = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], raw, dict(start["headers"])


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_every_protected_route_defaults_to_deny(endpoint):
    operations = FakeOperations()
    app = create_app(operations)
    status, raw, values = await exchange(app, endpoint.method, endpoint.path.replace("{resource}", "example"), b"{}")
    assert status == 401
    assert json.loads(raw) == {"error": {"code": "unauthenticated", "retryable": False}, "meta": {}}
    assert values[b"cache-control"] == b"no-store"
    assert not operations.calls and not operations.preflights


async def test_only_liveness_is_unprotected():
    operations = FakeOperations()
    app = create_app(operations)
    assert (await exchange(app, target="/livez"))[:2] == (200, b'{"status":"ok"}')
    assert (await exchange(app, target="/absent"))[0] == 401
    assert (await exchange(app, target="/livez?fields=identity"))[0] == 401
    assert not operations.calls


@pytest.mark.parametrize("target,method,status", [
    ("/absent", "GET", 404), ("/v1/capabilities", "POST", 405),
    ("/v1/capabilities", "HEAD", 405), ("/v1/capabilities/", "GET", 404),
])
async def test_framework_errors_keep_safe_envelope(target, method, status):
    app = create_app(FakeOperations(), development=True)
    actual, raw, values = await exchange(app, method, target, b"{}")
    assert actual == status
    assert set(json.loads(raw)) == {"error", "meta"}
    assert json.loads(raw)["meta"] == {}
    assert values[b"connection"] == b"close"
    assert values[b"x-content-type-options"] == b"nosniff"


async def test_resolver_only_passes_opaque_principal_and_server_observed_peer():
    operations = FakeOperations()
    observed = []
    principal = Principal("fabricated-resolver-handle")

    def authenticate(connection, values):
        observed.append((connection, values))
        return principal

    app = create_app(operations, authenticate=authenticate)
    status, _, _ = await exchange(app, extra=[(b"x-forwarded-for", b"203.0.113.8"), (b"x-forwarded-proto", b"https")])
    assert status == 200
    assert observed[0][0].peer == ("127.0.0.1", 12345)
    assert observed[0][0].scheme == "http"
    assert operations.preflights == [(principal, "capabilities")]
    assert operations.calls[0][0] == principal


async def test_policy_denial_does_not_read_body_and_development_cannot_bypass_it():
    operations = FakeOperations()
    operations.denial = Response(403, b'{"error":{"code":"forbidden"},"meta":{}}')

    async def forbidden_receive():
        raise AssertionError("Unauthorized body was read")

    status, raw, _ = await exchange(create_app(operations, development=True), "POST", "/api/workouts", b"{}", receive=forbidden_receive)
    assert (status, raw) == (403, operations.denial.body)
    assert not operations.calls


@pytest.mark.parametrize("target,body,extra,status", [
    ("/v1/workouts", b'{"a":1,"a":2}', (), 422),
    ("/v1/workouts", b'{"a":NaN}', (), 422),
    ("/v1/workouts", b'{"a":1e999}', (), 422),
    ("/v1/workouts", b'{"a":"\\ud800"}', (), 422),
    ("/v1/workouts", b'[]', (), 422),
    ("/v1/workouts", b'\xff', (), 422),
    ("/v1/workouts", b'{}', ((b"content-type", b"text/plain"),), 415),
    ("/v1/workouts", b'{}', ((b"content-length", b"999999"),), 413),
    ("/v1/workouts", b'{}', ((b"content-length", b"3"),), 400),
    ("/v1/workouts", b'{}', ((b"content-length", b"-1"),), 411),
    ("/v1/workouts", b'{}', ((b"content-encoding", b"gzip"),), 415),
    ("/v1/workouts", b'{}', ((b"authorization", b"one"), (b"authorization", b"two")), 400),
    ("/v1/workouts", b'{}', ((b"if-match", b'"rev-0"'), (b"if-match", b'"rev-1"')), 400),
    ("/v1/workouts", b'{}', ((b"transfer-encoding", b"chunked"),), 400),
    ("/v1/workouts", b'{}', ((b"host", b"example.invalid"),), 400),
    ("/v1/workouts", b'{}', ((b"origin", b"https://example.invalid"),), 403),
    ("/v1/workouts", b'{}', ((b"sec-fetch-site", b"cross-site"),), 403),
    ("/v1/%77orkouts", b'{}', (), 400),
    ("/v1/../workouts", b'{}', (), 400),
    ("/v1//workouts", b'{}', (), 400),
    ("/v1/workouts?unexpected=true", b'{}', (), 422),
])
async def test_malformed_envelopes_do_not_reach_execute(target, body, extra, status):
    operations = FakeOperations()
    actual, raw, _ = await exchange(create_app(operations, development=True), "POST", target, body, extra)
    assert actual == status, raw
    assert json.loads(raw)["meta"] == {}
    assert not operations.calls


@pytest.mark.parametrize("target,status", [
    ("/v1/context?scopes=weight&scopes=training", 422),
    ("/v1/context?scopes=%FF", 422), ("/v1/context?scopes=%XZ", 400),
    ("/v1/context?" + "scopes=x&" * 33, 422),
    ("/v1/records?sourceIds=manual&fields=value", 200),
    ("/?export=1", 200), ("/api/training/fast?date=2030-01-01&revision=example", 200),
])
async def test_query_selection_is_explicit(target, status):
    operations = FakeOperations()
    actual, _, _ = await exchange(create_app(operations, development=True), target=target)
    assert actual == status


async def test_structural_header_target_and_response_limits():
    operations = FakeOperations()
    small = replace(DEFAULT_LIMITS, json_depth=3, json_nodes=10, header_value=80, target_bytes=80, response_bytes=64)
    app = create_app(operations, development=True, limits=small)
    assert (await exchange(app, "POST", "/v1/workouts", b'{"a":{"b":{"c":1}}}'))[0] == 422
    assert (await exchange(app, "POST", "/v1/workouts", b'{"a":[1,2,3,4,5,6,7,8,9,10]}'))[0] == 422
    assert (await exchange(app, extra=[(b"authorization", b"x" * 81)]))[0] == 431
    assert (await exchange(app, target="/" + "x" * 81))[0] == 414
    assert not operations.calls
    operations.result = Response(200, b"x" * 65)
    assert (await exchange(app))[0] == 503
    operations.result = Response(200, b"{}", (("x-private-header", "hidden"),))
    assert (await exchange(app))[0] == 503


IDENTITY_HEADERS = [(b"x-installation-id", b"install"), (b"x-dataset-id", b"dataset"), (b"x-restore-epoch", b"epoch")]


async def test_header_intent_and_immutable_receipts_cross_adapter_unchanged():
    operations = FakeOperations()
    raw = b'{ "data": {"saved": true}, "meta":{"dataRevision":"rev-1"}}\n'
    operations.result = Response(201, raw, (("ETag", '"rev-1"'), ("Idempotency-Replayed", "true")))
    extra = IDENTITY_HEADERS + [(b"if-match", b'"rev-0"'), (b"idempotency-key", b"example-key"), (b"x-api-version", b"1")]
    app = create_app(operations, development=True)
    status, actual, values = await exchange(app, "PUT", "/v1/records/example", b'{"sourceId":"manual"}', extra)
    request = operations.calls[0][1]
    assert (status, actual) == (201, raw)
    assert values[b"idempotency-replayed"] == b"true"
    assert request.identity.installation_id == "install"
    assert request.resource_id == "example" and request.if_match == '"rev-0"'
    assert request.idempotency_key == "example-key" and request.deadline > time.monotonic()
    assert request.payload == {"sourceId": "manual"}


async def test_healthkit_gzip_and_receiver_identity_are_preserved():
    operations = FakeOperations()
    receipt = b'{"status":"accepted","batchId":"batch","recordsAccepted":1,"deletionsAccepted":0,"duplicateBatch":false}'
    operations.result = Response(200, receipt, tuple((key.decode(), value.decode()) for key, value in IDENTITY_HEADERS))
    body = b'{"schemaVersion":1,"batchId":"batch","deviceId":"device"}'
    extra = IDENTITY_HEADERS + [(b"content-encoding", b"gzip"), (b"x-health-device-id", b"device")]
    app = create_app(operations, development=True)
    assert (await exchange(app, "POST", "/v1/healthkit/batches", gzip.compress(body), extra))[:2] == (200, receipt)
    assert operations.calls[0][1].health_device_id == "device"
    assert operations.calls[0][1].payload == json.loads(body)
    for broken in ((), (("x-installation-id", "wrong"),)):
        operations.result = Response(200, receipt, broken)
        assert (await exchange(app, "POST", "/v1/healthkit/batches", gzip.compress(body), extra))[0] == 503


@pytest.mark.parametrize("variant", ["bomb", "trailing", "concatenated", "truncated"])
async def test_gzip_limits_never_submit_partial_intent(variant):
    operations = FakeOperations()
    body = gzip.compress(b'{"a":"' + b"x" * 600 + b'"}') if variant == "bomb" else gzip.compress(b"{}")
    body = body + b"trailing" if variant == "trailing" else body
    body = body + gzip.compress(b"{}") if variant == "concatenated" else body
    body = body[:-2] if variant == "truncated" else body
    app = create_app(operations, development=True, limits=replace(DEFAULT_LIMITS, healthkit_body=256))
    status, _, _ = await exchange(app, "POST", "/v1/healthkit/batches", body, [(b"content-encoding", b"gzip")])
    assert status == (413 if variant == "bomb" else 422)
    assert not operations.calls


async def test_total_body_deadline_and_disconnect_never_submit():
    operations = FakeOperations()
    app = create_app(operations, development=True, limits=replace(DEFAULT_LIMITS, body_seconds=0.02))

    async def slow():
        await asyncio.sleep(0.05)
        return {"type": "http.request", "body": b"{", "more_body": True}

    assert (await exchange(app, "POST", "/v1/workouts", b"{}", receive=slow))[0] == 408

    async def disconnected():
        return {"type": "http.disconnect"}

    assert (await exchange(app, "POST", "/v1/workouts", b"{}", receive=disconnected))[0] == 408
    assert not operations.calls


async def test_timed_out_job_retains_slot_until_actual_completion():
    jobs = Jobs(replace(DEFAULT_LIMITS, service_jobs=1, admission_seconds=0.02))
    gate = threading.Event()
    completed = []

    def decided_write():
        gate.wait(timeout=2)
        completed.append("one durable decision")
        return "original receipt"

    try:
        with pytest.raises(EnvelopeError, match="outcome_unknown"):
            await jobs.call(decided_write, deadline=time.monotonic() + 0.04)
        assert len(jobs.running) == 1
        with pytest.raises(EnvelopeError, match="service_busy"):
            await jobs.call(lambda: completed.append("must not run"), deadline=time.monotonic() + 0.2)
        assert not completed
    finally:
        gate.set()
        await jobs.close()
    assert completed == ["one durable decision"]
    with pytest.raises(EnvelopeError, match="service_unavailable"):
        await jobs.call(lambda: None, deadline=time.monotonic() + 1)


async def test_active_request_admission_is_bounded():
    operations = FakeOperations()
    app = Transport(operations, None, True, replace(DEFAULT_LIMITS, active_requests=1))
    gate = asyncio.Event()
    entered = asyncio.Event()

    async def waiting_body():
        entered.set()
        await gate.wait()
        return {"type": "http.request", "body": b"{}", "more_body": False}

    task = asyncio.create_task(exchange(app, "POST", "/v1/workouts", b"{}", receive=waiting_body))
    await asyncio.wait_for(entered.wait(), 1)
    try:
        status, raw, values = await exchange(app)
        assert status == 503 and json.loads(raw)["error"]["retryable"]
        assert values[b"retry-after"] == b"1"
    finally:
        gate.set()
        await task
        await app.jobs.close()
