"""Fabricated security authority for adapter evidence, never production policy."""

import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from health_buddy.security_api import (
    Authenticated,
    BearerProof,
    ClientIdentity,
    CookieDirective,
    IngressConfig,
    ProxyProof,
    Runtime,
    SecretDelivery,
    SecurityReply,
    SessionProof,
)
from health_buddy.service_api import Identity, Principal, Response, ServiceError

ORIGIN = "https://synthetic.example"
TOKEN = "synthetic-owner-" + "a" * 32
SESSION = "synthetic-session-" + "b" * 32
CSRF = "synthetic-csrf-" + "c" * 32
IDENTITY = Identity(
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
    "00000000-0000-4000-8000-000000000003",
)
CLIENT = ClientIdentity("synthetic-actor", "synthetic-epoch", IDENTITY)


class FakeSecurity:
    def __init__(self, boundary=None):
        self.boundary = boundary
        self.calls = []
        self.mechanisms = {}
        self.secret_override = None

    def authenticate(self, proof):
        if isinstance(proof, BearerProof) and proof.token == TOKEN:
            mechanism = "bearer"
        elif isinstance(proof, SessionProof) and proof.token == SESSION:
            mechanism = "session"
        elif (
            isinstance(proof, ProxyProof)
            and proof.boundary is self.boundary
            and self.boundary is not None
            and proof.subject == "owner@example.invalid"
        ):
            mechanism = "proxy"
        else:
            raise ServiceError(401, "unauthenticated")
        principal = Principal("synthetic-" + mechanism)
        self.mechanisms[principal.credential_id] = mechanism
        return Authenticated(
            principal,
            mechanism,
            CLIENT,
            isinstance(proof, SessionProof) and proof.csrf == CSRF,
        )

    def describe(self, principal):
        if principal.credential_id not in self.mechanisms:
            raise ServiceError(401, "unauthenticated")
        return CLIENT

    def preflight(self, principal, action):
        if action not in ("bootstrap.redeem", "pairing.redeem") and principal is None:
            raise ServiceError(401, "unauthenticated")

    def execute(self, principal, request):
        self.calls.append(request)
        mechanism = self.mechanisms.get(principal.credential_id) if principal else None
        if request.action == "session.create":
            if mechanism not in ("bearer", "proxy"):
                raise ServiceError(403, "forbidden")
            return SecurityReply(
                200, client=CLIENT, cookie=CookieDirective("issue", SESSION, 3600)
            )
        if request.action == "session.get":
            secret = self.secret_override or (
                SecretDelivery("csrf", CSRF) if mechanism == "session" else None
            )
            return SecurityReply(200, client=CLIENT, secret=secret)
        if request.action == "session.revoke":
            return SecurityReply(
                200, {"revoked": True}, cookie=CookieDirective("clear")
            )
        if request.action == "bootstrap.redeem":
            return SecurityReply(
                200, client=CLIENT, secret=SecretDelivery("owner-token", TOKEN)
            )
        return SecurityReply(200, {"ok": True}, client=CLIENT)


class FakeOperations:
    def __init__(self):
        self.calls = []

    def preflight(self, principal, operation):
        return None

    def execute(self, principal, request):
        self.calls.append(request)
        return Response(200, b'{"data":{"saved":true},"meta":{}}')


def fake_runtime(socket_path="", *, uds=False):
    boundary = object() if uds else None
    return Runtime(
        FakeOperations(),
        FakeSecurity(boundary),
        IngressConfig(
            "tailscale-uds" if uds else "loopback",
            ORIGIN,
            "owner@example.invalid",
            socket_path,
            3600,
        ),
        boundary,
    )


@contextmanager
def short_socket_directory():
    """Only the queue-admitted private parent; no ambient /tmp fallback."""
    value = os.environ.get("HEALTH_BUDDY_TEST_SOCKET_ROOT")
    if not value:
        pytest.fail("Queue must supply HEALTH_BUDDY_TEST_SOCKET_ROOT")
    parent = Path(value)
    if (
        not parent.is_absolute()
        or ".." in parent.parts
        or parent == Path("/tmp")  # noqa: S108 - Reject the public temp root.
        or parent.is_relative_to("/mnt")
        or any(item.is_symlink() for item in (parent, *parent.parents))
    ):
        pytest.fail("Socket fixture requires its admitted native private parent")
    details = parent.stat()
    longest = parent / "u-xxxxxxxx/w/security/http.sock"
    if (
        not stat.S_ISDIR(details.st_mode)
        or stat.S_IMODE(details.st_mode) != 0o700
        or details.st_uid != os.geteuid()
        or len(os.fsencode(longest)) > 107
    ):
        pytest.fail("Socket fixture parent is unsafe or too long")
    with tempfile.TemporaryDirectory(prefix="u-", dir=parent) as folder:
        yield Path(folder)
