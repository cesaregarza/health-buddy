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
        ("externalOrigin", "https://example.invalid:443"),
        ("externalOrigin", "https://EXAMPLE.invalid"),
        ("externalOrigin", "https://example.invalid:0443"),
        ("externalOrigin", "https://example.invalid:0"),
        ("externalOrigin", "https://example.invalid:65536"),
        ("externalOrigin", "https://example.invalid:"),
        ("externalOrigin", "https://bad host.invalid"),
        ("externalOrigin", "https://bad\\host.invalid"),
        ("externalOrigin", "https://bad_host.invalid"),
        ("externalOrigin", "https://-bad.invalid"),
        ("externalOrigin", "https://example.invalid."),
        ("externalOrigin", "https://127.1"),
        ("externalOrigin", "https://2130706433"),
        ("externalOrigin", "https://0x7f000001"),
        ("externalOrigin", "https://example.invalid\x7f"),
        ("externalOrigin", "https://[2001:0db8::1]"),
        ("externalOrigin", "https://[fe80::1%25eth0]"),
        ("ownerSubject", " owner@example.invalid"),
        ("ownerSubject", "=?utf-8?B?synthetic?="),
        ("ownerSubject", []),
        ("ownerSubject", "synthetic\x7f@example.invalid"),
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


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.invalid",
        "https://example.invalid:8443",
        "https://127.0.0.1",
        "https://[2001:db8::1]:8443",
    ],
)
def test_canonical_origin_retains_exact_browser_origin(tmp_path, origin):
    settings = defaults()
    settings["security"]["externalOrigin"] = origin
    assert validate(settings, tmp_path).ingress().external_origin == origin
