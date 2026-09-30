# Local MCP adapter

This adapter is being verified for CES-1072. The source checkpoint is not an
installation or release qualification. Installable artifacts and host-specific
Codex/Claude Code guidance have separate verification gates.

The optional `mcp` dependency group supplies the maintained SDK and HTTPX2. The
dedicated `health-buddy-mcp --settings /absolute/private/adapter.json` command
serves one local stdio connection. No remote MCP listener, provider API key,
model call, exporter, shell tool or owner maintenance endpoint is provided.
The SDK handles protocol versions and cancellation through its public stream
API; the adapter adds bounded strict UTF-8 framing and isolates protocol stdout.

The operator supplies a private, owned settings file (0600, parent 0700). Its
version-1 fields are `schemaVersion`, canonical HTTPS `origin`, exact `identity`
(`installationId`, `datasetId`, `restoreEpoch`), absolute `credentialFile`,
absolute `retryRoot`, stable `clientId`, explicit `writeSources` and
`acknowledgeAiEgress: true`. Optional `caFile` supplies a private PEM trust anchor.
The credential is an agent bearer token in a separate private file; owner and
phone credentials are refused. There are no credential argv/env fallbacks.
TLS verification is required, redirects/cookies/compression and ambient proxy
settings are refused. The endpoint and receiver tuple are fixed by these
operator settings; tool arguments cannot change them.

Selected health tool responses reach the AI host the user chose. That host's
handling of the supplied context is outside this local adapter. Reads require
explicit scopes or record window/filter selection; discovery does not read an
entire personal workspace. Missingness, provenance and truncation remain in the
API result. If a result exceeds a tool budget, select a narrower supported
window/filter; the adapter does not silently truncate or claim an empty result.

The finite tools discover the workspace, get selected context, list one record
page, get the plan, inspect source status, log a typed observation, save a
completed workout, propose/apply a plan, and inspect/retry an existing write.
Tool listing rechecks current authenticated capabilities. Current server policy
remains authoritative on every operation and completed receipt replay. Local
write source selection is an additional restriction, not a grant.

Writes require an explicit stable `intentId`, receiver tuple and
`expectedRevision`. Dates and timestamps are explicit schema fields; the adapter
never regenerates them on retry. Before sending, it retains the exact original
body, method, route, API version, identity, CAS revision and key. Repeating an
identical intent replays it; changed content under the same ID is a conflict.
An uncertain response is not permission to generate another ID. Use
`write_status` and `retry_write`; current actor/security-epoch/receiver binding is
checked before exposing status or replaying. Credential rotation within the
same authenticated actor is compatible; revoked old credentials are not.

Plan proposals retain the supplied plan body and original CAS without a health
mutation. Review that body and the returned digest before explicit apply.
Structural validation is local; canonical semantic validation remains pending
until apply. An outdated CAS is a conflict, never silently refreshed. Local
capacity, corrupt state and changed identity require deliberate owner
reconciliation outside everyday MCP tools. Retained actions are never evicted,
discarded or automatically rekeyed by these tools.

The explicit retry root is client-owned storage. Opening it does not initialize
a backend, security authority or second health store. It supports eight client
profiles, 1024 retained request slots per profile and 1024 proposal slots per
profile. Each request is at most 1 MiB and each proposal at most 384 KiB.
Back up all of this root while holding its `state.lock`; remote server backup
does not cover it. A co-located adapter still needs this explicit backup
boundary. Restored pending state replays its original request; it does not
infer that restored client state undoes a committed server mutation.

Input lines are at most 384 KiB/depth 32 with eight outstanding protocol IDs.
Only one service job runs at a time. Its waiter has a 35-second deadline; an
already admitted job is not abandoned when the caller disconnects or cancels.
Shutdown allows 50 seconds for retained jobs; a forced process termination can
leave a pending outcome for replay. Ordinary API writes are limited to 64 KiB,
plans to 256 KiB, API responses to 4 MiB, tool results to 64 KiB and selected
context to 32 KiB. Output protocol frames are limited to 160 KiB.

Discovery contains bounded current-policy metadata and logical documentation
references. It does not expose native private inventory, absolute parent paths,
secret references, hidden extension counts or unverified extension names. The
fixed `health-buddy://adapter/v1` resource describes this adapter only. Remote
source/docs/image identity is unknown unless the backend supplies separately
verified evidence; an arbitrary config string does not establish an artifact's
identity or bind it to the running process.

At this source checkpoint, authored tests cover model API retry/proposals,
actual private-state backup locking, strict settings, SDK model framing and
safe error projection. Actual SDK wire/HTTPS-authority integration, cancellation,
stdout/no-egress/telemetry behavior and full dependency component notices remain
pending queue verification. Neither direct model tests nor wheel metadata are
substitutes for those gates.
