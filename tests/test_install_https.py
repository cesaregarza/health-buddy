"""Pinned Serve JSON and scoped ownership; no Tailscale/Docker process."""

import json
import os
import stat
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from health_buddy import install_activation, install_https
from health_buddy.service_api import ServiceError
from tests.test_install_activation import fixture


class Reply:
    def __init__(self, value, *, lost=False, returncode=0):
        read, write = os.pipe()
        payload = json.dumps(value).encode() if value is not None else b""
        os.write(write, payload)
        os.close(write)
        self.stdout = os.fdopen(read, "rb", buffering=0)
        self.returncode = returncode
        self.lost = lost

    def wait(self, timeout=None):
        if self.lost and timeout is not None:
            self.lost = False
            raise subprocess.TimeoutExpired("synthetic CLI", 20)
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9


def serve_fixture(tmp_path, monkeypatch):
    activation, engine, selected, identity, note = fixture(tmp_path, monkeypatch, guided_owner=True)
    install_activation.activate(**activation)
    workspace = selected["workspace"]
    cli = tmp_path / "synthetic-tailscale"
    cli.write_bytes(b"synthetic native CLI never executed")
    cli.chmod(0o700)
    daemon = tmp_path / "synthetic-tailscaled.sock"
    api_socket = workspace / "security/runtime/http.sock"
    original = Path.lstat

    def metadata(path, *args, **kwargs):
        if path in (daemon, api_socket):
            return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=1000, st_gid=1000)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", metadata)
    # Existing owner fixture used synthetic.example.test as the origin. This
    # fixture tailnet intentionally reports that same synthetic DNS identity.
    hostname = "synthetic.example.test"
    hostport = hostname + ":443"
    version = {"majorMinorPatch": "1.102.5", "short": "1.102.5", "long": "1.102.5-t5fb2a81b0", "daemonLong": "1.102.5-t5fb2a81b0", "gitCommit": install_https.SOURCE, "cap": 139}
    status = {"Version": version["long"], "BackendState": "Running", "Self": {"DNSName": hostname + ".", "CapMap": {"https": []}}, "CurrentTailnet": {"MagicDNSSuffix": "example.test", "MagicDNSEnabled": True}, "CertDomains": [hostname], "AuthURL": "https://private.invalid/auth-never-print"}
    config = {"TCP": {"443": {"HTTPS": True}, "8443": {"HTTPS": True}}, "Web": {hostport: {"Handlers": {"/other": {"Text": "synthetic owner handler"}}}, hostname + ":8443": {"Handlers": {"/": {"Text": "other port"}}}}, "Services": {"svc:unrelated": {"TCP": {"3000": {"TCPForward": "127.0.0.1:3000"}}}}, "Foreground": {"owner-session": {"TCP": {"9000": {"TCPForward": "127.0.0.1:9000"}}}}}
    state = {"version": version, "status": status, "config": config, "lost": None, "permission": False, "after_edit": False, "calls": []}
    original_popen = subprocess.Popen

    def response(command, **kwargs):
        if command[0] != str(cli):
            return original_popen(command, **kwargs)
        assert command[1] == "--socket=" + str(daemon)
        assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["stderr"] == subprocess.DEVNULL
        state["calls"].append(command[2:])
        if state["permission"]:
            return Reply({"AuthURL": "never print"}, returncode=1)
        arguments = command[2:]
        if arguments == ["version", "--json", "--daemon"]:
            return Reply(state["version"])
        if arguments == ["status", "--json", "--peers=false"]:
            return Reply(state["status"])
        if arguments == ["serve", "status", "--json"]:
            return Reply(state["config"])
        assert arguments[:4] == ["serve", "--bg", "--https=443", "--set-path=/"]
        retained = json.loads(selected["journal"].read_bytes())
        expected = "removing" if arguments[4] == "off" else "setting"
        assert retained["privateHttps"]["phase"] == expected
        config = state["config"]
        if arguments[4] == "off":
            del config["Web"][hostport]["Handlers"]["/"]
            if not config["Web"][hostport]["Handlers"]:
                del config["Web"][hostport]
                del config["TCP"]["443"]
        else:
            assert arguments[4] == "unix:" + str(api_socket)
            config.setdefault("TCP", {})["443"] = {"HTTPS": True}
            config.setdefault("Web", {}).setdefault(hostport, {"Handlers": {}})["Handlers"]["/"] = {"Proxy": arguments[4]}
        if state["after_edit"]:
            config["Services"]["svc:unrelated"]["Tun"] = True
        lost = state["lost"] == ("remove" if arguments[4] == "off" else "setup")
        state["lost"] = None
        return Reply(None, lost=lost)

    monkeypatch.setattr(subprocess, "Popen", response)
    arguments = dict(journal=selected["journal"], tailscale=cli, daemon_socket=daemon, action="setup", confirm_local_tailscale=True, confirm_serve=True, confirm_quiesced=True)
    return arguments, state, selected, identity, note, engine


@pytest.mark.parametrize("lost", [None, "setup", "remove"])
def test_scoped_serve_replays_preserve_routes_and_personal_state(tmp_path, monkeypatch, lost):
    arguments, state, selected, _identity, note, _engine = serve_fixture(tmp_path, monkeypatch)
    original = deepcopy(state["config"])
    files = {path: path.read_bytes() for path in selected["workspace"].rglob("*") if path.is_file()}
    journal = selected["journal"].read_bytes()
    assert install_https.route(**{**arguments, "action": "dry-run"})["dryRun"]
    assert selected["journal"].read_bytes() == journal
    assert all(path.read_bytes() == payload for path, payload in files.items())
    state["lost"] = "setup" if lost == "setup" else None
    if lost == "setup":
        with pytest.raises(ServiceError, match="owner_permission_or_daemon_action"):
            install_https.route(**arguments)
    assert install_https.route(**arguments)["privateRouteConfigured"]
    assert install_https.route(**arguments)["privateRouteConfigured"]
    state["lost"] = "remove" if lost == "remove" else None
    if lost == "remove":
        with pytest.raises(ServiceError, match="owner_permission_or_daemon_action"):
            install_https.route(**{**arguments, "action": "remove"})
    assert not install_https.route(**{**arguments, "action": "remove"})["privateRouteConfigured"]
    assert not install_https.route(**{**arguments, "action": "remove"})["privateRouteConfigured"]
    mutations = [item for item in state["calls"] if item[:2] == ["serve", "--bg"]]
    assert len(mutations) == 2
    assert state["config"] == original
    assert all(path.read_bytes() == payload for path, payload in files.items())
    assert note.read_text() == "synthetic personal work retained"


@pytest.mark.parametrize("gate", ["version", "daemon", "login", "dns", "cert", "cap", "permission"])
def test_admission_gaps_do_not_mutate_or_print_auth_urls(tmp_path, monkeypatch, capsys, gate):
    arguments, state, selected, _identity, _note, _engine = serve_fixture(tmp_path, monkeypatch)
    if gate == "version":
        state["version"]["short"] = "1.100.0"
    elif gate == "daemon":
        state["version"]["daemonLong"] = "1.102.5-other"
    elif gate == "login":
        state["status"]["BackendState"] = "NeedsLogin"
    elif gate == "dns":
        state["status"]["Self"]["DNSName"] = "another.example.test."
    elif gate == "cert":
        state["status"]["CertDomains"] = []
    elif gate == "cap":
        state["status"]["Self"]["CapMap"] = {}
    else:
        state["permission"] = True
    before = selected["journal"].read_bytes()
    cli = ["--journal", str(arguments["journal"]), "--tailscale", str(arguments["tailscale"]), "--daemon-socket", str(arguments["daemon_socket"]), "--action", "setup", "--confirm-local-tailscale", "--confirm-serve", "--confirm-quiesced"]
    assert install_https.main(cli) == 2
    output = capsys.readouterr().out
    assert "AuthURL" not in output and "private.invalid" not in output and str(arguments["daemon_socket"]) not in output
    assert selected["journal"].read_bytes() == before
    assert not any(item[:2] == ["serve", "--bg"] for item in state["calls"])


@pytest.mark.parametrize("conflict", ["funnel", "tcp", "http", "foreground", "root"])
def test_conflicts_do_not_mutate_unowned_config(tmp_path, monkeypatch, conflict):
    arguments, state, selected, _identity, _note, _engine = serve_fixture(tmp_path, monkeypatch)
    hp = "synthetic.example.test:443"
    if conflict == "funnel":
        state["config"]["AllowFunnel"] = {hp: True}
    elif conflict == "tcp":
        state["config"]["TCP"]["443"] = {"TCPForward": "127.0.0.1:3000"}
    elif conflict == "http":
        state["config"]["TCP"]["443"] = {"HTTP": True}
    elif conflict == "foreground":
        state["config"]["Foreground"]["other"] = {"TCP": {"443": {"HTTPS": True}}}
    else:
        state["config"]["Web"][hp]["Handlers"]["/"] = {"Proxy": "unix:/other-owner.sock"}
    before = deepcopy(state["config"])
    with pytest.raises(ServiceError):
        install_https.route(**arguments)
    assert state["config"] == before and "privateHttps" not in json.loads(selected["journal"].read_bytes())
    assert not any(item[:2] == ["serve", "--bg"] for item in state["calls"])


def test_concurrent_edit_is_retained_and_never_restored(tmp_path, monkeypatch):
    arguments, state, selected, _identity, _note, _engine = serve_fixture(tmp_path, monkeypatch)
    state["after_edit"] = True
    with pytest.raises(ServiceError, match="unrelated_configuration_changed"):
        install_https.route(**arguments)
    assert state["config"]["Services"]["svc:unrelated"]["Tun"] is True
    assert json.loads(selected["journal"].read_bytes())["privateHttps"]["phase"] == "setting"
    count = len(state["calls"])
    with pytest.raises(ServiceError, match="unrelated_configuration_changed"):
        install_https.route(**arguments)
    assert not any(item[:2] == ["serve", "--bg"] for item in state["calls"][count:])
    assert not any("reset" in item or "set-raw" in item or "login" in item for item in state["calls"])


def test_cli_output_cap_refuses_oversized_stream(tmp_path, monkeypatch):
    arguments, _state, _selected, _identity, _note, _engine = serve_fixture(tmp_path, monkeypatch)
    stream = iter([b"x" * install_https.LIMIT, b"x"])
    monkeypatch.setattr(os, "read", lambda _fd, _size: next(stream))
    with pytest.raises(ServiceError, match="install_https_response_limit"):
        install_https.command(arguments["tailscale"], arguments["daemon_socket"], "status", "--json", "--peers=false")
