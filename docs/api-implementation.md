# Canonical service implementation boundary

CES-1066 implementation in progress, based on accepted CES-1065
`1d7b691f5b89a8083663e0e1437c7b2afc97c39d`. This small interface checkpoint
does not establish runtime behavior or test success. The normative identity,
retry and authorization rules remain [v1-contract.md](v1-contract.md).

`health_buddy.service_api` is the shared synchronous interface. Transport
receives an `Operations` object and an explicitly injected trusted principal
resolver. Production defaults to no principal and a deny policy. It never
constructs an effective grant from request JSON or forwarded identity headers.
The implementation will be `health_buddy.operations.Service(workspace, policy)`;
omitting `policy` denies all protected operations. An explicitly named local
development constructor supplies the local owner policy; it is not production
authentication. No tokens or pairing are implemented by this slice.

Transport resolves a fixed operation, authenticates, calls `preflight`, bounds
and parses input, then calls `execute` in a bounded worker. `execute` performs
current authentication/grants, exact identity, shape/version, retry lookup,
revision and mutation checks in order. Preliminary admission cannot bypass its
policy recheck. The policy guard serializes revocation through the durable
commit decision, in workspace-lock then policy-lock order. Transport owns no
store, journal, retry state or commit. Core owns safe response serialization;
the transport emits its response bytes unchanged, including replay receipts.

## Fixed routes and requests

| Route | Request.operation | Resource/payload/query |
| --- | --- | --- |
| GET /v1/capabilities | capabilities | none |
| GET /v1/records | records.list | bounded from/to, kinds, sourceIds, fields, limit |
| GET /v1/records/{id} | records.get | resource_id |
| PUT /v1/records/{id} | records.put | resource_id and strict observation intent |
| GET /v1/context | context.read | scopes, days, ask, limit; core bounds all |
| GET /v1/context/scopes | context.scopes | none |
| POST /v1/workouts | workouts.write | retained completed-workout JSON schema |
| GET /v1/workouts/status | workouts.status | none |
| POST /v1/logs/{kind} | logs.write | kind in resource_id and typed logger intent |
| GET /v1/plans/current | plan.read | none |
| PUT /v1/plans/current | plan.write | validated program JSON |
| POST /v1/healthkit/batches | healthkit.ingest | retained protocol-v1 batch JSON |
| GET /v1/projections/status | projection.status | none |
| GET / or /index.html | dashboard.read | protected HTML from one canonical snapshot |
| GET /icon.svg or /manifest.webmanifest | asset.read | exact basename in resource_id |
| POST /v1/context/intent | context.intent | explicit enabled-provider intent |
| GET /v1/training/fast | training.fast.read | date, revision |
| POST /v1/training/fast | training.fast.write | date, revision, step |

Liveness is the only unprotected route and contains no identity or health data.
Static allowlisted assets also require admission through `asset.read`; no other
file name or path is an asset route. Legacy `/api` aliases may map
to the corresponding operation while bundled UI moves to the v1 envelope; they
must require the same identity/revision/retry headers for health writes and may
not silently generate a new key or retry envelope. Core derives method/path
from operation/resource identity so all adapters share the same ledger scope.
Provider calls/cache changes do not masquerade as health mutations.

Write headers populate `Identity`, `if_match` and `idempotency_key` as untrusted
strings; missing or malformed values are rejected by core in the documented
order. HealthKit keeps its body batch identity and success acknowledgement,
including receiver identity response headers. Missing server-owned metadata is
never filled from client JSON. The precise new logger/domain schemas are core
owned and will be documented before final verification.

The untrusted `X-Health-Device-ID` header populates `health_device_id`; core
requires it to match both the schema-v1 body deviceId and policy-owned binding.
It never creates or selects a device grant.

`Request.deadline` is an optional monotonic deadline supplied by the trusted
adapter, checked before the durable decision and excluded from intent digest.
Expired admission cannot commit. After COMMIT_INTENT, cancellation/disconnect
cannot abort or discard recovery; the non-abandoning worker finishes or leaves
the decided transaction recoverable before admitting another canonical read.

## Parallel ownership

- Core worker owns `service_api.py`, domain/operations/journal/stores/policy,
  App/CLI, canonical projections, scoped extension clients, core tests, bundled
  dashboard integration and this API/domain documentation. The CLI imports the
  transport's `serve` hook lazily after it is integrated.
- Transport worker owns `transport*.py`, `production_server.py`, replacement
  `server.py`, `tests/test_transport*`, HTTP-adapter tests and transport docs.
  It owns the production dependency pins in `pyproject.toml` and related notices;
  core will not edit that file concurrently. New code remains inside the already
  configured lint/type/package scope.
- Transport factory: `transport.create_app(operations, *, authenticate=None,
  development=False)` returns the ASGI application. The resolver is transport
  owned; its output is only `Principal | None`. A no-resolver production app
  rejects protected requests. Development mode explicitly restricts loopback,
  Host and Origin and chooses only an explicit development principal; it must
  not cause a deny-policy service to accept a request.
- CLI hook: `production_server.serve(operations_factory, *, port=8791,
  development=False) -> None`, with `Callable[[], Operations]`; an importable
  factory or picklable partial constructs operations/policy inside the serving
  child, never inheriting live store/lock/database handles. Explicit loopback
  bind, one serving worker plus a lightweight supervisor, and bounded resources.
  Production deployment configuration/auth remains later.
- The integration owner will cherry-pick the independently reviewed transport
  changes, freeze an exact combined SHA and submit it to the one testing queue.
  Interface changes are coordinated before either side edits this module.

The retained manual Git adapter stays private/local with no remote, hooks,
signing or code updates. The canonical coordinator journals immutable prepared
commit/ref and HealthKit SQLite effects. Recovery/adoption and exact receipts
must be proven by synthetic queue tests before this document can claim success.
Core bounds complete response and receipt bytes before COMMIT_INTENT. A later
temporary failure is a retryable, ambiguous 503; it never claims an already
decided mutation was aborted. The exact original retry envelope is retained.
