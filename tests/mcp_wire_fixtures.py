"""Owned HTTPS bridge and real SDK subprocess; synthetic data, queue only.

This bridge exercises TLS and the actual private UDS/authority/service. It is a
test proxy, not Tailscale or a deployable server. All descendants require the
queue cgroup hard timeout; fixture cleanup covers ordinary assertion failures.
"""

import ipaddress
import json
import os
import selectors
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from health_buddy.core.domain import identity_value
from health_buddy.core.security_api import AgentGrant
from tests.security_fixtures import action, secured
from tests.test_transport_auth_wire import request, server
from tests.transport_process import ROOT


def certificate(folder):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = folder / "synthetic-ca.pem", folder / "synthetic-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.chmod(0o600)
    key_path.chmod(0o600)
    return cert_path, key_path


class Bridge(HTTPServer):
    timeout = 0.1
    uds = None
    lose_next = False
    redirect_next = False
    responses = None
    seen = None
    hold_next = False
    held = None
    release = None

    def handle_error(self, request, client_address):
        # Connection loss is deliberately injected; never log headers/tokens.
        return


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(3)

    def log_message(self, format, *args):
        return

    def relay(self):
        bridge = self.server
        size = int(self.headers.get("Content-Length", "0"))
        assert 0 <= size <= 384 * 1024
        body = self.rfile.read(size) if size else None
        # Keep only operation metadata and hashes in test traces, no credentials.
        bridge.seen.append((self.command, self.path))
        if bridge.redirect_next:
            bridge.redirect_next = False
            self.send_response(307)
            self.send_header("Location", bridge.origin + "/must-not-follow")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        values = {
            key: value
            for key, value in self.headers.items()
            if key.lower()
            not in {
                "host",
                "connection",
                "content-length",
                "x-forwarded-host",
                "x-forwarded-proto",
            }
        }
        values["X-Forwarded-Host"] = bridge.origin.removeprefix("https://")
        status, raw, headers = request(
            bridge.uds, self.command, self.path, headers=values, body=body
        )
        if self.command in {"PUT", "POST"}:
            bridge.responses.append((status, raw))
            if bridge.hold_next:
                bridge.hold_next = False
                bridge.held.set()
                assert bridge.release.wait(5), (
                    "Synthetic reply hold exceeded fixture bound"
                )
            if bridge.lose_next:
                bridge.lose_next = False
                self.close_connection = True
                return
        self.send_response(status)
        for key, value in headers.items():
            if key not in {"content-length", "connection", "server", "date"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    do_GET = do_POST = do_PUT = relay


@contextmanager
def actual_backend(folder, client_folder):
    bridge = Bridge(("127.0.0.1", 0), Proxy)
    bridge.origin = f"https://127.0.0.1:{bridge.server_port}"
    bridge.responses, bridge.seen = [], []
    bridge.held, bridge.release = threading.Event(), threading.Event()
    cert, key = certificate(client_folder)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    bridge.socket = context.wrap_socket(bridge.socket, server_side=True)
    root = folder / "w"
    runtime, owner, _ = secured(root, proxy=True)
    config_path = root / "config.json"
    config = json.loads(config_path.read_bytes())
    config["security"]["externalOrigin"] = bridge.origin
    config_path.write_text(json.dumps(config))
    grant = action(
        runtime,
        owner,
        "grants.create",
        payload=AgentGrant(
            "Synthetic MCP agent",
            ("records:read", "records:write"),
            source_ids=("manual",),
            read_sources=("manual",),
            read_kinds=None,
            read_fields=None,
        ),
    )
    token_path = client_folder / "agent-token"
    token_path.write_text(grant.secret.value)
    token_path.chmod(0o600)
    identity = runtime.operations.journal.state().identity
    settings = {
        "schemaVersion": 1,
        "origin": bridge.origin,
        "identity": identity_value(identity),
        "credentialFile": str(token_path),
        "retryRoot": str(client_folder / "retry"),
        "clientId": "synthetic-mcp",
        "writeSources": ["manual"],
        "acknowledgeAiEgress": True,
        "caFile": str(cert),
    }
    settings_path = client_folder / "adapter.json"
    settings_path.write_text(json.dumps(settings))
    settings_path.chmod(0o600)
    thread = None
    try:
        with server(folder, workspace=root) as (_, uds):
            bridge.uds = uds
            thread = threading.Thread(
                target=bridge.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
            )
            thread.start()
            yield bridge, settings_path, runtime, owner, grant
    finally:
        bridge.release.set()
        if thread is not None:
            bridge.shutdown()
            thread.join(5)
            assert not thread.is_alive()
        bridge.server_close()


@contextmanager
def defer_phase_signals(enabled):
    """Deliver phase cancellation only after bounded owned-resource cleanup.

    The packaged helper calls this on its main thread. Temporary handlers also
    defer process-directed signals delivered via another thread; blocking only
    the main thread's POSIX signal mask would not establish that guarantee.
    Existing source SDK tests leave this disabled by default.
    """
    if not enabled:
        yield
        return
    signals = (signal.SIGTERM, signal.SIGALRM)
    previous = {number: signal.getsignal(number) for number in signals}
    pending = set()

    def defer(number, frame):
        pending.add(number)

    for number in signals:
        signal.signal(number, defer)
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        # Both represent phase cancellation. Deliver once after mandatory reap;
        # the original helper handler raises the same safe timeout exception.
        if pending:
            signal.raise_signal(next(number for number in signals if number in pending))


class ExistingBridge(Bridge):
    """One owned TLS connection at a time, with cancellable bounded handshake."""

    handshake_timeout = 3

    def __init__(self, address):
        super().__init__(address, Proxy, bind_and_activate=False)
        self.stop_requested = threading.Event()
        self.accepted = threading.Event()
        self.connection_lock = threading.Lock()
        self.active_connection = None
        self.tls_context = None

    def get_request(self):
        connection, address = self.socket.accept()
        try:
            # Accepted TCP sockets do not inherit the listening socket timeout.
            # Never perform a blocking TLS handshake in the listener's accept().
            connection.settimeout(self.handshake_timeout)
            with self.connection_lock:
                connection = self.tls_context.wrap_socket(
                    connection, server_side=True, do_handshake_on_connect=False
                )
                self.active_connection = connection
            self.accepted.set()
            connection.do_handshake()
            return connection, address
        except BaseException:
            connection.close()
            with self.connection_lock:
                if self.active_connection is connection:
                    self.active_connection = None
            if self.stop_requested.is_set():
                # Owner shutdown can race the next SSL operation after accept.
                # BaseServer handles an OSError here as an abandoned connection.
                raise OSError("owned_bridge_stopped") from None
            raise

    def shutdown_request(self, request):
        try:
            super().shutdown_request(request)
        finally:
            with self.connection_lock:
                if self.active_connection is request:
                    self.active_connection = None

    def serve_owned(self):
        # handle_request's listener wait is bounded by Bridge.timeout (0.1s).
        # No blocking BaseServer.shutdown() precedes the owner's timed join.
        while not self.stop_requested.is_set():
            self.handle_request()

    def stop_owned(self):
        self.stop_requested.set()
        with self.connection_lock:
            if self.active_connection is not None:
                try:
                    self.active_connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


@contextmanager
def existing_backend(uds, listener_fd, certificate_files):
    """TLS fixture over an already-running packaged UDS; no source backend.

    The caller retains the original listening descriptor to preserve its exact
    origin between sessions. This context closes only its duplicated socket.
    """
    owned = socket.fromfd(listener_fd, socket.AF_INET, socket.SOCK_STREAM)
    bridge = None
    thread = None
    try:
        host, port = owned.getsockname()
        assert host == "127.0.0.1" and 0 < port < 65536
        assert owned.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
        bridge = ExistingBridge((host, port))
        bridge.socket.close()
        bridge.socket = owned
        bridge.server_address = (host, port)
        bridge.origin = f"https://127.0.0.1:{port}"
        bridge.uds = uds
        bridge.responses, bridge.seen = [], []
        bridge.held, bridge.release = threading.Event(), threading.Event()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(*certificate_files)
        bridge.tls_context = context
        owned.settimeout(0.1)
        thread = threading.Thread(target=bridge.serve_owned, daemon=True)
        thread.start()
        yield bridge
    finally:
        with defer_phase_signals(True):
            if bridge is not None:
                bridge.release.set()
                bridge.stop_owned()
                try:
                    if thread is not None:
                        thread.join(5)
                        assert not thread.is_alive(), "Packaged bridge cleanup timeout"
                finally:
                    bridge.server_close()
            else:
                owned.close()


class Wire:
    def __init__(self, process, modern=False):
        self.process, self.modern = process, modern
        self.next_id, self.buffer = 0, bytearray()

    def send(self, value):
        self.process.stdin.write(json.dumps(value).encode() + b"\n")
        self.process.stdin.flush()

    def receive(self, *, timeout=45):
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while b"\n" not in self.buffer:
                if not selector.select(max(0, deadline - time.monotonic())):
                    raise AssertionError("MCP response timeout")
                part = os.read(self.process.stdout.fileno(), 8192)
                assert part, "MCP process exited without a complete response"
                self.buffer.extend(part)
                assert len(self.buffer) <= 160 * 1024
            line, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            return json.loads(line)

    def call(self, method, params=None):
        self.next_id += 1
        value = dict(params or {})
        if self.modern:
            value["_meta"] = {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientInfo": {
                    "name": "synthetic-test",
                    "version": "1",
                },
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        self.send(
            {"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": value}
        )
        reply = self.receive()
        assert reply["id"] == self.next_id
        return reply

    def tool(self, name, arguments):
        response = self.call("tools/call", {"name": name, "arguments": arguments})
        assert "result" in response, "Tool unexpectedly returned a protocol error"
        result = response["result"]
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
        return result["structuredContent"]


@contextmanager
def client(
    settings,
    folder,
    *,
    modern=False,
    instrumented=False,
    shutdown_timeout=55,
    protect_cleanup=False,
    launch=None,
):
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT),
        "HTTP_PROXY": "http://127.0.0.1:1",
        "HTTPS_PROXY": "http://127.0.0.1:1",
        "ALL_PROXY": "http://127.0.0.1:1",
        "OTEL_PYTHON_TRACER_PROVIDER": "synthetic-must-not-load",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:1",
    }
    module = "tests.mcp_process_runner" if instrumented else "health_buddy.mcp_server"
    command = [sys.executable, "-m", module, "--settings", str(settings)]
    cwd = ROOT
    if launch is not None:
        # Only the integration test supplies its owned helper-generated config.
        command = [launch["command"], *launch["args"]]
        environment.update(launch["env"])
        cwd = launch.get("cwd", ROOT)
    stderr = folder / "mcp-stderr.log"
    with stderr.open("ab") as log:
        # Fixed module and exclusively owned synthetic paths, bounded by queue cgroup.
        process = subprocess.Popen(  # noqa: S603
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            start_new_session=True,
        )
        wire = Wire(process, modern)
        try:
            if not modern:
                reply = wire.call(
                    "initialize",
                    {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "synthetic-test", "version": "1"},
                    },
                )
                assert reply["result"]["protocolVersion"] == "2025-11-25"
                wire.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            yield wire
        finally:
            with defer_phase_signals(protect_cleanup):
                try:
                    if process.stdin is not None and not process.stdin.closed:
                        try:
                            process.stdin.close()
                        except BrokenPipeError:
                            pass
                    try:
                        process.wait(timeout=shutdown_timeout)
                    except subprocess.TimeoutExpired:
                        # Only this still-live, privately owned session is signalled.
                        if process.returncode is None:
                            os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                finally:
                    if process.stdout is not None:
                        process.stdout.close()
    assert stderr.read_bytes() == b"", "Adapter emitted unexpected diagnostics"
