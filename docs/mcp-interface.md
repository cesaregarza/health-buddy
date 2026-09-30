# Local shared-tools interface checkpoint

This document defines retry storage and admitted discovery for the local MCP
adapter. The SDK server, bounded framing and private HTTP client are implemented
in source; [adapter usage](mcp-adapter.md) describes their finite tool interface.
Queue receipts establish tested commits and outcomes. Neither this interface nor
a source checkpoint qualifies a Codex/Claude host setup or a released artifact.

`GET /v1/workspace/discovery` maps to `workspace.discover`, requires current
`records:read`, and accepts no query or payload. The ordinary API envelope binds
the result to receiver identity and revision. The 32 KiB finite result contains
interface versions, permitted operation names, fixed relative documentation
references and currently ready extension descriptors admitted by read sources,
kinds and fields. Symbolic notes/test references do not assert those personal
files exist or disclose their contents. No extension code is imported or run.
An extension with multiple source bindings may be visible when one binding is
currently admitted. Discovery exposes neither those bindings nor their count;
each later metric invocation independently authorizes its selected source.
Disabled, invalid, unreviewed or non-admitted entries are omitted without hidden
counts. This is explicitly not the complete owner inventory. Built-in discovery
survives optional registry failure; it never calls native workspace inventory or
builds a health projection. Writing-only clients retain health-free capabilities
without receiving discovery authority.

`ReleaseIdentity` is optional trusted-factory input to `Service`. Default fields
are unknown. The serialization boundary accepts only finite package versions
and a complete, syntactically valid source evidence tuple. Actual bundle
verification belongs to the packaging reader; constructing a DTO proves nothing.
No request header, environment value or owner configuration string becomes
provenance. Even an offline verified image descriptor does not establish the
running process's image, so runtime artifact/platform/release fields remain null.
Document references are interface version 1 references, not a released-product
claim; docs/source hashes remain unknown until validated bundle evidence exists.

The remote adapter uses an explicit private `RetryRoot`, not `Config`,
`initialize`, `open_runtime`, or another local health store. Its existing parent
must be private and owned by the current UID; setup creates only the retry root.
Symlinks, hardlinks and group/world-readable files are refused. The operator
selects a bounded local client profile; a tool supplies a durable action ID
independent of the protocol request ID. Hashed profile/action paths avoid
interpreting either as a filesystem path. At most 8 profiles and 1024 retained
intents per profile are admitted, each at most 1 MiB. Capacity refuses new
intents; it never evicts evidence.

`McpWorkflowNamespace` reuses the existing request envelope, exact receipt and
cursor machinery. Format 2 records carry the authenticated stable actor, security
epoch, receiver tuple and exact namespace. The original method, route, payload,
CAS revision and idempotency key persist before dispatch. Same ID plus changed
intent conflicts, including after success. Same-actor credential rotation may
replay the original envelope; another actor or epoch cannot inspect or replay it.
Corrupt and legacy state remains retained. Status excludes the payload; MCP has
no automatic discard, rekey or arbitrary maintenance operation.
The adapter includes all normalized tool input in the intent digest; the
delayed payload builder is for preserving an already prepared action, not for
changing its contents under the same action ID. Inspection and retry of an
unknown action use a non-creating lookup under state.lock. They allocate no
profile or action slot. Only write preparation reserves a slot; lookup of an
existing reservation waits on its existing action lock.

The retry root is client-owned state. A backend backup does not include a separate
client root, and placing it under a backend directory alone does not make the
server's backup lock cover its writes. Backup must hold the client's `state.lock`
while copying its files and must preserve the original root and profile identity
on restore. The per-intent lock serializes actions; brief `state.lock` intervals
protect directory barriers and file publication. No state lock is held during
HTTP or authority admission. A backup may retain a pending envelope while its
request is in flight; replay reconciles its outcome after restore. Missing client
state is not permission to fabricate replacement keys for uncertain writes.

The separate optional `health-buddy-mcp` entrypoint supplies finite tools and
bounded private HTTP/stdio adapters. The repaired D2 queue run at ab9d3853 passed
112 cases plus lint and configured type checking. Its one remaining failure
exposed source-status grouping during the fresh-session scenario; that later
path and the grouping repair need an exact subsequent receipt. Full configured verification, complete bundled
dependency notices and actual host-client qualification remain separate gates.
See [fresh-session and native customization guidance](mcp-adapter.md).
