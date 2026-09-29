# Canonical operations and extension interface

Every supported UI, native CLI, compatibility logger and extension reaches
`Operations.execute(principal, Request)`. `Principal.credential_id` is a
policy-issued authenticated handle; knowing a persisted credential identifier
is not authentication. The default policy denies every request. Explicit
`--development` installs the local owner policy for native use and loopback
HTTP. Production credentials, pairing, browser sessions and deployment remain
CES-1067/CES-1068. Owner-trusted Python is not a hostile-code sandbox.

## Find the implementation

| Concern | Owner paths |
| --- | --- |
| Typed finite transport seam | `src/health_buddy/service_api.py`, `domain.py` |
| Admission, canonical mutation, source registration and backup seam | `operations.py`, `policy.py` |
| Durable decision, receipts, bootstrap and recovery | `journal.py`, `durability.py`, `stores.py`, `health_store.py` |
| Retained logger and plan semantics | `loggers.py`, `records.py`, `plans.py` |
| Source filtering, projections and private last-good inputs | `snapshots.py`, `views.py`, `projection.py` |
| Native/browser durable pending workflows | `app.py`, `cli.py`, `client_workflow.py`, dashboard template |
| Maintained HTTP, bounded admission and child factory | `transport.py`, `production_server.py` |
| Scoped examples | `extensions.py` (`ScopedClient`, `latest_body_mass`, `water_connector`) |
| Executable synthetic evidence | `tests/test_canonical_*.py`, `tests/test_client_workflow.py`, transport/portable tests and six dashboard browser scripts |

The fixed route table below defines the finite service route seam;
`docs/canonical-clients.md` describes pending-envelope recovery and CLI syntax.
Historical extracted source remains mapped to its accepted extraction commit in
`provenance/extraction.json`; these hashes are not rolling current-file hashes.

## Discovery and reads

Capabilities disclose allowed finite operations, API/schema versions and caps.
They advertise `sourceStatusOperation: "projection.status"` for a records reader;
that separately authorized operation provides source availability, last-success
timestamps, freshness and missingness. Device-only `sync:status` discovery has
no owner/source health projection and never scans health history.

Record lists accept an explicit date window of at most 366 days, at most 500 rows
per page, requested kinds/sources/fields, and an opaque cursor bound to the
selected window, current policy filters and canonical revision. Repeat the
returned exact window when following a cursor; revision changes require a new
read. Optional SQLite history is filtered by source/type/window and bounded at
the storage boundary. If a selected type exceeds the per-source window budget,
`413 source_window_too_large` requires a narrower range or kinds. This is a
size limitation, not proof that the source is missing. Direct stable-ID lookup
does not inherit the dashboard window or unrelated history truncation.

CSV observation IDs survive supported natural-key replacements and restarts;
metadata records locations/provenance without mirroring CSV values. Generic
observations are disjoint JSON records, using the same journal/revision. Receiver
IDs use a protected `hk:` namespace; SleepIQ export IDs use protected `sleep:`.
Receiver IDs derive from the registered logical stream and native source ID,
not content hashes or row positions. Tombstones remain authoritative.

Every observation exposes observed/received timestamps, source identity/kind,
provenance, nullable source timezone and honest missingness. Body mass remains a
scalar with explicit unit; its recorded body-composition fields are typed
attributes. Compound retained records use per-field value/unit/missingness;
empty optional fields are `null` with `not_recorded`, never zero. Field grants
filter the actual response; rich HTML/context is refused under partial field
grants because those views require complete admitted records.

Dashboard budgets apply separately to domains and retain admitted session
parents for selected set/cardio children. A dense heart-rate or set stream does
not evict weight measurements. Source and kind grants apply before optional
collection and again at disclosure. Last-good caches store private inputs and
actual source/window/kind coverage, not previously authorized HTML. Current
identity/version/grants are rechecked and the projection is rebuilt; stale
components retain their original revision/coverage. A narrow cache cannot be
relabeled as a broad complete read.

## Writes and recovery

Authentication/current grant/device binding precede tuple/version/shape checks,
then idempotency lookup, then revision comparison and mutation. Generic requests
carry the exact original identity tuple, `If-Match: "rev-N"` and idempotency key.
A successful new key advances the canonical revision once. Identical retries
replay the saved status/body/revision bytes; changed intent conflicts. Receipt
headers may add the documented replay marker without altering the original
saved receipt. Revocation/epoch change is checked even for a known old key.

HealthKit keeps the retained schema-v1 batch protocol on the canonical receiver:
current authenticated device/stream binding plus exact receiver tuple and
`X-Health-Device-ID` agreement. Its duplicate acknowledgement is the documented
exception: immutable original receipt remains saved, and repeated duplicates
return deterministic `duplicateBatch:true` with original full accepted counts,
unchanged revision and exact saved receiver headers. Old duplicate aggregate
batches cannot rewind stream metadata or resurrect tombstones.

A workspace lock admits one writer across processes and serializes fresh reads
with recovery. A transaction prepares immutable private Git objects/ref, its
SQLite effect, response bytes and timestamps before the durable decision.
`PREPARED` can be discarded; after fsynced `COMMIT_INTENT`, recovery rolls all
stores forward before fresh disclosure. The HealthKit effect marker and stream
changes share one SQLite transaction. Coordinator state binds the receiver ID,
path and latest acknowledged effect digest; missing/replaced/older same-identity
SQLite cannot silently become an empty or rolled-back receiver. With no pending
cross-store decision, manual work can remain useful while an optional receiver
is unavailable. A pending decision missing required input blocks fresh mutation
and consistent backup until operator reconciliation.

The serializer distinguishes request-shape bounds from complete datasets and
durable manifests. Native and HTTP ordinary requests are 64 KiB, plans 256 KiB,
HealthKit 4 MiB; responses 4 MiB and durable manifests 8 MiB. Finite, duplicate-free
JSON is required. A receiver batch permits 500 records/500 deletions and 40,000
JSON nodes; ordinary requests 20,000. The complete effect/receipt must be bounded
and recoverable before COMMIT_INTENT. Deadlines are rechecked after lock waits
and before decision; after decision the coordinator owns completion/recovery.
A retryable503 may represent an ambiguous outcome and never asserts rollback.

Hard-exit subprocess tests cover durable boundaries and fresh-process recovery;
writer/read races inspect canonical state and receipts. Those tests plus fsync
source review do not certify hardware or filesystem power-loss behavior.

## Optional source transition and backup

The single configured `storage.healthkit` path has explicit read-only or receiver
mode. Existing enabled-only configuration remains read-only. Receiver opt-in may
initialize an absent/empty database; a nonempty legacy import is refused with a
reconciliation requirement. It is never read additively alongside a second
receiver database. After receiver adoption, switching back to raw import mode
requires operator reconciliation; it cannot bypass receiver integrity checks.
Source/device registration is an authenticated owner operation on the native
coordinator, not a caller-selected stream or token minted by each upload.
Private migration/cutover and device acceptance remain separate tickets.

`with service.backup(authenticated_owner) as inventory:` retains the canonical
lock through the caller's copy. It recovers decisions, verifies bound stores,
and supplies identity/revision plus required paths. Copy the complete private
workspace, including source overrides, configuration, personal tests/state and
secrets; the required-path inventory is not a reduced backup allowlist. No
public operation returns local filesystem paths. Restore/cutover tooling remains
separate work; this context does not authorize or perform a production backup.

## Extension contract

An extension receives only a scoped Operations client and authenticated handle.
It cannot supply Authority or install policy through an operation. A connector
uses a previously registered granted source and original durable envelope; the
water example never guesses a revision or invents a retry key. The metric example
reads explicit source/window pages from the same canonical IDs displayed by the
dashboard, keeps a single revision and exposes unit, timezone, provenance source
IDs, freshness and missingness. A missing measurement remains unknown.

Dashboard metadata and `projection.status` distinguish `current` data from
`partial` bounded views and `stale` views using last-good or unavailable-source
data. `truncated:true` means an admitted source/type or dashboard domain exceeded
its view budget; dashboard and copied context explicitly warn that totals may
be incomplete. The flag is filtered by current source/kind grants. It does not
claim the underlying records were deleted or that a bounded view is complete.
Stale data can also be truncated, so callers must inspect both fields.

The native and browser client workflows persist complete original pending
requests before send and advance only after a matching validated receipt.
Conflicts, unknown outcomes, corruption and epoch changes preserve the pending
intent for explicit reconciliation. Real credential rotation and stable actor
binding are CES-1067 integration; tests use explicit synthetic authenticated
handles and no configured external provider calls.

## Fixed routes and requests

| Route | Request.operation | Resource/payload/query |
| --- | --- | --- |
| GET /v1/capabilities | capabilities | none |
| GET /v1/records | records.list | bounded from/to, kinds, sourceIds, fields, limit, cursor |
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
| GET / or /index.html | dashboard.read | protected HTML or format=json; export=1, tab, theme |
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
never filled from client JSON. Logger intent is `{sourceId, fields, replaceExisting?}`. `fields` uses the
camelCase equivalents of the fixed snake_case names in `loggers.FIELDS`
(for example measuredAtLocal,
weightLb, timezone). Only intake/blood-pressure support replaceExisting.
Completed-workout JSON retains its documented dashboard schema. Plans use
camelCase schemaVersion2 fields while dynamic template/date IDs remain literal.

The untrusted `X-Health-Device-ID` header populates `health_device_id`; core
requires it to match both the schema-v1 body deviceId and policy-owned binding.
It never creates or selects a device grant.

`Request.deadline` is an optional monotonic deadline supplied by the trusted
adapter, checked before the durable decision and excluded from intent digest.
Expired admission cannot commit. After COMMIT_INTENT, cancellation/disconnect
cannot abort or discard recovery; the non-abandoning worker finishes or leaves
the decided transaction recoverable before admitting another canonical read.


## Natural-key and source ownership

The named source must own every matched or replaced target row, even for a
no-op duplicate and even when the principal has both sources granted. No logger
success silently relabels an existing source. Intake retains distinct events
at the same timestamp; exact duplicates are no-ops, and replacement requires
exactly one matching timestamp. A supported old intake header without sodium
is promoted during initial adoption with an unknown value, preserving every
existing event. Measurement/BP timestamps, daily circumference site/side,
workout session IDs and the retained incremental set/cardio keys remain the
legacy business keys; canonical observation identity is separate metadata.
Incremental sets use session/exercise/ordinal, while completed-workout payloads
retain the full validator's equipment-aware set identity. Their semantics are
not silently interchanged by generic PUT.

## Synthetic native example

```python
from pathlib import Path
from health_buddy.extensions import ScopedClient, latest_body_mass
from health_buddy.operations import open_service
from health_buddy.policy import DEVELOPMENT_PRINCIPAL

service = open_service(Path("/tmp/example-health-workspace"), development=True)
client = ScopedClient(service, DEVELOPMENT_PRINCIPAL)
metric = latest_body_mass(
    client, source_id="manual",
    from_time="2030-01-01T00:00:00Z", to_time="2030-01-31T00:00:00Z",
)
# Empty data gives metric.value=None and explicit missingness, not zero.
```

The path and dates above are fabricated. Ordinary `open_service(path)` installs
the deny policy. A connector must first receive an owner-registered source,
scoped authenticated handle and the complete persisted write envelope; the
example does not mint production credentials or submit a private observation.
