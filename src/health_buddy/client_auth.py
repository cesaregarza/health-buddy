"""Reauthenticate one explicitly supplied native proof, never ambient secrets."""

from __future__ import annotations

from .security_api import BearerProof, ClientIdentity, Runtime
from .service_api import Operation, Principal, Request, Response


class AuthenticatedOperations:
    def __init__(self, runtime: Runtime, proof: BearerProof) -> None:
        self.runtime = runtime
        self.proof = proof

    def describe(self) -> ClientIdentity:
        admitted = self.runtime.security.authenticate(self.proof)
        return self.runtime.security.describe(admitted.principal)

    def preflight(self, principal: Principal | None, operation: Operation) -> Response | None:
        admitted = self.runtime.security.authenticate(self.proof)
        return self.runtime.operations.preflight(admitted.principal, operation)

    def execute(self, principal: Principal | None, request: Request) -> Response:
        admitted = self.runtime.security.authenticate(self.proof)
        return self.runtime.operations.execute(admitted.principal, request)
