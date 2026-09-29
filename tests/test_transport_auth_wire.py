"""Actual pinned Granian UDS/permission/lifecycle, synthetic authority only.

Queue must run inside its transient cgroup, including all child process groups.
A short queue-admitted HEALTH_BUDDY_TEST_SOCKET_ROOT contains only owned fixtures.
No installed Tailscale daemon is exercised or qualified here.
"""
from contextlib import contextmanager
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

import pytest

from health_buddy.transport_ingress import prepare_socket
from tests.auth_transport_fixtures import ORIGIN, TOKEN
from tests.transport_process import ROOT


class UnixHTTP(HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=3)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self.path))


def request(path, method="GET", target="/livez", *, headers=None, body=None):
    values = {"Host": "localhost", "X-Forwarded-Host": "synthetic.example", "X-Forwarded-Proto": "https"}
    values.update(headers or {})
    if body is not None:
        values["Content-Type"] = "application/json"
    connection = UnixHTTP(path)
    try:
        connection.request(method, target, body, values)
        result = connection.getresponse()
        return result.status, result.read(), {key.lower():value for key,value in result.getheaders()}
    finally:
        connection.close()


@contextmanager
def server(folder, *, ready=True, workspace=None):
    path = workspace / "security/http.sock" if workspace is not None else folder / "http.sock"
    args = [sys.executable, "-m", "tests.auth_transport_runner", "--socket", str(path)]
    if workspace is not None:
        args += ["--workspace", str(workspace)]
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src") + os.pathsep + str(ROOT), PYTHONUNBUFFERED="1")
    with (folder / "server.log").open("ab") as log:
        # Fixed owned synthetic module/paths; queue cgroup bounds descendants.
        process = subprocess.Popen(args, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)  # noqa: S603
        try:
            if ready:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise AssertionError("Synthetic UDS startup failed; inspect server.log")
                    try:
                        if request(path)[0] == 200:
                            break
                    except OSError:
                        pass
                    time.sleep(0.05)
                else:
                    raise AssertionError("Synthetic UDS readiness timeout")
            yield process, path
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass


@pytest.fixture
def short_directory(tmp_path):
    parent = os.environ.get("HEALTH_BUDDY_TEST_SOCKET_ROOT")
    if not parent:
        pytest.fail("Queue must supply its short admitted HEALTH_BUDDY_TEST_SOCKET_ROOT")
    with tempfile.TemporaryDirectory(prefix="u-", dir=parent) as value:
        folder = Path(value)
        folder.chmod(0o700)
        try:
            yield folder
        finally:
            # Preserve only synthetic diagnostics in the queue archive before
            # this fixture's exclusively owned short directory is removed.
            if (folder / "server.log").exists():
                (tmp_path / "auth-uds-server.log").write_bytes((folder / "server.log").read_bytes())
            (tmp_path / "auth-uds-cleanup.json").write_text(json.dumps({"ownedPath": str(folder), "cleanup": "fixture-finally"}))


def test_real_private_uds_graceful_restart_and_explicit_proxy_login(short_directory):
    folder = short_directory
    for _ in range(2):
        with server(folder) as (_process, path):
            assert path.stat().st_mode & 0o777 == 0o600
            assert folder.stat().st_mode & 0o777 == 0o700
            assert request(path, target="/v1/capabilities", headers={"Tailscale-User-Login": "owner@example.invalid"})[0] == 401
            headers = {"Tailscale-User-Login": "owner@example.invalid", "Origin": ORIGIN, "X-Health-Buddy-Browser": "1"}
            assert request(path, "POST", "/v1/sessions", headers=headers, body=b"{}")[0] == 200
            for altered in ({"Tailscale-User-Login": "wrong@example.invalid"}, {"Tailscale-Funnel-Request": "?1"}, {"X-Forwarded-Host": "wrong.invalid"}):
                assert request(path, "POST", "/v1/sessions", headers={**headers, **altered}, body=b"{}")[0] in (401, 403)
            # Tagged clients need no user header; explicit bearer remains usable.
            assert request(path, target="/v1/capabilities", headers={"Authorization": "Bearer " + TOKEN})[0] == 200
        assert not path.exists()


def test_untrusted_uid_cannot_connect_private_socket(short_directory):
    if os.geteuid() != 0:
        pytest.skip("Queue must provide root solely to launch a distinct unprivileged fixture UID")
    with server(short_directory) as (_process, path):
        source = "import socket,sys\ns=socket.socket(socket.AF_UNIX)\ntry:\n s.connect(sys.argv[1])\nexcept PermissionError:\n sys.exit(0)\nelse:\n sys.exit(1)\n"
        # Fixed system Python and owned socket; privileges only decrease.
        result = subprocess.run(["/usr/bin/python3", "-c", source, str(path)], user=65534, group=65534, extra_groups=[], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False)  # noqa: S603
        assert result.returncode == 0, "Unprivileged UID unexpectedly connected or fixture failed"


def test_shutdown_preserves_replacement_and_hardkill_stale_refuses(short_directory):
    with server(short_directory) as (_process, path):
        path.unlink()
        replacement = socket.socket(socket.AF_UNIX)
        replacement.bind(str(path))
        os.chmod(path, 0o600)
        replacement.listen(1)
        inode = path.stat().st_ino
    try:
        assert path.exists() and path.stat().st_ino == inode
        with pytest.raises(ValueError, match="already_exists"):
            prepare_socket(path)
    finally:
        replacement.close()
        path.unlink()  # This fixture created and owns the replacement path.
    with server(short_directory) as (process, path):
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)
    assert path.exists()
    with server(short_directory, ready=False) as (process, _path):
        assert process.wait(timeout=10) != 0
    assert path.exists()


def test_actual_authority_canonical_write_revoke_and_phone_role(short_directory):
    """Actual security SQLite + canonical service over the maintained UDS."""
    from datetime import UTC, datetime
    from uuid import uuid4
    from tests.security_fixtures import action, secured

    workspace = short_directory / "w"
    runtime, owner, owner_token = secured(workspace, receiver=True, proxy=True)
    origin = "https://synthetic.example.invalid"
    identity = runtime.operations.journal.state().identity
    base = {"X-Forwarded-Host":"synthetic.example.invalid"}
    tuple_headers = {"X-Installation-ID":identity.installation_id,"X-Dataset-ID":identity.dataset_id,"X-Restore-Epoch":identity.restore_epoch}
    bearer = {**base, **tuple_headers, "Authorization":"Bearer "+owner_token}

    with server(short_directory, workspace=workspace) as (_process, path):
        # The fixture is the trusted UDS caller, not a live Tailscale daemon.
        for route in ("/", "/icon.svg", "/api/context/pack", "/v1/records"):
            assert request(path, target=route, headers=base)[0] == 401
        login = {**base,"Origin":origin,"X-Health-Buddy-Browser":"1","Tailscale-User-Login":"synthetic-owner@example.invalid"}
        status, _body, headers = request(path,"POST","/v1/sessions",headers=login,body=b"{}")
        assert status == 201
        cookie = headers["set-cookie"].split(";")[0]
        session = {**base,"Cookie":cookie}
        status, body, _ = request(path,target="/v1/session",headers=session)
        assert status == 200
        csrf = json.loads(body)["secret"]["value"]
        status, body, _ = request(path,target="/v1/capabilities",headers=bearer)
        meta = json.loads(body)["meta"]
        write_headers = {**session, **tuple_headers, "Origin":origin,"X-CSRF-Token":csrf,"If-Match":f'"rev-{meta["dataRevision"]}"',"Idempotency-Key":uuid4().hex}
        payload = json.dumps({"sourceId":"manual","fields":{"eventAtLocal":datetime.now(UTC).isoformat(timespec="seconds"),"timezone":"UTC","itemName":"Fabricated test oats","caloriesKcal":123,"status":"consumed","category":"meal","source":"synthetic-entry"}}).encode()
        assert request(path,"POST","/v1/logs/intake",headers=write_headers,body=payload)[0] == 200
        no_csrf = {key:value for key,value in write_headers.items() if key!="X-CSRF-Token"}
        assert request(path,"POST","/v1/logs/intake",headers=no_csrf,body=payload)[0] == 403
        assert request(path,"POST","/v1/logs/intake",headers={**write_headers,"X-CSRF-Token":"wrong"},body=payload)[0] == 403
        assert request(path,"POST","/v1/logs/intake",headers=write_headers,body=payload)[0] == 200

        grant = {"name":"Fabricated agent","grants":["records:read","records:write"],"sourceIds":["manual"],"readSources":["manual"],"readKinds":None,"readFields":None}
        status, raw, _ = request(path,"POST","/v1/grants",headers=bearer,body=json.dumps(grant).encode())
        assert status == 201
        created = json.loads(raw)
        agent_token = created["secret"]["value"]
        agent = {**base, **tuple_headers, "Authorization":"Bearer "+agent_token}
        status, raw, _ = request(path,target="/v1/capabilities",headers=agent)
        revision = json.loads(raw)["meta"]["dataRevision"]
        pending = {**agent,"If-Match":f'"rev-{revision}"',"Idempotency-Key":uuid4().hex}
        status, first, _ = request(path,"POST","/v1/logs/intake",headers=pending,body=payload)
        assert status == 200
        assert request(path,"POST","/v1/logs/intake",headers=pending,body=payload)[1] == first
        action(runtime, owner, "grants.revoke", resource=created["data"]["id"])
        assert request(path,"POST","/v1/logs/intake",headers=pending,body=payload)[0] == 401

        status, raw, _ = request(path,"POST","/v1/pairing-intents",headers=bearer,body=b'{"name":"Fabricated phone"}')
        assert status == 201 and b"secret" not in raw
        intent = json.loads(raw)["data"]["id"]
        status, raw, _ = request(path,"POST",f"/v1/pairing-intents/{intent}/handoff",headers=bearer,body=b"{}")
        assert status == 201
        proof = json.loads(raw)["secret"]["value"]
        redeem = json.dumps({"proof":proof,"deviceId":str(uuid4()),"protocolVersion":1}).encode()
        status, raw, _ = request(path,"POST","/v1/pairings",headers={**base, **tuple_headers},body=redeem)
        assert status == 201
        phone = json.loads(raw)
        device_token = phone["secret"]["value"]
        device = {**base,"Authorization":"Bearer "+device_token}
        assert request(path,target="/v1/records",headers=device)[0] == 403
        assert request(path,target="/v1/devices",headers=device)[0] == 403
        status, raw, _ = request(path,target="/v1/session",headers=device)
        assert status == 200 and b"secret" not in raw
        assert request(path,"POST","/v1/pairings",headers={**base, **tuple_headers},body=redeem)[0] == 409
        action(runtime, owner, "devices.revoke", resource=phone["data"]["id"])
        assert request(path,target="/v1/capabilities",headers=device)[0] == 401
        # Secret values never enter immutable health receipt storage.
        ledger = (workspace / "operations/control.sqlite").read_bytes()
        for value in (owner_token, agent_token, device_token, proof, csrf):
            assert value.encode() not in ledger
