"""Synthetic operator failures and support-summary privacy boundaries."""

import json
from importlib.resources import files
from pathlib import Path
import socket

import pytest
from types import SimpleNamespace
from unittest.mock import patch

from health_buddy.app import App
from health_buddy.cli import main
from health_buddy.operator_diagnostics import report, source_findings, support_summary


def codes(value):
    return {item["code"] for item in value["diagnostics"]}


def test_doctor_invalid_config_is_actionable_json(tmp_path, capsys):
    root = tmp_path / "owner"
    root.mkdir()
    (root / "config.json").write_text('{"token":"synthetic-secret"}')
    assert main(["--workspace", str(root), "doctor", "--json"]) == 2
    output = capsys.readouterr().out
    assert "synthetic-secret" not in output
    assert "config_invalid" in codes(json.loads(output))


def test_status_core_without_site_or_agent(tmp_path, capsys):
    app = App.development(tmp_path / "owner")
    assert main(["--workspace", str(app.config.root), "--development", "status", "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["sources"]["sources"]["manual"]["availability"] == "empty"
    assert value["queue"]["state"] == "empty"
    assert value["connectivity"]["phone"] == "unknown"
    assert "phone_unknown" in codes(value)


def test_distinct_empty_stale_failed_and_fresh_empty():
    def projection(availability, freshness):
        return {"sources": {"phone": {"availability": availability, "freshness": freshness}}}

    assert source_findings(projection("empty", "fresh"))[0]["code"] == "source_empty"
    assert source_findings(projection("available", "stale"))[0]["code"] == "source_stale"
    assert source_findings(projection("unavailable", "unknown"))[0]["code"] == "source_failed"
    assert not source_findings(projection("disabled", "unknown"))


def test_low_disk_port_conflict_and_partial_permissions(tmp_path):
    app = App.development(tmp_path / "owner")
    app.config.storage("manual").chmod(0o755)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        with patch("health_buddy.operator_diagnostics.shutil.disk_usage", return_value=SimpleNamespace(free=100)):
            value = report(app.config.root, port=listener.getsockname()[1])
    assert {"disk_low", "port_conflict", "permissions_partial"} <= codes(value)


def test_support_bundle_excludes_tokens_and_personal_records():
    private = "Bearer synthetic-token body-mass=123.456 owner@example.test"
    value = {
        "installation": {"packageVersion": private},
        "sources": {"private-source": {"record": private}},
        "queue": {"payload": private},
        "extensions": {"items": [{"id": private, "state": "needs_review", "diagnostics": [private]}]},
        "diagnostics": [{"code": "source_stale", "recovery": private}, {"code": private}],
    }
    raw = json.dumps(support_summary(value))
    assert private not in raw
    for forbidden in ("synthetic-token", "123.456", "owner@example.test", "private-source"):
        assert forbidden not in raw
    assert "source_stale" in raw and "needs_review" in raw


def test_failed_worker_has_safe_diagnostic_and_preserves_personal_files(tmp_path):
    from health_buddy.extension_diagnostics import observed_call, recent_failure
    from health_buddy.extension_install import install
    from health_buddy.service_api import ServiceError

    app = App.development(tmp_path / "owner")
    install(app.config, Path(str(files("health_buddy").joinpath("reference_extensions/local.weekly-mass"))))
    private = "Bearer synthetic-failure-token body-mass=123.456"
    before = (app.config.root / "personal/extensions/local.weekly-mass/src/metric.py").read_bytes()
    with patch("health_buddy.extension_diagnostics.call", side_effect=ServiceError(503, "extension_timeout")):
        with pytest.raises(ServiceError, match="extension_timeout"):
            observed_call(app.config, "local.weekly-mass", "src/metric.py:compute", app.config.root, {"health": private})
    value = report(app.config.root)
    raw = json.dumps(value)
    assert private not in raw
    assert "extension_unavailable" in codes(value)
    assert recent_failure(app.config, "local.weekly-mass")["code"] == "extension_timeout"
    from health_buddy.extension_registry import Registry

    Registry(app.config).disable("local.weekly-mass")
    assert before == (app.config.root / "personal/extensions/local.weekly-mass/src/metric.py").read_bytes()


def test_missing_worker_record_is_unknown(tmp_path):
    from health_buddy.extension_diagnostics import recent_failure

    app = App.development(tmp_path / "owner")
    assert recent_failure(app.config, "local.weekly-mass")["state"] == "unknown"


def test_receiver_failure_and_private_phone_unknown(tmp_path):
    app = App.development(tmp_path / "owner")
    path = app.config.root / "config.json"
    config = json.loads(path.read_text())
    config["security"].update({"ingress": "tailscale-uds", "externalOrigin": "https://synthetic.example.test", "ownerSubject": "synthetic@example.test"})
    path.write_text(json.dumps(config))
    value = report(app.config.root)
    assert "receiver_unreachable" in codes(value)
    assert value["connectivity"]["phone"] == "unknown"


def test_partial_application_permissions_have_distinct_code(tmp_path):
    from health_buddy.service_api import ServiceError

    app = App.development(tmp_path / "owner")
    with patch.object(app, "_read", side_effect=ServiceError(403, "forbidden")):
        value = report(app.config.root, app=app)
    assert "authorization_partial" in codes(value)
    assert value["sources"] is None
