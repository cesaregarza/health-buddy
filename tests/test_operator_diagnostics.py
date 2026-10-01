"""Synthetic operator failures and support-summary privacy boundaries."""

import json
import socket
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from health_buddy.client.app import App
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
    assert (
        main(["--workspace", str(app.config.root), "--development", "status", "--json"])
        == 0
    )
    value = json.loads(capsys.readouterr().out)
    assert value["sources"]["sources"]["manual"]["availability"] == "empty"
    assert value["queue"]["state"] == "empty"
    assert value["connectivity"]["phone"] == "unknown"
    assert "phone_unknown" in codes(value)


def test_distinct_empty_stale_failed_and_fresh_empty():
    def projection(availability, freshness):
        return {
            "sources": {"phone": {"availability": availability, "freshness": freshness}}
        }

    assert source_findings(projection("empty", "fresh"))[0]["code"] == "source_empty"
    assert (
        source_findings(projection("available", "stale"))[0]["code"] == "source_stale"
    )
    assert (
        source_findings(projection("unavailable", "unknown"))[0]["code"]
        == "source_failed"
    )
    assert not source_findings(projection("disabled", "unknown"))


def test_low_disk_port_conflict_and_partial_permissions(tmp_path):
    app = App.development(tmp_path / "owner")
    app.config.storage("manual").chmod(0o755)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        with patch(
            "health_buddy.operator_diagnostics.shutil.disk_usage",
            return_value=SimpleNamespace(free=100),
        ):
            value = report(app.config.root, port=listener.getsockname()[1])
    assert {"disk_low", "port_conflict", "permissions_partial"} <= codes(value)


def test_support_bundle_excludes_tokens_and_personal_records():
    private = "Bearer synthetic-token body-mass=123.456 owner@example.test"
    value = {
        "installation": {"packageVersion": private},
        "sources": {"private-source": {"record": private}},
        "queue": {"payload": private},
        "extensions": {
            "items": [
                {"id": private, "state": "needs_review", "diagnostics": [private]}
            ]
        },
        "diagnostics": [
            {"code": "source_stale", "recovery": private},
            {"code": private},
        ],
    }
    raw = json.dumps(support_summary(value))
    assert private not in raw
    for forbidden in (
        "synthetic-token",
        "123.456",
        "owner@example.test",
        "private-source",
    ):
        assert forbidden not in raw
    assert "source_stale" in raw and "needs_review" in raw


def test_failed_worker_has_safe_diagnostic_and_preserves_personal_files(tmp_path):
    from health_buddy.extension.diagnostics import observed_call, recent_failure
    from health_buddy.extension.install import install
    from health_buddy.core.service_api import ServiceError

    app = App.development(tmp_path / "owner")
    install(
        app.config,
        Path(
            str(
                files("health_buddy").joinpath("reference_extensions/local.weekly-mass")
            )
        ),
    )
    sentinel = (
        app.config.root
        / "personal/extensions/local.weekly-mass/state/operator-failure.json"
    )
    sentinel.write_text("extension-owned sentinel")
    sentinel.chmod(0o600)
    private = "Bearer synthetic-failure-token body-mass=123.456"
    before = (
        app.config.root / "personal/extensions/local.weekly-mass/src/metric.py"
    ).read_bytes()
    with patch(
        "health_buddy.extension.diagnostics.call",
        side_effect=ServiceError(503, "extension_timeout"),
    ):
        with pytest.raises(ServiceError, match="extension_timeout"):
            observed_call(
                app.config,
                "local.weekly-mass",
                "src/metric.py:compute",
                app.config.root,
                {"health": private},
            )
    value = report(app.config.root)
    raw = json.dumps(value)
    assert private not in raw
    assert sentinel.read_text() == "extension-owned sentinel"
    assert "extension_unavailable" in codes(value)
    assert (
        recent_failure(app.config, "local.weekly-mass")["code"] == "extension_timeout"
    )
    from health_buddy.extension.registry import Registry

    Registry(app.config).disable("local.weekly-mass")
    assert (
        before
        == (
            app.config.root / "personal/extensions/local.weekly-mass/src/metric.py"
        ).read_bytes()
    )


def test_missing_worker_record_is_unknown(tmp_path):
    from health_buddy.extension.diagnostics import recent_failure

    app = App.development(tmp_path / "owner")
    assert recent_failure(app.config, "local.weekly-mass")["state"] == "unknown"


def test_receiver_failure_and_private_phone_unknown(tmp_path):
    app = App.development(tmp_path / "owner")
    path = app.config.root / "config.json"
    config = json.loads(path.read_text())
    config["security"].update(
        {
            "ingress": "tailscale-uds",
            "externalOrigin": "https://synthetic.example.test",
            "ownerSubject": "synthetic@example.test",
        }
    )
    path.write_text(json.dumps(config))
    value = report(app.config.root)
    assert "receiver_unreachable" in codes(value)
    assert value["connectivity"]["phone"] == "unknown"


def test_partial_application_permissions_have_distinct_code(tmp_path):
    from health_buddy.core.service_api import ServiceError

    app = App.development(tmp_path / "owner")
    with patch.object(app, "_read", side_effect=ServiceError(403, "forbidden")):
        value = report(app.config.root, app=app)
    assert "authorization_partial" in codes(value)
    assert value["sources"] is None


def test_failed_source_is_error_and_status_returns_nonzero(tmp_path, capsys):
    app = App.development(tmp_path / "owner")
    path = app.config.root / "config.json"
    settings = json.loads(path.read_text())
    settings["integrations"]["healthkit"]["enabled"] = True
    path.write_text(json.dumps(settings))
    source = {
        "state": "partial",
        "sources": {
            "healthkit": {
                "availability": "unavailable",
                "freshness": "unknown",
                "missingness": "source_error",
            }
        },
    }
    original = App._read

    def read(instance, operation, **query):
        if operation == "projection.status":
            return source
        return original(instance, operation, **query)

    with patch.object(App, "_read", read):
        assert (
            main(
                [
                    "--workspace",
                    str(app.config.root),
                    "--development",
                    "status",
                    "--json",
                ]
            )
            == 2
        )
    value = json.loads(capsys.readouterr().out)
    assert {"code": "source_failed", "severity": "error"}.items() <= next(
        item for item in value["diagnostics"] if item["code"] == "source_failed"
    ).items()


def test_source_runtime_version_without_installed_product_wheel(tmp_path):
    from importlib.metadata import PackageNotFoundError

    from health_buddy.core.release_identity import ReleaseIdentity

    app = App.development(tmp_path / "owner")
    with (
        patch(
            "health_buddy.operator_diagnostics.version",
            side_effect=PackageNotFoundError,
        ),
        patch(
            "health_buddy.operator_diagnostics.read_source_identity",
            return_value=ReleaseIdentity(
                package_version="0.1.0.dev0", source_evidence="packaged_manifest"
            ),
        ),
    ):
        value = report(app.config.root)
    assert value["installation"]["packageVersion"] == "0.1.0.dev0"
    assert value["installation"]["versionEvidence"] == "packaged_manifest"
    assert value["installation"]["releaseArtifact"] is None
