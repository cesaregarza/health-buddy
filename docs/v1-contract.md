# Health Buddy v1 contract

Contract version: **1.0.0**, status: **implementation target**, owner: CES-1063.
The fixture oracle specifies examples; it is not a production implementation.

## Product and host decisions

- One owner per installation; useful daily dashboard and logging without an
  agent, extra model API key, always-running LLM, or Jev. The owner's existing
  Codex or Claude Code harness supplies reasoning. Jev/Fast mode is optional.
- Native read-only iPhone HealthKit companion; owned public code is MIT. Keep
  third-party notices during allowlisted extraction.
- Repository name is **cesaregarza/health-buddy**, chosen by the owner. An
  already-running 64-bit Linux x86-64 host or ARM64 Raspberry Pi is the v1
  implementation target. Supported OS/version, minimum RAM/disk and physical
  Pi qualification remain installer/release gates, not verified support claims.
- Host and clients join an owner-controlled Tailscale network. The agent may
  run on a different host. Private connectivity, DNS/HTTPS reachability, clock
  validity and application authorization must pass installer preflight.
  Tailscale access alone does not grant application access.
- Public documentation hostname is **unselected**. CES-1078 may build relative
  links; CES-1079 deployment and Apple privacy/setup distribution are blocked
  until the operator selects and verifies a hostname and immutable site
  artifact. No example hostname or digest is a deployment decision.
- Runtime supports loopback HTTP for development and authenticated tailnet
  HTTPS for the product. Do not copy a personal IP/HTTP exception into public
  defaults. The existing mobile endpoint policy stays unchanged until its
  explicitly reviewed pairing/transport implementation ticket.
- Deferred: ChatGPT Work, Android, multiple users, generic plugin marketplace,
  new first-party connectors, preparing a bare Pi/OS, database rewrite, and
  mandatory Kubernetes/Postgres. User-created connectors/workflows are v1.

## Canonical ownership and module seams

Later extraction should follow these responsibilities; names may be adapted in
CES-1064 only with an explicit map that preserves each boundary.

| Boundary | Owner and authoritative inputs | Consumers |
| --- | --- | --- |
| `health_buddy/domain` | Validated records, units, time, provenance, revisions, errors | Every operation |
| `health_buddy/operations` | The only durable read/write transaction interface | HTTP API, CLI, MCP, jobs |
| `health_buddy/stores` | CSV/JSON manual records and definitions; SQLite HealthKit samples, batches and tombstones | Operations only |
| `health_buddy/identity` | Installation/dataset/restore identity, grants, pairing, revocation | Protected content and operations |
| `health_buddy/extensions` | Registry, compatibility, capability grants and state lifecycle | Views/metrics/connectors/workflows |
| `dashboard`, `adapters`, `jobs` | Presentation, transport and orchestration; no shadow data writes | Operations |
| `personal` | User additions, configuration, tests, notes and extension state | Versioned extension host |

Adapters preserve current file formats and source provenance. This contract
does not authorize a wholesale data migration. No dashboard, MCP tool, custom
connector or CLI directly edits canonical store files. For operations spanning
files and SQLite, a durable operation journal, lock and recovery protocol must
make committed revisions visible atomically; crash recovery must finish or
roll back a partial transaction before serving reads. Implementation proof is
CES-1066. The reference oracle has no disk/crash guarantee.

Generated dashboards, caches, reports and exports are rebuildable, not another
source of truth. An exported value includes its source identity and dataset
revision. Manual and device observations remain distinct. Daily HealthKit
activity uses HealthKit statistics, not a sum of overlapping source samples.

## Versions, identity and revisions

`contracts/v1/compatibility.json` is a versioned compatibility target. It is
deliberately **contract-only**: no code release, source SHA, runtime image or
site artifact has been published by this contract. A future release manifest
must bind each delivered component to actual immutable artifacts and pass
cross-component compatibility checks before activation.

- `installationId`: random UUID identifying a receiver installation. A new
  receiver gets a new value; an in-place restore may retain it.
- `datasetId`: random UUID identifying the logical set of authoritative health
  stores and personal workspace; preserved through a restore of that dataset.
- `restoreEpoch`: fresh random UUID on initial provisioning and **every restore**,
  including in-place restore. It is also the security epoch. It must be minted
  after reading the backup, never restored from it as the active value.
- `dataRevision`: monotonic nonnegative integer per active dataset epoch,
  committed with every successful canonical health mutation. Restoring an older
  snapshot may reduce it, which is safe only with a new restore epoch.
- `codeRelease`: a semantic release version and real artifact digest, separate
  from dataRevision. No data revision, private path or personal content enters a
  public release manifest. Storage schema, API, extension, pairing, tool and
  phone payload versions are distinct from code release versions.

Opaque IDs are identifiers, never authority. The namespace for a data revision
is the exact `(installationId, datasetId, restoreEpoch)` tuple. Clients must
invalidate cached reads, prepared writes, dedupe state and phone anchors when
that tuple changes. They must not silently use an old revision on a new epoch.

## Wire contract

The public application API is `/v1`. JSON names are camelCase; UTC/offset-aware
RFC3339 times, ISO local dates and explicit IANA timezones are distinct. Numeric
values must be finite, units explicit, and missing values null with a reason.
Reject unknown write fields, unknown enum values and unsupported major versions.
The fixtures use a bounded body-mass record to illustrate the canonical
envelope; CES-1066 adds domain schemas for the extracted operation inventory.

Protected reads and health writes require application authentication. Responses
use `{data, meta}` or `{error, meta}`. `meta` includes the identity tuple and
dataRevision once authentication succeeds. Unauthenticated failures expose no
private identity or health metadata. Error fields are `code`, `retryable` and
optional safe `details`; logs carry opaque request IDs and counts, not health
values, bearer tokens, pairing codes or full request bodies.

| Operation | Example and required grant |
| --- | --- |
| Capability discovery | `GET /v1/capabilities`, `records:read` or `sync:status`; device projection excludes health/owner data |
| Canonical bounded read | `GET /v1/records/{id}`, `records:read` |
| Create/update | `PUT /v1/records/{id}`, `records:write`, identity tuple, idempotency key, If-Match |
| Owner pairing intent | `POST /v1/pairing-intents`, `devices:manage` |
| One-time redeem | `POST /v1/pairings`, unguessable pairing secret plus exact receiver identity |
| Revoke device | `DELETE /v1/devices/{id}`, `devices:manage` |
| HealthKit ingest | `POST /v1/healthkit/batches`, device `healthkit:ingest` only |

Capabilities describe available operations/versions and advertise
`sourceStatusOperation: "projection.status"` for a `records:read` principal.
That separately admitted projection describes each currently granted source's
`availability`, `lastSuccessAt`, `freshness` and `missingness`, with measured
timestamps rather than a universal freshness claim. Empty fresh installations,
disabled optional sources, unavailable/error sources, stale data and unknown
HealthKit read authorization are distinct. HealthKit empty reads mean
`no_data_or_denied_read`, never proof of denied permission or a measured zero.
Requested time ranges, bounds and requested fields must constrain agent context;
provider transmission is explicit and previewable, not an automatic full dump.
The `sync:status` device capability response has no source status projection or
owner health data. Capability/CAS discovery never scans optional health history.

Every observation carries stable record/source IDs, source kind, observedAt and
receivedAt, typed value/unit, and provenance. Freshness states are `fresh`,
`stale`, `unknown`; missingness is null for a present observation or a typed
reason for absent data. Missing values are never substituted with zero.

Mutation intent is distinct from the stored observation. The example PUT accepts
only `kind`, `value`, `unit`, `observedAt` and a granted `sourceId`; record identity
comes from the validated path. The server assigns `receivedAt`, resolves source
kind from the authorized registry, and calculates freshness/missingness when
reading. Caller-supplied receipt timestamps, freshness, provenance objects or
other server-owned fields are rejected, not echoed as trusted facts. A source ID
must belong to the caller's explicit allowed sources; record write scope alone
does not permit impersonating a phone or another connector.

## Retry and revision invariants

Every health write sends `X-Installation-ID`, `X-Dataset-ID`,
`X-Restore-Epoch`, `Idempotency-Key`, and `If-Match: "rev-N"`. Identity and
precondition headers apply to canonical writes; HealthKit uses its own durable
batch identity and acknowledgement protocol below.

Validation order is authentication/revocation → grant/device binding → exact
identity tuple → request shape/version → idempotency lookup → revision
precondition → atomic mutation. Returning an old success must never bypass a
revoked credential or a changed epoch.

Persist the idempotency ledger with the mutation. Key scope is
`(actorId, datasetId, restoreEpoch, method, path, Idempotency-Key)`; compare a
digest of normalized typed JSON plus the original If-Match and API version.
Normalization ignores JSON object-key order and whitespace, preserves array
order, and uses one canonical finite numeric representation; duplicate JSON
keys are rejected before normalization. Retain keys for the entire epoch.
Identical retries return the original status/body/committed revision, even if
later writes changed the current revision; `Idempotency-Replayed: true` marks
the receipt. A changed request under the same key is `409 idempotency_conflict`
and writes nothing. A new key with a stale If-Match is `409 revision_conflict`;
read, resolve intentionally and submit a new key. No automatic overwrite.

Relevant errors: `401 unauthenticated`/`credential_revoked`, `403 forbidden` or
`device_mismatch`, `404 record_not_found`, `409 identity_changed`,
`restore_epoch_changed`, `revision_conflict`, `idempotency_conflict`,
`pairing_consumed`, `410 pairing_expired`, `422 invalid_request`,
`428 identity_required`/`revision_required`, `429 rate_limited`,
`503 source_unavailable`. Only explicit temporary failures (429/503, with
bounded Retry-After) are automatically retryable. Network ambiguity allows an
identical idempotent retry. A conflict requires resolution, not a blind retry.
Uncertain replacement history uses `409 reconciliation_required`; it blocks
canonical activation until reviewed rather than admitting ambiguous duplicates.

## Application and proxy security boundary

Owner setup uses a one-time bootstrap shown locally, then rotated into a
protected owner session/grant. Agents get separately revocable named grants;
devices get distinct per-installation tokens. Store high-entropy token digests
server-side; never log plaintext. Cookie sessions use Secure, HttpOnly and
SameSite, with CSRF protection and origin validation for browser mutations.

| Actor | Allowed grants | Explicit exclusion |
| --- | --- | --- |
| Owner | records read/write, extensions manage, devices manage, operations admin | No implicit HealthKit write capability |
| Agent health token | Explicit owner-approved records read/write | No code installation/activation, owner credential or device-management inheritance |
| Native coding-agent maintenance | Separate explicit owner-authorized source edit/install/enable boundary | Never obtainable by presenting an ordinary records token |
| Device | healthkit:ingest, sync:status for its bound device | No health reads, exports, arbitrary operation/job execution |
| Connector/workflow | Manifest's approved scoped operations | No inherited owner or agent privilege |

All static dashboard content, API reads and mutations share authorization;
unguarded static exports are prohibited. Health details never appear on a
public status endpoint. The backend accepts verified owner proxy identity only
from a configured local/private trusted proxy connection that strips inbound
identity headers and supplies a validated configured owner subject. Client
headers claiming a Tailscale user are ignored on all other connections.
Forwarded host/proto headers are similarly trusted only from that proxy.
The proxy credential-to-owner mapping must be explicit and tested; arbitrary
tailnet membership or a similarly named account does not match the owner.

Tagged Tailscale clients may have no user identity headers. They require an
application agent/device token; lack of an identity header neither authenticates
nor prevents an otherwise valid token from working. The fixture peer context
models a verified transport boundary and is never client-supplied JSON.

Extension maintenance is local owner-authorized code execution, separately
audited from ordinary MCP health operations. `extensions:manage` is a privileged
maintenance grant and must never be implied by `records:read` or `records:write`.
Scaffold/edit/install/enable cannot be reached through a routine health token;
remote maintenance, if offered later, requires a separately reviewed authorization
design. User-authorized native Codex/Claude source editing remains supported.

Compromise limits: a device token can inject/tombstone its device's allowed data
until revoked; it cannot prove physiological truth. An agent token may read or
write within its approved scope until revoked. Native user extension code is
trusted owner code, **not a hostile-code sandbox**; a compromised extension or
runtime may access everything its OS identity can. Isolation, least-privilege
mounts and secret grants reduce exposure but do not provide binary attestation.

## Pairing and restore recovery

The owner first creates a safe, bounded approval reservation; it is not yet a
redeemable intent. Explicit private owner handoff creates an intent bound to the exact receiver identity tuple, expiring
after **300 seconds**, with a CSPRNG secret of at least 256 bits. Only its digest
is stored. Redemption is rate-limited and atomically consumes it, binds the
device UUID, and returns a random device token exactly once over HTTPS. No token
or secret goes into a normal URL/query/log. Displayed QR/deep-link input is
treated as secret and must not send it to public sites or analytics.

Known expired redemption returns 410; a consumed intent returns 409 even for an
identical retry. If the response was lost, revoke the uncertain device grant and
create a new intent; never replay a plaintext device token from the server.
The phone stores the token in Keychain. Owner revocation takes effect before
the next authenticated request, including duplicate batch retries. A re-pair
must explicitly replace a prior binding; it cannot silently switch datasets.
Owner revocation also invalidates outstanding replacement reservations/proofs.
Re-pair after revocation requires a new owner decision. Bounded cleanup removes
old reservations; a purged or unknown proof receives an authentication failure.

Every restore invalidates all restored tokens, sessions, pairing intents and
grant caches by rotating the restore/security epoch. Bootstrap locally and
issue new grants; do not resurrect a token revoked after the backup. Preserve
dataset ID and source record IDs/tombstones, but regenerate receiver identity
as appropriate. Phone anchors, pending batches and aggregate hashes are scoped
to `(installationId, datasetId, restoreEpoch, deviceId, HealthKit type)`. After
re-pairing a restored receiver, reconcile from available history before setting
new anchors; source history that is no longer available is an explicit gap.

## Existing HealthKit protocol and additive negotiation

Keep the existing schema-v1 body and its `schemaVersion`, `batchId`, `deviceId`,
`generatedAt`, `records`, `deletions`, and `X-Health-Device-ID` contract. Keep the
existing read-type and metadata allowlist; no clinical records, routes/location,
nutrition or HealthKit writes. Do not copy private endpoint defaults.

The Health Buddy receiver requires negotiated pairing protocol 1 and exact
identity headers on every ingest. Missing identity is 428; mismatched identity
or restore epoch is 409. It must not quietly fall back to the old receiver
behavior. An old app has **not** acquired these guarantees merely because the
body schema remains 1: CES-1075 implements negotiation and scoped sync state.
Protocol incompatibility must be shown before a new installation is activated.

Persist each `(deviceId, datasetId, restoreEpoch, batchId)` and canonical payload
digest. Exact duplicates acknowledge the same batch without double writes;
different payloads under a batch ID conflict. Validate and commit the entire
batch atomically. Raw delivery uniqueness is `(deviceId, recordId)` in its
dataset; canonical observation identity is `(sourceStreamId, recordId)`.
`sourceStreamId` is receiver-owned and bound during owner-approved pairing;
the client cannot claim another stream. Re-pair/token rotation retains the
stream. Replacement-phone pairing must explicitly select the predecessor stream
and retire its old writer before the new writer can activate. Identical stable
HealthKit record IDs and normalized payloads reconcile to one canonical
observation while retaining both delivery provenances. A different payload for
one native object ID is a visible reconciliation conflict. Aggregate updates
use their documented deterministic type/date/timezone identity and revision,
never add overlapping totals.

If replacement history has changed IDs or lacks enough identity/provenance to
prove sameness, stage the overlapping interval as unresolved; do not automatically
add both histories to canonical metrics. The owner must reconcile or choose a
source/window before activation. Missing HealthKit history is an explicit gap,
not proof of complete recovery. CES-1076 must implement these replacement-phone
invariants. Deletions are durable stream-scoped tombstones, including for records
not yet delivered, and prevent delayed replay or replacement from resurrection.

Acknowledgements retain `status: accepted`, matching batchId,
recordsAccepted/deletionsAccepted and duplicateBatch. A new batch acknowledges
all supplied objects/deletions. A duplicate acknowledges either the original
full counts or zero/zero; never partial counts. This legacy success body is an
explicit exception to the general data/meta envelope: negotiated successful
responses carry `X-Installation-ID`, `X-Dataset-ID` and `X-Restore-Epoch` headers.
The canonical receiver retains the original accepted receipt unchanged. For a
duplicate HealthKit batch it derives a deterministic acknowledgement with
`duplicateBatch: true`, the original full counts and exact receiver tuple
headers. This is the HealthKit-only replay-body exception; generic writes replay
their original body byte-for-byte. Duplicate acknowledgement does not mutate
records, stream metadata, tombstones or dataRevision, and still requires current
authentication, device/source binding and the active identity epoch.
Before advancing any anchor, the phone validates those response headers against
its paired tuple as well as the body batch ID/status/counts. Missing or wrong
response identity is not an acknowledgement, even with otherwise correct counts.
The phone persists anchors only after that complete acknowledgement. Existing
cesar-health-sync PR #2 supplies reliability foundations; reuse them, do not
replace them. Its Linux contract success does not prove physical-device behavior.

## Compatibility, migration and rollback

Minor API changes are additive for reads only; old clients ignore documented
unknown read fields. Writes remain strict. Changing a required field, scope,
state meaning, identity rule or existing result behavior requires a new major
contract and an explicit migration. Publish deprecation notices with replacement
interfaces and a minimum of two stable releases of overlap; never remove an
interface that an installed enabled extension still needs without a preflight
conflict. No release timing is promised by this policy.

Preflight validates runtime/installer/agent/phone/extension compatibility before
changing active pointers. Stage migrations against a consistent encrypted
snapshot; record exact schema versions, artifact/source IDs and extension
migration plans. Do not mix old/new stores, activate one half of a tuple, or
allow two receiver instances to write the same dataset. Failed migration leaves
the old runtime and workspace intact. Incompatible extensions block activation
until remediation or explicit owner-approved disabling; their files/state stay
preserved and visible. They are never silently disabled or deleted.

Rollback with schema-compatible data switches the runtime pointer only after
compatibility checks. Otherwise restore the **whole** pre-upgrade snapshot,
mint a new security epoch, re-pair devices and explicitly reconcile any later
writes. Never run an older binary against a newer unsupported schema or discard
post-snapshot writes silently. Live migration/cutover and rollback are separate
operator actions, not consequences of publishing a PR.
