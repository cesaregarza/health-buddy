"""Focused security adapter cases; real authority integration is separate."""

import json

import pytest

from health_buddy.security_api import SecretDelivery
from health_buddy.transport import create_app
from health_buddy.transport_ingress import VerifiedSocket, prepare_socket
from health_buddy.transport_security import COOKIE
from tests.auth_transport_fixtures import CSRF, ORIGIN, SESSION, TOKEN, fake_runtime
from tests.test_transport import exchange

ORIGIN_HEADER = (b"origin", ORIGIN.encode())
BEARER = (b"authorization", ("Bearer " + TOKEN).encode())
COOKIE_HEADER = (b"cookie", (COOKIE + "=" + SESSION).encode())


async def test_cookie_csrf_is_required_again_on_every_mutation():
    runtime = fake_runtime()
    app = create_app(runtime=runtime)
    for token, expected in ((CSRF, 200), (None, 403), ("wrong", 403), (CSRF, 200)):
        headers = [ORIGIN_HEADER, COOKIE_HEADER]
        if token is not None:
            headers.append((b"x-csrf-token", token.encode()))
        status, _, _ = await exchange(app, "POST", "/v1/workouts", b"{}", headers)
        assert status == expected
    assert len(runtime.operations.calls) == 2


async def test_loopback_forged_proxy_cannot_create_owner_or_read_health():
    runtime = fake_runtime()
    app = create_app(runtime=runtime)
    headers = [
        ORIGIN_HEADER,
        (b"tailscale-user-login", b"owner@example.invalid"),
        (b"x-health-buddy-browser", b"1"),
    ]
    assert (await exchange(app, "POST", "/v1/sessions", b"{}", headers))[0] == 401
    for path in ("/", "/icon.svg", "/api/context/pack", "/v1/records"):
        assert (await exchange(app, target=path, extra=headers))[0] == 401
    assert not runtime.operations.calls and not runtime.security.calls


async def test_session_cookie_attributes_and_private_csrf_channel():
    app = create_app(runtime=fake_runtime())
    status, raw, headers = await exchange(
        app,
        "POST",
        "/v1/sessions",
        b"{}",
        [ORIGIN_HEADER, BEARER, (b"x-health-buddy-browser", b"1")],
    )
    assert status == 200 and b"secret" not in raw
    assert (
        headers[b"set-cookie"].decode()
        == f"{COOKIE}={SESSION}; Path=/; Max-Age=3600; Secure; HttpOnly; SameSite=Strict"
    )
    assert headers[b"cache-control"] == b"no-store"
    status, raw, _ = await exchange(
        app, target="/v1/session", extra=[ORIGIN_HEADER, COOKIE_HEADER]
    )
    assert status == 200 and json.loads(raw)["secret"] == {
        "kind": "csrf",
        "value": CSRF,
    }
    status, raw, headers = await exchange(
        app,
        "DELETE",
        "/v1/session",
        extra=[ORIGIN_HEADER, COOKIE_HEADER, (b"x-csrf-token", CSRF.encode())],
    )
    assert status == 200 and b"Max-Age=0" in headers[b"set-cookie"]
    assert SESSION.encode() not in raw and CSRF.encode() not in raw


async def test_bearer_self_binding_never_returns_session_or_csrf():
    runtime = fake_runtime()
    app = create_app(runtime=runtime)
    status, raw, headers = await exchange(
        app, target="/v1/session", extra=[ORIGIN_HEADER, BEARER]
    )
    assert status == 200 and "client" in json.loads(raw)["data"]
    assert b"secret" not in raw and b"set-cookie" not in headers
    runtime.security.secret_override = SecretDelivery("csrf", CSRF)
    status, raw, _ = await exchange(
        app, target="/v1/session", extra=[ORIGIN_HEADER, BEARER]
    )
    assert status == 503 and CSRF.encode() not in raw


@pytest.mark.parametrize(
    "extra",
    [
        [COOKIE_HEADER, BEARER],
        [COOKIE_HEADER, (b"cookie", (COOKIE + "=" + SESSION).encode())],
        [
            (
                b"cookie",
                (COOKIE + "=" + SESSION + "; " + COOKIE + "=" + SESSION).encode(),
            )
        ],
        [BEARER, BEARER],
    ],
)
async def test_ambiguous_credentials_fail_before_authority(extra):
    runtime = fake_runtime()
    status, _, _ = await exchange(
        create_app(runtime=runtime), extra=[ORIGIN_HEADER, *extra]
    )
    assert status == 400 and not runtime.operations.calls


@pytest.mark.parametrize(
    "headers,status",
    [
        ([BEARER], 403),
        ([BEARER, ORIGIN_HEADER], 403),
        (
            [
                BEARER,
                (b"origin", b"https://attacker.invalid"),
                (b"x-health-buddy-browser", b"1"),
            ],
            403,
        ),
    ],
)
async def test_login_requires_exact_origin_and_browser_header(headers, status):
    assert (
        await exchange(
            create_app(runtime=fake_runtime()), "POST", "/v1/sessions", b"{}", headers
        )
    )[0] == status


@pytest.mark.parametrize(
    "body,extra,status",
    [
        (b"{}" * 9000, [], 413),
        (b'{"proof":"a","proof":"b"}', [], 422),
        (
            b'{"proof":"synthetic-' + b"a" * 32 + b'"}',
            [(b"content-encoding", b"gzip")],
            415,
        ),
        (b'{"proof":"synthetic-' + b"a" * 32 + b'","actorId":"owner"}', [], 422),
    ],
)
async def test_security_inputs_are_bounded_and_finite(body, extra, status):
    runtime = fake_runtime()
    values = [ORIGIN_HEADER, (b"x-health-buddy-browser", b"1"), *extra]
    assert (
        await exchange(
            create_app(runtime=runtime), "POST", "/v1/bootstrap", body, values
        )
    )[0] == status
    assert not runtime.security.calls


def test_private_socket_configuration_refuses_unsafe_and_long_paths(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "sock"
    prepare_socket(path)
    path.write_text("not a socket")
    with pytest.raises(ValueError, match="already_exists"):
        prepare_socket(path)
    with pytest.raises(ValueError, match="unavailable"):
        VerifiedSocket.capture(path)
    with pytest.raises(ValueError, match="unavailable"):
        prepare_socket(tmp_path / ("x" * 108))
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError, match="unavailable"):
        prepare_socket(tmp_path / "different")


async def test_revoked_cookie_has_origin_protected_cleanup_without_authority():
    runtime = fake_runtime()
    app = create_app(runtime=runtime)
    revoked = (b"cookie", (COOKIE + "=" + "revoked-" + "z" * 32).encode())
    for headers, expected in (
        ([ORIGIN_HEADER, revoked], 403),
        ([ORIGIN_HEADER, revoked, (b"x-health-buddy-browser", b"1")], 200),
    ):
        status, raw, values = await exchange(
            app, "DELETE", "/v1/session", extra=headers
        )
        assert status == expected
        if status == 200:
            assert b"Max-Age=0" in values[b"set-cookie"]
            assert json.loads(raw)["data"] == {"cleared": True}
    assert not runtime.operations.calls and not runtime.security.calls
    # Subsequent explicit bearer login works; no automatic cookie authority.
    assert (
        await exchange(
            app,
            "POST",
            "/v1/sessions",
            b"{}",
            [ORIGIN_HEADER, BEARER, (b"x-health-buddy-browser", b"1")],
        )
    )[0] == 200


async def test_unauthenticated_browser_document_gets_fixed_login_without_health():
    runtime = fake_runtime()
    status, raw, headers = await exchange(
        create_app(runtime=runtime),
        target="/",
        extra=[ORIGIN_HEADER, (b"accept", b"text/html")],
    )
    assert status == 303 and headers[b"location"] == b"/login" and raw == b""
    assert not runtime.operations.calls
