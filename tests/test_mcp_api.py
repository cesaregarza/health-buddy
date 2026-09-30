"""Real HTTPX2 parsing/stream bounds with a model transport, no network claim."""

import json
import time

import anyio
import httpx2
import pytest

from health_buddy.domain import envelope, identity_value
from health_buddy.mcp_api import MAX_RESPONSE, HttpOperations
from health_buddy.mcp_settings import Settings
from health_buddy.service_api import Request, ServiceError
from tests.test_extension_workflow import IDENTITY
from tests.test_mcp_settings import settings_file


class Body(httpx2.AsyncByteStream):
    def __init__(self, chunks, delay=0):
        self.chunks, self.delay = chunks, delay

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await anyio.sleep(self.delay)
            yield chunk


def configured(tmp_path, monkeypatch, *, status=200, body=None, headers=None, delay=0):
    path, _ = settings_file(tmp_path)
    calls, options = [], []
    raw = body if body is not None else envelope({"ok": True}, IDENTITY, 0).body

    def reply(request):
        calls.append(request)
        return httpx2.Response(status, headers=headers or {"Content-Type": "application/json"}, stream=Body([raw], delay))

    original = httpx2.AsyncClient

    def client(**kwargs):
        options.append(kwargs)
        return original(**kwargs, transport=httpx2.MockTransport(reply))

    monkeypatch.setattr("health_buddy.mcp_api.httpx2.AsyncClient", client)
    return HttpOperations(Settings.read(path)), calls, options


def test_fixed_origin_headers_no_proxy_cookie_or_redirect_policy(tmp_path, monkeypatch):
    api, calls, options = configured(tmp_path, monkeypatch)
    result = api.execute(None, Request("capabilities"))
    assert result.status == 200 and len(calls) == 1
    assert str(calls[0].url) == "https://synthetic.example.invalid/v1/capabilities"
    assert calls[0].headers["authorization"] == "Bearer " + "a" * 43
    assert "cookie" not in calls[0].headers and calls[0].headers["accept-encoding"] == "identity"
    assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
    assert options[0]["verify"] is True and options[0]["http2"] is False


@pytest.mark.parametrize("change", [
    {"headers": [("content-type", "application/json"), ("content-type", "application/json")]},
    {"headers": {"Content-Type": "application/json", "Content-Encoding": "gzip"}},
    {"headers": {"Content-Type": "application/json", "Set-Cookie": "synthetic-private-canary"}},
    {"headers": {"Content-Type": "application/json", "Content-Length": str(MAX_RESPONSE + 1)}},
    {"body": b'{"data":{"x":"\xff"},"meta":{}}'},
    {"body": b'{"data":{},"data":{},"meta":{}}'},
    {"body": b'{"data":{"x":Infinity},"meta":{}}'},
    {"body": b" " * (MAX_RESPONSE + 1)},
])
def test_remote_envelope_refuses_ambiguity_compression_and_growth(tmp_path, monkeypatch, change):
    api, calls, _ = configured(tmp_path, monkeypatch, **change)
    with pytest.raises(ServiceError) as failure:
        api.execute(None, Request("capabilities"))
    assert "synthetic-private-canary" not in str(failure.value)
    assert len(calls) == 1


def test_untrusted_error_details_are_never_reflected(tmp_path, monkeypatch):
    api, _, _ = configured(tmp_path, monkeypatch, status=403, body=json.dumps({"error": {"code": "synthetic-private-canary", "detail": "synthetic-private-canary"}, "meta": {"private": "synthetic-private-canary"}}).encode())
    result = api.execute(None, Request("capabilities"))
    assert result.status == 403 and b"synthetic-private-canary" not in result.body


def test_wrong_tuple_and_bool_revision_refuse(tmp_path, monkeypatch):
    for meta in ({**identity_value(IDENTITY), "apiVersion": 1, "dataRevision": True},
                 {**identity_value(IDENTITY), "restoreEpoch": "00000000-0000-4000-8000-000000000099", "apiVersion": 1, "dataRevision": 0}):
        with monkeypatch.context() as patch:
            api, _, _ = configured(tmp_path, patch, body=json.dumps({"data": {}, "meta": meta}).encode())
            with pytest.raises(ServiceError):
                api.execute(None, Request("capabilities"))


def test_total_deadline_cancels_slow_response(tmp_path, monkeypatch):
    api, calls, _ = configured(tmp_path, monkeypatch, delay=1)
    with api.dispatch(time.monotonic() + 0.05):
        with pytest.raises(ServiceError, match="transport_unavailable"):
            api.execute(None, Request("capabilities"))
    assert len(calls) == 1


def test_no_arbitrary_operation_query_resource_or_unselected_replay_source(tmp_path, monkeypatch):
    api, calls, _ = configured(tmp_path, monkeypatch)
    for request in (
        Request("asset.read", resource_id="private"),
        Request("context.read", query={"url": "https://other.invalid"}),
        Request("logs.write", resource_id="../private", payload={}),
        Request("logs.write", resource_id="measurement", payload={"sourceId": "unselected"}),
    ):
        with pytest.raises(ServiceError):
            api.execute(None, request)
    assert calls == []
