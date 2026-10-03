# Owner, agent and device authorization

The installed source runtime uses one security authority alongside the canonical
health coordinator. Without explicitly initialized security it denies protected
dashboard, assets, context and API operations. The separate `--development`
mode remains a deliberate loopback development tool; it is not a login bypass for
an authenticated listener. Installer/proxy deployment and real-phone acceptance
remain later delivery gates.

For native use, initialize the private workspace and create a retained owner
credential directly. These commands are OS-owner maintenance, not actions for a
routine health token; they work without TLS, Tailscale or a model provider:

```sh
health-buddy --workspace /path/to/private-workspace init
health-buddy --workspace /path/to/private-workspace security bootstrap \
  --owner-token-file /path/to/private-workspace/secrets/owner-token
health-buddy --workspace /path/to/private-workspace \
  --credential-file /path/to/private-workspace/secrets/owner-token status
```

This is fresh setup only: existing or incomplete authority state is refused,
never implicitly recovered. The raw token file is create-only mode0600.

Browser sign-in additionally requires deliberately configured HTTPS ingress.
The default `security.externalOrigin: null` rejects browser login origins. For
example, the following **synthetic placeholders** describe the security part
of an owner configuration for an independently wired private Tailscale proxy:

```json
{"ingress":"tailscale-uds","externalOrigin":"https://synthetic.example.invalid",
 "ownerSubject":"synthetic-owner@example.invalid","socketPath":"security/http.sock",
 "sessionSeconds":3600}
```

Use the actual canonical HTTPS origin and exact verified owner subject when an
operator configures the proxy. Source commands do not create HTTPS certificates,
Tailscale Serve wiring or a trusted connection. Once
that ingress is configured, `health-buddy --workspace PATH serve` starts the
private backend. An existing retained owner credential can log in there.

Alternatively, a fresh browser-first setup can choose
`security bootstrap --proof-file /path/to/private-workspace/secrets/bootstrap.json`
instead of `--owner-token-file`. The flags are mutually exclusive. This
create-only mode0600 JSON file contains a one-time proof and exact receiver tuple;
paste it only into this workspace's login form. The proof has256bits of entropy
and expires after300seconds. The server stores only its purpose-separated digest.
No proof appears in a URL, shell argument, ordinary log or health receipt.
The browser's private bootstrap response delivers an owner token once. Retain
that credential deliberately in a private owner file before navigating away;
routine login after logout or session expiry uses this retained credential.
Browser localStorage and URLs never store it. Session cookies are host-only,
Secure, HttpOnly and SameSiteStrict. Cookie mutations require the session's CSRF
proof and exact admitted origin. Session CSRF validation is per presentation.

Native health commands require the explicit private token file:

```sh
health-buddy --workspace /path/to/private-workspace \
  --credential-file /path/to/private-workspace/secrets/owner-token status
```

The file must be an existing owner-owned regular mode0600 file in a private
directory, with one bearer value and optional final newline. It is not searched
through environment variables or ambient tool credentials. Authenticated native
clients recheck credentials before each operation. Same-actor agent rotation
preserves the actor binding and original pending health request; another actor
or epoch cannot inherit it. Legacy handle-based pending files remain preserved
and blocked until deliberate resolution. See [client workflows](canonical-clients.md).

If the only owner credential is lost in loopback mode, deliberate OS-owner
recovery is available. It is not ordinary re-login and invalidates all credentials,
sessions, intents and cached handles, including phone and agent access:

```sh
health-buddy --workspace /path/to/private-workspace security recover \
  --confirm-revoke-all \
  --owner-token-file /path/to/private-workspace/secrets/new-owner-token
```

Recovery leaves health records, identity and canonical receipts unchanged. Output
must be a new private file; it is never overwritten or printed. Interrupted
setup/recovery may leave an empty output file and incomplete security metadata;
admission fails closed and another explicit recovery with a new output path is
required. Missing authority state never starts automatic bootstrap.

Owner grant inventory distinguishes write sources from read source/kind/field
restrictions. An empty read list means none; null means all explicitly approved.
Ordinary agents may receive records:read, records:write and explicit
providers:invoke. Provider configuration must also be enabled; no credential
grants enable an otherwise disabled provider. Agents cannot manage devices,
install/activate code, register arbitrary sources or assume owner authority.
Native Codex/Claude maintenance remains separately owner-authorized trusted code.

Pairing starts with an owner approval reservation, bounded to900seconds and64
outstanding rows. Its URL and status contain no secret. Explicit owner handoff
creates the256bit redeemable proof and starts the300second lifetime. The phone
alone receives the device token on one successful redemption. Consumed proof
returns409; a still-retained expired proof returns410. Unknown, revoked or purged
proof fails authentication. Owner revocation invalidates outstanding replacement
reservations/proofs as well as current credentials. A new explicit owner
reservation can re-pair that same device UUID, preserving source/stream; another
phone/history returns reconciliation_required for the later replacement workflow.

Consumption first persists an inactive credential and server-selected binding;
canonical provisioning runs under the same workspace→security lock order, then
activation commits. A crash before activation cannot admit an unprovisioned
credential. A completed activation can lose its one-time response: no plaintext
is recovered or re-delivered, and the owner explicitly re-pairs. Process hard-exit
tests do not establish hardware power-loss or physical-device qualification.

Security state is `security/authority.sqlite`, `security/epoch.json` and the
independent `operations/security-binding.json`. High-entropy credentials,
sessions and pairing proofs persist only as domain-separatedSHA256 digests;
these are generated secrets, not human passwords. Denied-attempt state uses
three fixed global budgets, not attacker-selected per-peer rows: authenticate240,
public60 and security120 admission units per60seconds. Preflight and execution
can each consume a unit. Actor count is bounded128, live credentials512,
owner sessions32, and safe action audit history256. Handles are process-local,
capped256 and expire after300seconds inactivity; a guessed credential row ID
is never an authenticated handle.

Native backup context holds the canonical writer lock, which also quiesces
security mutation/rate state. Back up the **whole private workspace**, including
personal code/config/tests/state and security inventory. Explicit recovery
preserves an old authority and its recognized SQLite sidecars in a private
`security/retired-*` directory before installing the replacement. A multi-file
quarantine is not atomic: interruption preserves subsets and an incomplete
inventory, and admission stays closed. There is no automatic old-authority
restoration or arbitrary sidecar cleanup. SQLite can replay a hot journal before
reading a database, so old sidecars must never be attached to the replacement.
[SQLite locking and hot-journal recovery](https://www.sqlite.org/lockingv3.html)
explains that boundary. Restore qualification and mandatory epoch invalidation after an operational
restore remain separate requirements.

Trusted Tailscale owner headers are accepted only to create an owner session over
the explicitly configured private Unix socket. Loopback TCP never accepts them.
The service-owned parent is0700, socket0600, with no concurrent TCP listener;
the child verifies the actual connection/socket boundary. Exact configured owner
subject and forwarded origin must match; Funnel is rejected. Tagged clients use
their own bearer credentials. Same service UID/root are trusted maintenance,
not distinguishable hostile processes. Actual Tailscale deployment/version and
device behavior still require their separate qualification gates.

See [security DTOs and routes](security-interface.md) for module contracts.
