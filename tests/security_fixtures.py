"""Synthetic security workspaces; generated test secrets never leave tmp_path."""

import json

from health_buddy.security_api import BearerProof, BootstrapProof, SecurityRequest
from health_buddy.security_runtime import open_runtime, setup_security
from health_buddy.workspace import initialize


def secured(root, *, receiver=False, proxy=False):
    initialize(root)
    path = root / "config.json"
    value = json.loads(path.read_text())
    if receiver:
        value["integrations"]["healthkit"] = {"enabled": True, "mode": "receiver"}
    if proxy:
        value["security"].update(
            ingress="tailscale-uds", externalOrigin="https://synthetic.example.invalid",
            ownerSubject="synthetic-owner@example.invalid",
        )
    path.write_text(json.dumps(value))
    proof_file = root / "secrets/bootstrap.json"
    setup_security(root, proof_file)
    proof = json.loads(proof_file.read_text())["proof"]
    runtime = open_runtime(root)
    identity = runtime.operations.journal.state().identity
    reply = runtime.security.execute(None, SecurityRequest(
        "bootstrap.redeem", proof=BootstrapProof(proof), identity=identity,
    ))
    token = reply.secret.value
    owner = runtime.security.authenticate(BearerProof(token))
    return runtime, owner, token


def action(runtime, owner, name, *, payload=None, resource=None):
    return runtime.security.execute(owner.principal if owner else None, SecurityRequest(
        name, payload=payload, resource_id=resource,
        identity=runtime.operations.journal.state().identity,
    ))
