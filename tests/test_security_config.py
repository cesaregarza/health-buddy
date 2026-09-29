"""Synthetic configuration boundaries; runtime security tests follow integration."""

import pytest

from health_buddy.config import ConfigError, defaults, validate


def test_existing_configuration_adds_denying_loopback_without_rewrite(tmp_path):
    original = defaults()
    original.pop("security")
    config = validate(original, tmp_path)
    assert "security" not in original
    assert config.ingress().mode == "loopback"
    assert config.ingress().external_origin is None
    assert "security" not in config.public()


def test_private_uds_settings_are_explicit_and_not_public(tmp_path):
    value = defaults()
    value["security"].update(
        ingress="tailscale-uds",
        externalOrigin="https://health.example.invalid",
        ownerSubject="owner@example.invalid",
    )
    config = validate(value, tmp_path)
    assert config.ingress().socket_path == str(tmp_path / "security/http.sock")
    assert config.ingress().owner_subject == "owner@example.invalid"
    assert "ownerSubject" not in repr(config.public())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ingress", []),
        ("ingress", "trusted-loopback"),
        ("externalOrigin", "http://example.invalid"),
        ("externalOrigin", "https://:synthetic@example.invalid"),
        ("externalOrigin", "https://example.invalid/path"),
        ("externalOrigin", "https://[broken"),
        ("ownerSubject", " owner@example.invalid"),
        ("ownerSubject", "=?utf-8?B?synthetic?="),
        ("ownerSubject", []),
        ("socketPath", "stores/health.sock"),
        ("socketPath", "security/authority.sqlite"),
        ("sessionSeconds", True),
        ("sessionSeconds", 0),
    ],
)
def test_security_settings_reject_bad_shapes_without_values(tmp_path, field, value):
    settings = defaults()
    settings["security"][field] = value
    with pytest.raises(ConfigError):
        validate(settings, tmp_path)


def test_uds_requires_both_origin_and_exact_owner_subject(tmp_path):
    settings = defaults()
    settings["security"]["ingress"] = "tailscale-uds"
    with pytest.raises(ConfigError):
        validate(settings, tmp_path)
