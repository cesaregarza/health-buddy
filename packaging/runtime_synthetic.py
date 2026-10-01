"""Synthetic image qualification only; never invoke against an owner workspace."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from importlib.resources import files
from pathlib import Path

sys.path.insert(0, "/opt/health-buddy/source/src")

from health_buddy.core.domain import digest, encode, identity_value
from health_buddy.core.durability import atomic_bytes
from health_buddy.core.extension_api import PrepareConnector
from health_buddy.core.security_api import AgentGrant, BearerProof, SecurityRequest
from health_buddy.core.service_api import Request
from health_buddy.extension_install import install
from health_buddy.extension_jobs import run_event
from health_buddy.extension_prepare import prepare
from health_buddy.extension_registry import Registry
from health_buddy.packaged_runtime import open_packaged
from health_buddy.security_runtime import read_credential, setup_security

SOURCE = Path("/opt/health-buddy/source")
ROOT = Path("/workspace")
NAME = "local.water-import"
QUALIFICATION = ROOT / "personal/state/container-qualification"
OWNER = ROOT / "secrets/synthetic-owner-token"
GRANT = ROOT / "secrets/synthetic-water-token"


def event(phase: str) -> dict[str, object]:
    return {
        "eventId": "container-" + phase,
        "sourceId": "synthetic-water",
        "observedAt": "2030-01-03T10:00:00Z",
        "value": 300,
        "unit": "mL",
    }


def state_path(phase: str) -> Path:
    return (
        ROOT
        / "personal/extensions"
        / NAME
        / "state/requests"
        / (digest({"eventId": event(phase)["eventId"]}) + ".json")
    )


def personal_inventory() -> dict[str, str]:
    selected = [ROOT / "config.json", ROOT / "personal/WORKSPACE.md"]
    for name in (NAME, "local.weekly-mass"):
        extension = ROOT / "personal/extensions" / name
        selected.extend(
            path
            for path in extension.rglob("*")
            if path.is_file() and "state" not in path.relative_to(extension).parts
        )
    return {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in selected
    }


def seed() -> None:
    setup_security(ROOT, OWNER, owner_token=True)
    runtime = open_packaged(ROOT)
    proof = BearerProof(read_credential(OWNER))
    config = runtime.operations.config
    for name in (NAME, "local.weekly-mass"):
        install(
            config,
            Path(str(files("health_buddy").joinpath("reference_extensions", name))),
        )
    prepare(
        config,
        runtime,
        proof,
        PrepareConnector(NAME, "synthetic-water", "secrets/synthetic-water-token"),
    )
    weekly = ROOT / "personal/extensions/local.weekly-mass"
    (weekly / "src/metric.py").write_text(
        (weekly / "src/metric.py").read_text()
        + "\n# Synthetic owner-maintained customization.\n"
    )
    for relative, data in (
        ("assets/owner.txt", b"synthetic asset"),
        ("tests/owner_check.py", b"assert 2 + 2 == 4\n"),
        ("notes/owner.md", b"Synthetic maintained note\n"),
    ):
        path = weekly / relative
        path.write_bytes(data)
        path.chmod(0o600)
    Registry(config).enable(NAME, source_ids=("synthetic-water",))
    Registry(config).enable("local.weekly-mass", source_ids=("manual",))
    QUALIFICATION.mkdir(parents=True, mode=0o700)
    atomic_bytes(QUALIFICATION / "ordinary-event.json", encode(event("ordinary")))
    atomic_bytes(QUALIFICATION / "personal.json", encode(personal_inventory()))
    atomic_bytes(QUALIFICATION / "seeded", b"synthetic-only\n")
    check()


def backup_crypto_check() -> None:
    """Exercise shipped native crypto using fabricated bytes, never owner data."""
    from health_buddy.backup_crypto import seal, unseal
    from health_buddy.core.service_api import ServiceError

    assert (QUALIFICATION / "seeded").read_bytes() == b"synthetic-only\n", (
        "synthetic guard missing"
    )
    key = os.urandom(32)
    fabricated = b"synthetic encrypted backup qualification"
    ciphertext = seal(fabricated, key)
    assert unseal(ciphertext, key) == fabricated, "backup crypto roundtrip failed"
    altered = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    try:
        unseal(altered, key)
    except ServiceError as error:
        assert error.code == "backup_authentication_failed", (
            "backup crypto tamper rejection failed"
        )
    else:
        raise AssertionError("backup crypto accepted tampered ciphertext")
    print(
        json.dumps(
            {
                "schemaVersion": 1,
                "scope": "shipped_native_backup_crypto_fabricated_bytes_only",
                "authenticatedRoundtrip": True,
                "tamperRejected": True,
            },
            sort_keys=True,
        )
    )


def seed_sdk() -> None:
    """OS-owner synthetic handoff; the host client never opens this authority."""
    check()
    runtime = open_packaged(ROOT)
    owner = runtime.security.authenticate(BearerProof(read_credential(OWNER)))
    identity = runtime.operations.journal.state().identity
    reply = runtime.security.execute(
        owner.principal,
        SecurityRequest(
            "grants.create",
            identity=identity,
            payload=AgentGrant(
                "Synthetic packaged SDK agent",
                ("records:read", "records:write"),
                source_ids=("manual",),
                read_sources=("manual",),
                read_kinds=None,
                read_fields=None,
            ),
        ),
    )
    assert reply.secret is not None, "Synthetic SDK grant unavailable"
    token = ROOT / "secrets/synthetic-sdk-token"
    with token.open("xb") as stream:
        stream.write(reply.secret.value.encode("ascii"))
    token.chmod(0o600)
    atomic_bytes(QUALIFICATION / "sdk-identity.json", encode(identity_value(identity)))


def check() -> None:
    assert (QUALIFICATION / "seeded").read_bytes() == b"synthetic-only\n", (
        "synthetic guard missing"
    )
    assert personal_inventory() == json.loads(
        (QUALIFICATION / "personal.json").read_bytes()
    ), "personal source/config/assets/tests/notes changed"
    runtime = open_packaged(ROOT)
    principal = runtime.security.authenticate(
        BearerProof(read_credential(OWNER))
    ).principal
    result = runtime.operations.execute(principal, Request("extensions.list"))
    assert result.status == 200, "extension discovery unavailable"
    result = runtime.operations.execute(
        principal, Request("extensions.read", resource_id="local.weekly-mass")
    )
    assert result.status == 200, "maintained Python metric unavailable"
    assert (SOURCE / "health-runner/dashboard/template.html").is_file(), (
        "dashboard missing"
    )
    assert (SOURCE / "health-runner/dashboard/assets/extension-worker.js").is_file(), (
        "dashboard JavaScript worker missing"
    )
    assert not runtime.operations.config.enabled("jev"), "optional provider enabled"
    try:
        (SOURCE / "runtime-write-must-fail").write_bytes(b"must not persist")
    except OSError:
        pass
    else:
        raise AssertionError("release filesystem was writable")


def interrupt(phase: str) -> None:
    check()
    runtime = open_packaged(ROOT)
    original = runtime.operations.execute
    initial = runtime.operations.journal.state().revision

    def intercepted(principal, request):
        if request.operation != "records.put":
            return original(principal, request)
        receipt = None
        if phase == "after-commit":
            response = original(principal, request)
            assert response.status == 200, "canonical fixture save failed"
            receipt = {
                "status": response.status,
                "bodyBase64": base64.b64encode(response.body).decode("ascii"),
                "headers": [
                    list(item)
                    for item in response.headers
                    if item[0].lower() != "idempotency-replayed"
                ],
            }
        pending = json.loads(state_path(phase).read_bytes())
        assert pending["state"] == "pending" and pending["cursor"] == 0, (
            "intent not durable before send"
        )
        atomic_bytes(
            QUALIFICATION / (phase + ".json"),
            encode(
                {"initialRevision": initial, "pending": pending, "receipt": receipt}
            ),
        )
        os._exit(83)

    runtime.operations.execute = intercepted
    run_event(
        runtime.operations.config,
        runtime,
        BearerProof(read_credential(GRANT)),
        NAME,
        event(phase),
    )
    raise AssertionError("fault boundary was not reached")


def resume(phase: str) -> None:
    check()
    before = json.loads((QUALIFICATION / (phase + ".json")).read_bytes())
    runtime = open_packaged(ROOT)
    credential = BearerProof(read_credential(GRANT))
    was_complete = json.loads(state_path(phase).read_bytes())["state"] == "complete"
    previous_revision = runtime.operations.journal.state().revision
    result = run_event(
        runtime.operations.config, runtime, credential, NAME, event(phase)
    )
    saved = state_path(phase).read_bytes()
    value = json.loads(saved)
    assert value["state"] == "complete" and value["cursor"] == 1, (
        "receipt/cursor incomplete"
    )
    for key in ("envelope", "intentDigest", "clientIdentity"):
        assert value[key] == before["pending"][key], "original request changed"
    if before["receipt"] is not None:
        assert value["receipt"] == before["receipt"], "saved receipt changed"
    expected_revision = (
        previous_revision if was_complete else before["initialRevision"] + 1
    )
    assert runtime.operations.journal.state().revision == expected_revision, (
        "duplicate canonical effect"
    )
    retained = json.loads(base64.b64decode(value["receipt"]["bodyBase64"]))
    assert retained["meta"]["dataRevision"] == before["initialRevision"] + 1, (
        "original receipt revision changed"
    )
    assert (
        run_event(runtime.operations.config, runtime, credential, NAME, event(phase))
        == result
    ), "replay changed response"
    assert state_path(phase).read_bytes() == saved, (
        "completed replay changed state bytes"
    )
    principal = runtime.security.authenticate(
        BearerProof(read_credential(OWNER))
    ).principal
    listed = runtime.operations.execute(
        principal,
        Request(
            "records.list",
            query={
                "from": "2030-01-01T00:00:00Z",
                "to": "2030-01-07T23:59:59Z",
                "sourceIds": "synthetic-water",
            },
        ),
    )
    rows = json.loads(listed.body)["data"]["records"]
    matches = [row for row in rows if row["id"] == value["envelope"]["resourceId"]]
    assert (
        len(matches) == 1 and matches[0]["value"] == 0.3 and matches[0]["unit"] == "L"
    ), "canonical normalized event missing or duplicated"
    check()


def ordinary_check() -> None:
    """The production packaged job command has already written this event."""
    check()
    state = json.loads(state_path("ordinary").read_bytes())
    assert state["state"] == "complete" and state["cursor"] == 1, (
        "shipped job command did not save"
    )
    assert (
        state["envelope"]["payload"]["value"] == 0.3
        and state["envelope"]["payload"]["unit"] == "L"
    ), "job normalization changed"
    receipt = json.loads(base64.b64decode(state["receipt"]["bodyBase64"]))
    assert receipt["data"]["saved"] is True, "shipped job receipt invalid"


def licenses() -> None:
    """Retain actual image legal/SBOM bytes and hashes without workspace reads."""
    roots = (Path("/usr/share/doc"), Path("/opt/health-buddy/dependencies"))
    result = []
    total = 0
    visited = 0
    for root in roots:
        for directory, dirs, names in os.walk(root, followlinks=False):
            visited += len(dirs) + len(names)
            if visited > 50000:
                raise ValueError("legal_inventory_entry_limit")
            for name in names:
                path = Path(directory) / name
                relative = path.relative_to(root)
                selected = (root.name == "doc" and name == "copyright") or (
                    root.name == "dependencies"
                    and any(part.endswith(".dist-info") for part in relative.parts)
                    and (
                        name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))
                        or "sboms" in relative.parts
                    )
                )
                if not selected:
                    continue
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or path.stat().st_size > 1024**2
                ):
                    raise ValueError("legal_inventory_unsupported_file")
                data = path.read_bytes()
                total += len(data)
                if len(result) >= 1024 or total > 4 * 1024**2:
                    raise ValueError("legal_inventory_byte_limit")
                result.append(
                    {
                        "path": str(path),
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "text": data.decode("utf-8"),
                    }
                )
    print(
        json.dumps(
            {
                "schemaVersion": 1,
                "scope": (
                    "actual installed image legal files and shipped SBOMs; "
                    "SBOM membership is not static-link proof"
                ),
                "files": result,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    os.umask(0o077)
    if len(sys.argv) == 2 and sys.argv[1] == "licenses":
        licenses()
        raise SystemExit(0)
    if len(sys.argv) == 2 and sys.argv[1] == "seed":
        seed()
    elif len(sys.argv) == 2 and sys.argv[1] == "backup-crypto-check":
        backup_crypto_check()
    elif len(sys.argv) == 2 and sys.argv[1] == "seed-sdk":
        seed_sdk()
    elif len(sys.argv) == 2 and sys.argv[1] == "ordinary-check":
        ordinary_check()
    elif len(sys.argv) == 2 and sys.argv[1] == "check":
        check()
    elif (
        len(sys.argv) == 3
        and sys.argv[1] in ("interrupt", "resume")
        and sys.argv[2] in ("before-send", "after-commit")
    ):
        (interrupt if sys.argv[1] == "interrupt" else resume)(sys.argv[2])
    else:
        raise SystemExit("explicit synthetic qualification action required")
    print("synthetic container qualification step passed")
