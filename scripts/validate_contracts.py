#!/usr/bin/env python3
"""Check a finite synthetic contract corpus; this is not a server implementation.

Schema checks establish shape. Relational checks independently establish the
claimed no-double-write, no-privilege-escalation and preservation invariants.
There is no I/O beyond reading this repository, network, token crypto or storage.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "v1"
IDENTITY_HEADERS = {
    "installationId": "X-Installation-ID",
    "datasetId": "X-Dataset-ID",
    "restoreEpoch": "X-Restore-Epoch",
}
REQUIRED_CASES = {
    "read-present", "read-missing", "capabilities", "write-create",
    "identical-retry", "conflicting-retry", "stale-update", "spoofed-proxy",
    "trusted-owner-proxy", "tagged-client-token", "tagged-client-no-token",
    "device-cannot-read", "agent-cannot-install", "revoked-retry",
    "pairing-created", "pairing-redeemed", "pairing-replayed", "pairing-expired",
    "device-revoked", "healthkit-accepted", "healthkit-duplicate",
    "healthkit-missing-epoch", "healthkit-revoked-retry", "healthkit-wrong-device",
    "old-epoch-write", "replacement-same-observation", "replacement-uncertain-overlap",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load(path):
    def invalid_constant(value):
        raise ValueError(f"non-finite JSON number: {value}")

    return json.loads(path.read_text(), object_pairs_hook=unique_object,
                      parse_constant=invalid_constant)


def schema_check(value, filename):
    schema = load(CONTRACT / filename)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "timestamp has no offset")
    return result


def check_manifest(manifest):
    schema_check(manifest, "compatibility.schema.json")
    required = {
        "runtime": {"api", "storage", "extensions", "pairing", "phonePayload", "agentTools"},
        "installer": {"api", "storage", "extensions", "pairing"},
        "codex": {"api", "extensions", "agentTools"},
        "claudeCode": {"api", "extensions", "agentTools"},
        "iphone": {"pairing", "phonePayload"},
    }
    for name, component in manifest["components"].items():
        require(set(component["requires"]) == required[name], f"{name}: missing interface")
        for interface, versions in component["requires"].items():
            require(manifest["interfaces"][interface] in versions,
                    f"{name}: incompatible {interface}")


def error(case, status, code):
    response = case["response"]
    require(response["status"] == status, f"{case['id']}: expected {status}")
    require(response["body"].get("error") == {"code": code, "retryable": False},
            f"{case['id']}: wrong typed error")
    require(case["before"] == case["after"], f"{case['id']}: rejection mutated state")
    if status == 401:
        require("meta" not in response["body"], "unauthenticated identity leak")


def check_record(record):
    require(set(record) == {"recordId", "kind", "value", "unit", "observedAt",
                           "receivedAt", "provenance", "freshness", "missingness"},
            "canonical record fields drift")
    require(record["kind"] == "body_mass" and record["unit"] == "kg", "record units")
    require(type(record["value"]) in (int, float) and record["missingness"] is None,
            "present record must have a finite value and no missingness")
    require(Decimal(str(record["value"])).is_finite(), "non-finite record")
    require(set(record["provenance"]) == {"sourceId", "sourceKind"}, "provenance required")
    require(record["freshness"]["status"] in {"fresh", "stale", "unknown"}, "freshness")
    for value in [record["observedAt"], record["receivedAt"], record["freshness"]["asOf"]]:
        timestamp(value)


def check_intent(intent, context):
    require(set(intent) == {"kind", "value", "unit", "observedAt", "sourceId"},
            "mutation intent contains server-owned or unknown fields")
    require(intent["sourceId"] in context["allowedSourceIds"], "ungranted source")
    require(intent["kind"] == "body_mass" and intent["unit"] == "kg", "intent units")
    require(type(intent["value"]) in (int, float) and Decimal(str(intent["value"])).is_finite(), "intent value")
    timestamp(intent["observedAt"])


def check_corpus(corpus):
    schema_check(corpus, "scenarios.schema.json")
    cases = {case["id"]: case for case in corpus["scenarios"]}
    require(len(cases) == len(corpus["scenarios"]), "duplicate scenario ID")
    require(set(cases) == REQUIRED_CASES, "scenario coverage changed without contract review")
    identity = corpus["identity"]
    for value in identity.values():
        UUID(value)
    original = cases["write-create"]
    for case in cases.values():
        rule, request, response = case["rule"], case["request"], case["response"]
        before, after, context = case["before"], case["after"], case["context"]
        body = response["body"]
        if response["status"] >= 400:
            require(before == after, f"{case['id']}: failure changed state")
        if "meta" in body:
            active_identity = before.get("identity", identity)
            require(all(body["meta"][key] == value for key, value in active_identity.items()),
                    f"{case['id']}: response identity mismatch")
            if "dataRevision" in after:
                expected_revision = (original["after"]["dataRevision"] if rule == "retry"
                                     else after["dataRevision"])
                require(body["meta"]["dataRevision"] == expected_revision, "receipt revision drift")
        if rule == "read":
            require(before == after, "read changed state")
            if case["id"] == "read-present":
                require(response["status"] == 200, "present read failed")
                check_record(body["data"]["record"])
            else:
                error(case, 404, "record_not_found")
        elif rule == "capabilities":
            require(before == after and response["status"] == 200, "capability read")
            data = body["data"]
            require(all(data[key] == 1 for key in ["apiMajor", "pairingProtocol", "phonePayloadSchema", "extensionApi"]), "capability versions")
            states = {(s["availability"], s["freshness"], s["missingness"]) for s in data["sources"]}
            require(states == {("empty", "unknown", "no_records"), ("disabled", "unknown", "not_configured"), ("available", "fresh", "no_data_or_denied_read"), ("available", "stale", None), ("unavailable", "unknown", "source_error")}, "source absence/freshness conflated")
        elif rule in {"write", "retry", "conflict", "stale"}:
            require(all(request["headers"].get(header) == identity[key] for key, header in IDENTITY_HEADERS.items()), "write identity headers")
            require("records:write" in context["grants"], "write grant")
            require(bool(request["headers"].get("Idempotency-Key")), "missing retry key")
            check_intent(request["body"], context)
            if rule == "write":
                record = body["data"]["record"]
                check_record(record)
                require(response["status"] == 201 and all(record[k] == request["body"][k] for k in ["kind", "value", "unit", "observedAt"]), "create receipt")
                require(record["recordId"] == request["path"].rsplit("/", 1)[1] and record["receivedAt"] == context["serverNow"], "server-owned receipt fields")
                require(record["provenance"] == {"sourceId": request["body"]["sourceId"], "sourceKind": "manual"}, "grant-bound provenance")
                require(after == {"dataRevision": before["dataRevision"] + 1, "recordCount": before["recordCount"] + 1}, "create must commit once")
                require(request["headers"]["If-Match"] == f'"rev-{before["dataRevision"]}"', "create precondition")
            elif rule == "retry":
                require(request == original["request"], "retry request changed")
                require(context["actorId"] == original["context"]["actorId"], "retry actor drift")
                require(body == original["response"]["body"] and response["status"] == original["response"]["status"], "retry must return original receipt before stale check")
                require(response["headers"].get("Idempotency-Replayed") == "true" and before == after, "retry duplicated mutation")
            elif rule == "conflict":
                require(request["headers"]["Idempotency-Key"] == original["request"]["headers"]["Idempotency-Key"] and request["body"] != original["request"]["body"], "conflict input")
                error(case, 409, "idempotency_conflict")
            else:
                require(request["headers"]["If-Match"] != f'"rev-{before["dataRevision"]}"', "stale input not stale")
                require(request["headers"]["Idempotency-Key"] != original["request"]["headers"]["Idempotency-Key"], "stale input reuses key")
                error(case, 409, "revision_conflict")
        elif rule == "authorization":
            grants = context["grants"]
            if context.get("revoked"):
                error(case, 401, "credential_revoked")
            elif not request["headers"].get("Authorization") and context["peer"] != "verified-owner-proxy":
                error(case, 401, "unauthenticated")
            elif ("/extensions/" in request["path"] or "records:read" not in grants):
                error(case, 403, "forbidden")
            else:
                require(response["status"] == 200 and before == after, "valid scoped read rejected")
                check_record(body["data"]["record"])
        elif rule == "pairing":
            now = timestamp(context["now"])
            if before["intent"] == "absent":
                require("devices:manage" in context["grants"] and response["status"] == 201, "pairing owner grant")
                require((timestamp(body["data"]["expiresAt"]) - now).total_seconds() == 300, "pairing TTL")
                require(body["data"]["identity"] == identity and after == {"intent": "issued", "activeDevices": 0}, "pairing binding")
            elif before["intent"] == "consumed":
                error(case, 409, "pairing_consumed")
            elif now >= timestamp(context["expiresAt"]):
                error(case, 410, "pairing_expired")
            else:
                require(all(request["headers"].get(header) == identity[key] for key, header in IDENTITY_HEADERS.items()), "pairing receiver binding")
                require(response["status"] == 201 and after == {"intent": "consumed", "activeDevices": 1}, "pairing not consumed atomically")
                require(set(body["data"]["scopes"]) == {"healthkit:ingest", "sync:status"}, "pairing overgrant")
        elif rule == "revoke":
            require("devices:manage" in context["grants"] and body["data"]["revoked"] and after["activeDevices"] == 0, "device revocation")
        elif rule == "healthkit":
            check_healthkit(case, identity, cases)
        elif rule == "restore":
            require(context["freshGrant"] and before["identity"]["restoreEpoch"] != request["headers"]["X-Restore-Epoch"], "restore precondition")
            error(case, 409, "restore_epoch_changed")
        elif rule == "replacement":
            incoming = request["body"]["incoming"]
            require(context["ownerApprovedReplacement"] and not before["oldWriterActive"], "replacement needs retirement and owner choice")
            require(after["canonicalCount"] == before["canonicalCount"], "replacement duplicated canonical observation")
            if incoming == context["priorObservation"]:
                require(response["status"] == 200 and body["data"]["canonicalAdded"] == 0 and after["deliveryCount"] == before["deliveryCount"] + 1, "replacement provenance reconciliation")
            else:
                require(context["overlappingHistory"], "replacement overlap prerequisite")
                error(case, 409, "reconciliation_required")


def check_healthkit(case, identity, cases):
    request, response, context = case["request"], case["response"], case["context"]
    payload, receipt = request["body"], response["body"]
    require(set(payload) == {"schemaVersion", "batchId", "deviceId", "generatedAt", "records", "deletions"} and payload["schemaVersion"] == 1, "legacy payload schema changed")
    if context.get("revoked"):
        error(case, 401, "credential_revoked")
    elif payload["deviceId"] != context["boundDeviceId"] or request["headers"]["X-Health-Device-ID"] != context["boundDeviceId"]:
        error(case, 403, "device_mismatch")
    elif any(header not in request["headers"] for header in IDENTITY_HEADERS.values()):
        error(case, 428, "identity_required")
    else:
        require("healthkit:ingest" in context["grants"], "ingest grant missing")
        require(all(request["headers"][header] == identity[key] for key, header in IDENTITY_HEADERS.items()), "ingest identity mismatch")
        require(all(response["headers"].get(header) == identity[key] for key, header in IDENTITY_HEADERS.items()), "HealthKit acknowledgement identity mismatch")
        require(response["status"] == 200 and receipt["status"] == "accepted" and receipt["batchId"] == payload["batchId"], "HealthKit receipt identity")
        if receipt["duplicateBatch"]:
            prior = cases[context["prior"]]
            require(request == prior["request"] and prior["response"]["status"] == 200,
                    "duplicate acknowledgement has no identical prior batch")
        expected = (len(payload["records"]), len(payload["deletions"]))
        accepted = (receipt["recordsAccepted"], receipt["deletionsAccepted"])
        require(accepted == expected or (receipt["duplicateBatch"] and accepted == (0, 0)), "partial HealthKit acknowledgement")
        require(case["after"]["anchorAdvanced"] is True, "complete receipt did not permit anchor")
        delta = 0 if receipt["duplicateBatch"] else len(payload["deletions"])
        require(case["after"]["tombstoneCount"] == case["before"]["tombstoneCount"] + delta, "duplicate tombstone mutation")


def check_lifecycle(value):
    require(value["fixtureVersion"] == 1 and value["synthetic"] is True, "lifecycle provenance")
    restore = value["restore"]
    before, after = restore["before"], restore["after"]
    require(before["identity"]["datasetId"] == after["identity"]["datasetId"], "restore lost logical dataset")
    require(before["identity"]["restoreEpoch"] != after["identity"]["restoreEpoch"], "restore resurrected epoch")
    require(not after["activeGrants"] and not after["pairingIntents"] and after["requiresLocalBootstrap"] and after["requiresPhoneRepair"], "restore resurrected authorization")
    require(sorted(restore["backupInventory"]) == sorted(restore["restoredInventory"]), "restore lost personal work")
    for part in ["src", "assets", "config", "tests", "notes", "state"]:
        require(any(f"/{part}/" in path for path in restore["backupInventory"]), f"backup omitted {part}")
    require({x["id"] for x in value["upgrades"]} == {"compatible", "incompatible", "fork"}, "upgrade coverage")
    for upgrade in value["upgrades"]:
        expected = ("core_fork_requires_rebase" if upgrade["coreFork"] else
                    "activate" if upgrade["runtimeExtensionApi"] in upgrade["extensionRequires"] else "blocked")
        require(upgrade["outcome"] == expected and upgrade["preservesPersonal"], "unsafe upgrade activation")


def check_extension(example):
    schema_check(example, "extension.schema.json")
    manifest = example["manifest"]
    metric = manifest["kind"] == "metric-view"
    require(set(manifest["entrypoints"]) == ({"metric", "view"} if metric else {"connector", "workflow"}), "extension entrypoints")
    require(manifest["scopes"] == (["records:read"] if metric else ["records:write"]), "extension scope creep")
    if not metric:
        require(example["cases"][0]["input"] == example["cases"][1]["input"] and
                example["cases"][0]["output"] == example["cases"][1]["output"],
                "connector retry changed identity or normalized payload")
    for case in example["cases"]:
        source, result = case["input"], case["output"]
        require(source["sourceId"] == result["sourceId"], "extension lost provenance")
        if metric:
            numbers = source["values"]
            expected = float(sum(Decimal(str(n)) for n in numbers) / len(numbers)) if numbers else None
            require(result["value"] == expected and result["count"] == len(numbers), "metric wrong value/count")
            require(result["unit"] == source["unit"] == "kg", "metric units")
            require(result["missingness"] == (None if numbers else "insufficient_data"), "metric missingness")
        else:
            require(source["unit"] == "mL" and result["unit"] == "L" and Decimal(str(result["value"])) == Decimal(str(source["value"])) / 1000, "connector unit normalization")
            require(result["idempotencyKey"] == manifest["id"] + ":" + source["eventId"], "connector retry key")


def validate_all():
    check_manifest(load(CONTRACT / "compatibility.json"))
    corpus = load(CONTRACT / "fixtures/scenarios.json")
    check_corpus(corpus)
    check_lifecycle(load(CONTRACT / "fixtures/lifecycle.json"))
    for name in ["weekly-mass", "water-import"]:
        check_extension(load(CONTRACT / "examples" / f"{name}.json"))
    return len(corpus["scenarios"])


if __name__ == "__main__":
    try:
        count = validate_all()
    except (ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"Contract validation failed: {exc}") from exc
    print(f"PASS: compatibility, {count} synthetic scenarios, lifecycle and 2 extension examples")
