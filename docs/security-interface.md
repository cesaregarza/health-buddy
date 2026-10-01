# Security implementation interface

This document describes the CES-1067 security owner and transport interface.
The accepted health Operations API and immutable health receipt bytes remain
separate from security state and one-time private replies. Execution evidence
comes from recorded check runs, not from this source map.

## Ownership and factories

Core owns `security_api.py`, configuration, durable security state/policy,
`security_runtime.py`, canonical provisioning and CLI. Transport owns HTTP and
`production_server.py`, `client_workflow.py`, App authentication adaptation,
browser UI and its wire/browser tests. Shared files are edited by their named
owner; there is no new authentication framework or dependency.

Core supplies `security_runtime.open_runtime(workspace, *, development=False)`
returning `Runtime(operations, security, ingress, proxy_boundary)`. It is invoked
inside the serving child. The supervisor reads only validated `Config.ingress()`
and calls `serve(runtime_factory, *, ingress, port=8791, development=False)`.
The child verifies the constructed ingress equals the immutable bind plan.
No live SQLite connection or lock crosses the process boundary.

`App.authenticated(root, *, proof: BearerProof, runtime: Runtime | None = None)`
opens one Runtime if needed, authenticates, and injects `security.describe` into
the native workflow. CLI health commands use an explicit private credential
file, never token argv or ambient credential lookup. App health data still flows
only through Operations.

`ClientIdentity` contains a stable opaque actor binding, security epoch and the
exact installation/dataset/restore tuple. It is current authenticated self data,
not authority. Native pending format2 binds this value and survives authorized
same-actor credential rotation. Legacy production format1 handle hashes cannot
prove actor mapping: preserve/refuse pending state until deliberate acknowledged
resolution, never rewrite its health request/key/CAS to make it pass. Explicit
development retains its separate compatibility path.

Authenticated handles are process-local, credential-deduplicated, capped at256
and expire after300seconds of inactivity; they never replace current SQLite
credential/actor/epoch checks. A long-lived App retains only its explicitly
supplied BearerProof in memory and reauthenticates before operations and self
description. Revoked/rotated old proof fails until the caller deliberately
supplies the replacement. No automatic credential-file reload or ambient lookup.

## Finite security requests

All incoming fields are validated again by the Security owner. Unknown fields,
wrong DTO/action combinations, malformed IDs and noncanonical shapes are denied.
Security JSON is capped at16KiB, no compression; secrets are bounded strings and
are excluded from reprs, logs, error details and ordinary reply data.

| Action and HTTP route | Request | Successful reply / admission |
| --- | --- | --- |
| bootstrap.redeem, POST /v1/bootstrap | BootstrapProof; exact Identity | One-time owner-token; no cookie. Local setup proof only. |
| session.create, POST /v1/sessions | BearerProof or ProxyProof | Fixed session CookieDirective + ClientIdentity; owner only. |
| session.get, GET /v1/session | Current authenticated principal | ClientIdentity; only a session also receives tagged csrf delivery. |
| session.revoke, DELETE /v1/session | Current session principal | Cookie clear and safe outcome. |
| grants.create, POST /v1/grants | AgentGrant; exact Identity | Safe ID/name/scope metadata + one-time agent-token; owner only. |
| grants.list, GET /v1/grants | No payload | Safe inventory, never digests/tokens; owner only. |
| grants.rotate, POST /v1/grants/{id}/rotate | Resource ID; exact Identity | New one-time same-actor token, retire selected prior credential; owner only. |
| grants.revoke, DELETE /v1/grants/{id} | Resource ID; exact Identity | Safe revocation outcome; owner only. |
| pairing.create, POST /v1/pairing-intents | PairingReservation; exact Identity | Safe approval reservation ID/URL/status; devices:manage owner. |
| pairing.status, GET /v1/pairing-intents/{id} | Resource ID | Safe status; devices:manage owner. |
| pairing.handoff, POST /v1/pairing-intents/{id}/handoff | Resource ID; exact Identity | One-time private pairing-proof,300second expiry; owner only. |
| pairing.redeem, POST /v1/pairings | PairingRedemption + exact Identity | One-time device-token to the redeeming phone only. |
| devices.list, GET /v1/devices | No payload | Safe receiver-owned inventory; devices:manage owner. |
| devices.revoke, DELETE /v1/devices/{id} | Resource ID; exact Identity | Safe revocation outcome; devices:manage owner. |

Grant JSON is `{name, grants, sourceIds, readSources, readKinds, readFields}`;
bounded arrays are explicit, null read restrictions mean all approved, empty
arrays mean none. Agent grants are an explicit subset of records:read,
records:write and providers:invoke. They cannot inherit owner administration,
device management, HealthKit upload or maintenance authority. Source IDs must
already belong to the canonical registry. Owner role has no implicit upload
grant. A device is bound to one server-owned source/stream and receives only
healthkit:ingest/sync:status.

Reservation JSON is `{name, replacementDeviceId?}`. Redemption JSON is
`{proof, deviceId, protocolVersion}`; protocolVersion must be1. Identity always
comes from exact untrusted receiver tuple headers, never inferred from a secret.
Proof DTOs are selected by the finite route, not caller-controlled role strings.

`SecurityReply.data` is safe JSON; `client` is authenticated self metadata.
`cookie` and `secret` are separate typed private channels. Transport explicitly
serializes the expected secret kind for each action; it never serializes a DTO
or Authenticated wholesale. Session CSRF is the sole repeatable secret channel,
only to its current session. Owner bootstrap UI pauses for deliberate private
retention of the returned owner token before creating a session. The page holds
it only in memory, never localStorage or a health retry ledger. A lost sole
credential requires deliberate local recovery.

Native `security bootstrap --proof-file PATH` writes a create-only private JSON
bundle with exactly `{proof, identity: {installationId, datasetId, restoreEpoch},
protocolVersion: 1}`. Public login reveals no receiver tuple; the owner explicitly
supplies this private bundle. Pairing reservation/status returns `{id, status,
expiresAt, approvalPath}`. Handoff adds `identity` and `protocolVersion` to those
safe fields and delivers the proof separately. Status is awaiting_owner, ready,
consumed, expired or revoked. Device/agent inventories expose named grants,
write `sourceIds`, and exact `readSources`/`readKinds`/`readFields`; null means all
approved and an empty array means none. They never include credential digests.

The mutually exclusive fresh native setup option is
`security bootstrap --owner-token-file PATH`, a raw owner token in a create-only
mode0600 file. It refuses existing/incomplete authority exactly like proof
bootstrap and does not require browser HTTPS. Browser login requires explicit
canonical externalOrigin and separately configured HTTPS ingress; default null
origin deliberately denies it. See [owner setup](authorization.md).

For loopback app-credential login after logout/expiry, the owner retains an
explicit private credential handoff outside browser storage. If the browser-only
bootstrap consumed the owner's sole proof and it was not retained, deliberate
native OS-owner recovery is available:
`health-buddy --workspace PATH security recover --confirm-revoke-all --owner-token-file PATH`.
It rotates the security epoch, revokes all old security materials and writes the
new owner token once to a create-only0600 file. Existing health data is preserved.
There is no HTTP recovery route or ordinary health-token maintenance escalation.

Cookie is fixed `__Host-health-buddy`, Secure/HttpOnly/SameSite=Strict/Path=/,
no Domain. Session proof carries optional CSRF; the security owner validates it
against that session and returns `csrf_verified`. Transport requires it and exact
configured Origin for cookie mutations. Login/proof routes also require strict
Origin/custom-header protections; bearer phone requests use their explicit proof
path. Cookie+bearer and duplicate credentials fail instead of choosing authority.

## Trusted ingress and roles

Optional `security` configuration adds ingress, externalOrigin, ownerSubject,
socketPath and sessionSeconds to schema1 without rewriting existing files.
Defaults remain loopback, no external origin/owner mapping and3600second session.
Security settings never appear in Config.public or health context.
`externalOrigin` must already be a canonical browser HTTPS origin: lowercase
ASCII DNS or canonical IP literal, no default443 port, credentials, path, query,
fragment, whitespace, escapes or ambiguous numeric-host spellings. Invalid
configuration is rejected with an actionable error; it is never silently rewritten.

Loopback TCP never accepts proxy identity. Header mode binds only a Unix socket
under a non-symlink service-owned0700 security directory; socket0600, restrictive
umask before bind, no concurrent TCP listener. The serving child validates actual
socket type/owner/mode/path and its pinned server's actual UDS connection evidence.
Only then can transport use the Runtime's opaque instance-bound proxy capability.
A forged ProxyProof object/subject or caller JSON cannot supply this capability.

Only POST /v1/sessions uses exact configured ASCII ownerSubject from the isolated
proxy. Header mapping is not used on health routes, so logout/revocation cannot
silently recreate owner authority. Tagged clients use app credentials. Tailscale
UDS forwarding rewrites Host to localhost; protected forwarded host/proto must
match configured externalOrigin after boundary verification. Reject Funnel and
non-ASCII/encoded owner subjects in v1. Same service UID/root remain trusted
native maintenance, not a hostile-code sandbox or distinct process attestation.
Actual proxy deployment and patched Tailscale qualification belong to1068.

## Durable authority, provisioning and recovery

One `security/authority.sqlite` plus required epoch and operations-side binding
markers owns digest-only
credentials, sessions, active intents, grants and bounded denial/rate state.
Missing/corrupt metadata fails closed, never silently bootstraps again. The
operations-side marker distinguishes a lost security store from fresh setup.
Only the
local owner bootstrap/recovery entrypoint can establish/rotate authority. Every
credential/session/intent carries active security epoch and exact receiver tuple.

All security mutations, audit/denial counters and rate buckets acquire the
workspace lock before the cross-process security lock. Rate buckets use a fixed
finite action/global key set, never unbounded attacker-derived rows. Current
policy guard stays held through health COMMIT_INTENT; revocation uses the same
order, so cached receipt replay rechecks current grant and epoch. Preflight is
preliminary only and must not acquire locks in reverse order.

Approval reservations are not redeemable intents. They have bounded lifetime,
count and cleanup. Private owner handoff creates a256bit proof, stores only its
digest, starts exactly300seconds and returns the proof once. Redemption consumes
it atomically and persists an inactive credential plus provisioning reference.
Under the same workspace lock, the coordinator calls the private
`Service._provision_device_locked(binding: DeviceBinding, *, identity, deadline)`
to install/reuse only that server-selected source/device/stream through the
canonical journal. It activates the credential only after matching provisioning.
Do not call public register_source recursively while holding these locks.

Interrupted provisioning remains consumed/inactive and never recovers plaintext
or automatically activates an orphan. A new explicit owner intent may reconcile
the known dormant binding. Same-device re-pair requires the owner-selected prior
device in the reservation, preserves source/stream and retires its writer. A
client claiming an existing UUID cannot select that predecessor. Different-device
or history replacement returns409 reconciliation_required for CES-1076.

The security database/epoch marker/rate state are required backup inventory,
and the existing workspace backup lock quiesces them. The later restore owner
must rotate epoch and invalidate every restored credential/session/intent,
including a grant revoked after the backup; this ticket supplies the explicit
native rotation hook, not restore execution or qualification.

## Primary references and evidence boundary

- [Python secrets](https://docs.python.org/3.12/library/secrets.html): use explicit
  token_bytes(32)/token_urlsafe(32), not the changeable default entropy.
- [Python hashlib](https://docs.python.org/3.12/library/hashlib.html): SHA-256
  digests for generated high-entropy secrets; no user-password authentication.
- [SQLite transactions](https://www.sqlite.org/lang_transaction.html) and
  [synchronous settings](https://www.sqlite.org/pragma.html#pragma_synchronous):
  explicit write transactions/durability configuration; crash tests remain
  process evidence, not physical power-loss certification.

Current UDS/Serve primary-source evidence and precise transport admission rules
are owned by the transport lane. Runtime, concurrency, secret scans, actual UDS
negative cases and all regressions run on a frozen integrated commit. No
production/device/deployment claim yet.
