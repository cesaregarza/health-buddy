"""Actual admission and bounded metadata; no SDK or runtime artifact claim."""

import json
from dataclasses import replace

from health_buddy.discovery import source_identity
from health_buddy.extension_registry import Registry
from health_buddy.release_identity import ArtifactIdentity, ReleaseIdentity
from health_buddy.security_api import AgentGrant, BearerProof
from health_buddy.service_api import Request
from health_buddy.transport import ENDPOINTS
from tests.extension_fixtures import example
from tests.security_fixtures import action, secured


def discover(runtime, principal):
    return runtime.operations.execute(principal, Request("workspace.discover"))


def grant(
    runtime,
    owner,
    *,
    grants=("records:read",),
    sources=("manual",),
    kinds=None,
    fields=None,
):
    reply = action(
        runtime,
        owner,
        "grants.create",
        payload=AgentGrant(
            "Fabricated discovery client",
            grants,
            source_ids=("manual",),
            read_sources=sources,
            read_kinds=kinds,
            read_fields=fields,
        ),
    )
    admitted = runtime.security.authenticate(BearerProof(reply.secret.value))
    return admitted.principal, reply.data["id"]


def test_current_policy_filters_metadata_without_history_or_native_inventory(
    tmp_path, monkeypatch
):
    from health_buddy import personal_workspace, snapshots

    runtime, owner, _ = secured(tmp_path / "owner")
    config = runtime.operations.config
    example(config, "local.weekly-mass")
    selected = Registry(config).enable("local.weekly-mass", source_ids=("manual",))
    example(config, "local.water-import")
    Registry(config).enable("local.water-import", source_ids=("hidden-source",))

    def forbidden(*args, **kwargs):
        raise AssertionError("private inventory or health history was queried")

    monkeypatch.setattr(personal_workspace, "describe", forbidden)
    monkeypatch.setattr(snapshots, "capture", forbidden)
    principal, _ = grant(runtime, owner)
    response = discover(runtime, principal)
    assert response.status == 200 and len(response.body) <= 32768
    data = json.loads(response.body)["data"]
    assert [item["id"] for item in data["extensions"]] == ["local.weekly-mass"]
    assert data["extensions"][0]["reviewedDigest"] == selected.reviewed_digest
    assert data["ownerInventoryComplete"] is False
    assert data["extensionInventory"] == "authorized_ready_entries_only"
    assert data["source"]["sourceEvidence"] == "unknown"
    assert all(
        value is None
        for key, value in data["source"].items()
        if key != "sourceEvidence"
    )
    assert "workspace.discover" in data["availableOperations"]
    assert "context.intent" not in data["availableOperations"]
    for forbidden_text in (
        str(config.root),
        "hidden-source",
        "local.water-import",
        "secretReferences",
        "approvedEgress",
    ):
        assert forbidden_text.encode() not in response.body
    for restriction in (
        {"sources": ()},
        {"kinds": ("water-intake",)},
        {"fields": ("id",)},
    ):
        narrowed, _ = grant(runtime, owner, **restriction)
        result = discover(runtime, narrowed)
        assert (
            result.status == 200 and json.loads(result.body)["data"]["extensions"] == []
        )


def test_write_only_can_negotiate_but_cannot_discover_and_revocation_is_current(
    tmp_path,
):
    runtime, owner, _ = secured(tmp_path / "owner")
    write_only, _ = grant(runtime, owner, grants=("records:write",), sources=())
    capabilities = runtime.operations.execute(write_only, Request("capabilities"))
    assert capabilities.status == 200
    assert (
        "workspace.discover"
        not in json.loads(capabilities.body)["data"]["availableOperations"]
    )
    assert discover(runtime, write_only).status == 403
    admitted, identifier = grant(runtime, owner)
    assert discover(runtime, admitted).status == 200
    action(runtime, owner, "grants.revoke", resource=identifier)
    assert discover(runtime, admitted).status == 401
    assert discover(runtime, None).status == 401


def test_mixed_binding_discovery_does_not_disclose_hidden_sources_or_grant_reads(
    tmp_path,
):
    runtime, owner, _ = secured(tmp_path / "owner")
    config = runtime.operations.config
    name = "local.weekly-mass"
    example(config, name)
    registry = Registry(config)
    registry.enable(name, source_ids=("manual",))
    principal, _ = grant(runtime, owner, sources=("manual",))
    before = discover(runtime, principal)
    assert before.status == 200
    registry.enable(name, source_ids=("manual", "fabricated-hidden-source"))
    after = discover(runtime, principal)
    # Source selection is per metric invocation. The same visible descriptor
    # is useful for manual, without disclosing the other binding or its count.
    assert after.status == 200 and after.body == before.body
    assert json.loads(after.body)["data"]["extensions"][0]["id"] == name
    assert b"fabricated-hidden-source" not in after.body
    query = {"from": "2030-01-01T00:00:00Z", "to": "2030-01-07T23:59:59Z"}
    allowed = runtime.operations.execute(
        principal,
        Request(
            "extensions.read",
            resource_id=name,
            query={**query, "sourceId": "manual"},
        ),
    )
    denied = runtime.operations.execute(
        principal,
        Request(
            "extensions.read",
            resource_id=name,
            query={**query, "sourceId": "fabricated-hidden-source"},
        ),
    )
    assert allowed.status == 200
    assert json.loads(allowed.body)["data"]["metric"]["sourceId"] == "manual"
    assert denied.status == 403


def test_optional_invalid_disabled_metadata_and_extra_query_are_safe(tmp_path):
    runtime, owner, _ = secured(tmp_path / "owner")
    config = runtime.operations.config
    extension = example(config, "local.weekly-mass")
    # Disabled entries disclose no installation name; invalid optional source
    # does not turn a useful built-in discovery response into a failure.
    for raw in ((extension / "extension.json").read_bytes(), b"{invalid"):
        (extension / "extension.json").write_bytes(raw)
        response = discover(runtime, owner.principal)
        assert response.status == 200
        assert json.loads(response.body)["data"]["extensions"] == []
        assert b"local.weekly-mass" not in response.body
    response = runtime.operations.execute(
        owner.principal, Request("workspace.discover", query={"path": "/private"})
    )
    assert response.status == 422
    for request in (
        Request("workspace.discover", resource_id="private"),
        Request("workspace.discover", payload={}),
    ):
        assert runtime.operations.execute(owner.principal, request).status == 422
    routes = [route for route in ENDPOINTS if route.operation == "workspace.discover"]
    assert len(routes) == 1 and routes[0].path == "/v1/workspace/discovery"
    assert routes[0].method == "GET" and not routes[0].query_keys


def test_only_bounded_valid_source_metadata_and_no_unbound_image_identity():
    # Fabricated hashes test serialization only; these are not release evidence.
    supplied = ReleaseIdentity(
        package_version="0.1.0",
        source_commit="a" * 40,
        source_tree="b" * 40,
        source_archive_sha256="c" * 64,
        docs_sha256="d" * 64,
        source_evidence="packaged_manifest",
        artifact=ArtifactIdentity("sha256:" + "e" * 64, "linux/amd64", "v0.1.0"),
    )
    result = source_identity(supplied)
    assert result["sourceEvidence"] == "packaged_manifest"
    assert result["sourceCommit"] == "a" * 40
    assert result["runtimeArtifactDigest"] is None and result["imagePlatform"] is None
    assert result["releaseVersion"] is None and result["sourceDirty"] is None
    for value in (
        replace(supplied, source_commit="/private/path"),
        replace(supplied, docs_sha256=None),
    ):
        invalid = source_identity(value)
        assert (
            invalid["sourceEvidence"] == "unknown" and invalid["sourceCommit"] is None
        )
    assert (
        source_identity(replace(supplied, package_version="x" * 100))["packageVersion"]
        is None
    )
