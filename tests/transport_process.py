"""Bounded queue-owned subprocess fixture for the actual production launcher."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from http.client import HTTPConnection
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RunningServer:
    server_port: int
    pid: int

    @property
    def server_address(self):
        return ("127.0.0.1", self.server_port)

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.server_port}"


def request(http, method, path, body=None, headers=None, timeout=15):
    connection = HTTPConnection("127.0.0.1", http.server_port, timeout=timeout)
    values = dict(headers or {})
    if method in ("POST", "PUT"):
        values.setdefault("Origin", http.origin)
        values.setdefault("Content-Type", "application/json")
    try:
        connection.request(method, path, body=body, headers=values)
        response = connection.getresponse()
        return response.status, response.read(), {key.lower(): value for key, value in response.getheaders()}
    finally:
        connection.close()


@contextmanager
def running(folder, *, workspace=None, development=True):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    args = [sys.executable, "-m", "tests.transport_runner", "--port", str(port)]
    if workspace is not None:
        args += ["--workspace", str(workspace)]
    else:
        args += ["--evidence", str(folder / "factory.json")]
    if development:
        args += ["--development"]
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src") + os.pathsep + str(ROOT), PYTHONUNBUFFERED="1")
    with (folder / "server.log").open("wb") as log:
        process = subprocess.Popen(
            args, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, start_new_session=True,
        )
        http = RunningServer(port, process.pid)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError("Production launcher exited; inspect synthetic server.log")
                try:
                    if request(http, "GET", "/livez", timeout=0.3)[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(0.05)
            else:
                raise AssertionError("Production launcher readiness exceeded 15 seconds")
            yield http
        finally:
            # The fixture owns only this new process group, including Granian's
            # one child. Never leave a worker running after an assertion fails.
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
