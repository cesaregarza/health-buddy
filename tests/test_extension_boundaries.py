"""Actual child failure bounds and real-authority HTTP extension admission."""

import json
import time

import pytest

from health_buddy.core.security_api import AgentGrant
from health_buddy.core.service_api import ServiceError
from health_buddy.extension import runner as extension_runner
from health_buddy.extension.registry import Registry
from tests.extension_fixtures import example
from tests.security_fixtures import action, secured
from tests.test_transport_auth_wire import request, server
from tests.test_transport_auth_wire import short_directory as short_directory


@pytest.mark.parametrize(
    "source, code",
    [
        (
            "import time\ndef calculate(value):\n    time.sleep(30)\n",
            "extension_timeout",
        ),
        (
            "def calculate(value):\n    return 'x' * 70000\n",
            "extension_execution_failed",
        ),
        (
            "def calculate(value):\n"
            "    raise RuntimeError('synthetic hidden detail')\n",
            "extension_execution_failed",
        ),
    ],
)
def test_real_child_timeout_output_and_exception_leave_no_running_worker(
    tmp_path, monkeypatch, source, code
):
    path = tmp_path / "metric.py"
    path.write_text(source)
    processes = []
    popen = extension_runner.subprocess.Popen

    def capture(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(extension_runner.subprocess, "Popen", capture)
    started = time.monotonic()
    with pytest.raises(ServiceError) as failure:
        extension_runner.call("metric.py:calculate", tmp_path, {})
    assert failure.value.code == code
    assert "synthetic hidden detail" not in str(failure.value)
    assert time.monotonic() - started < 8
    assert len(processes) == 1 and processes[0].poll() is not None
    assert not (tmp_path / "__pycache__").exists()


def test_child_does_not_inherit_ambient_credentials(tmp_path, monkeypatch):
    (tmp_path / "metric.py").write_text(
        "import os\ndef calculate(value):\n"
        "    return {'ambient': os.environ.get('SYNTHETIC_EXTENSION_TOKEN')}\n"
    )
    monkeypatch.setenv("SYNTHETIC_EXTENSION_TOKEN", "fabricated-ambient-secret")
    assert extension_runner.call("metric.py:calculate", tmp_path, {}) == {
        "ambient": None
    }


def test_real_http_authority_filters_views_and_has_no_code_activation(short_directory):
    root = short_directory / "w"
    runtime, owner, token = secured(root, proxy=True)
    config = runtime.operations.config
    metric = "local.weekly-mass"
    example(config, metric)
    selected = Registry(config).enable(metric, source_ids=("manual",))
    allowed = action(
        runtime,
        owner,
        "grants.create",
        payload=AgentGrant(
            "Synthetic allowed view",
            ("records:read",),
            read_sources=("manual",),
            read_kinds=None,
            read_fields=None,
        ),
    )
    restricted = action(
        runtime,
        owner,
        "grants.create",
        payload=AgentGrant(
            "Synthetic restricted view",
            ("records:read",),
            read_sources=(),
            read_kinds=None,
            read_fields=None,
        ),
    )
    baseline = config.path("personal/extension-registry.json").read_bytes()
    revision = runtime.operations.journal.state().revision
    base = {"X-Forwarded-Host": "synthetic.example.invalid"}
    read_path = (
        f"/v1/extensions/{metric}?from=2030-01-01T00:00:00Z&to=2030-01-07T23:59:59Z"
    )
    asset_path = f"/v1/extensions/{metric}/view.js?review={selected.reviewed_digest}"
    with server(short_directory, workspace=root) as (_process, path):
        for route in ("/v1/extensions", read_path, asset_path, "/extension-worker.js"):
            assert request(path, target=route, headers=base)[0] == 401
        for credential, expected in (
            (token, 200),
            (allowed.secret.value, 200),
            (restricted.secret.value, 403),
        ):
            headers = {**base, "Authorization": "Bearer " + credential}
            status, raw, response_headers = request(
                path, target=read_path, headers=headers
            )
            assert status == expected
            assert response_headers["cache-control"] == "no-store"
            if status == 200:
                assert json.loads(raw)["data"]["metric"]["value"] is None
            assert request(path, target=asset_path, headers=headers)[0] == expected
            # Even the owner health credential cannot install or enable code.
            for command in ("install", "enable", "disable", "revert", "run"):
                denied, _, _ = request(
                    path,
                    "POST",
                    f"/v1/extensions/{metric}/{command}",
                    headers=headers,
                    body=b"{}",
                )
                assert denied in (404, 405)
        action(runtime, owner, "grants.revoke", resource=allowed.data["id"])
        headers = {**base, "Authorization": "Bearer " + allowed.secret.value}
        assert request(path, target=read_path, headers=headers)[0] == 401
        assert request(path, target=asset_path, headers=headers)[0] == 401
    assert config.path("personal/extension-registry.json").read_bytes() == baseline
    assert runtime.operations.journal.state().revision == revision
