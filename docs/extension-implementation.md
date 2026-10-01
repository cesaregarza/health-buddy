# Extension API 1 implementation

The installed descriptor is validated against
[`extension_manifest.schema.json`](../src/health_buddy/extension_manifest.schema.json).
[`extension_api.py`](../src/health_buddy/core/extension_api.py) declares finite DTOs and
limits. The `{manifest,cases}` documents under `contracts/v1/examples` and their
wrapper schema remain abstract protocol conformance examples. They specify the
metric, provenance, missingness and retry semantics; they are not installable
package descriptors or a second runtime. Executable packages and their tests
are under `src/health_buddy/reference_extensions`.

Descriptor schema version, extension API major, semantic extension version and
owned state schema are separate. API 1 accepts exactly `metric-view` (Python
`metric`, JavaScript `view`) or `connector-workflow` (Python `connector` and
`workflow`). Dependencies name an installed extension and exact version; no
package manager or remote resolution runs. Activation rejects missing, cyclic
or incompatible dependencies and changed state schemas. It never migrates state.

## Modules and private ownership

| Module | Responsibility |
| --- | --- |
| `extension_manifest`, `core/files` | Strict descriptor and bounded nonfollowing file inventory |
| `extension_registry` | Native reviewed activation, digest snapshots, compatibility, disable and revert |
| `extension_runner`, `extension_worker` | Bounded schema/pure Python hook execution |
| `extension_views` | Canonical caller-filtered metric inputs and exact reviewed view asset |
| `extension_prepare`, `extension_jobs` | Explicit scoped preparation and retained source-event jobs |
| `client_workflow` | Original request/receipt persistence shared with native clients |
| `personal_workspace`, `extension_cli` | Native inventory, recorded fork metadata and maintenance commands |
| dashboard `extension-worker.js` / template | JavaScript ViewSpec worker and escaped built-in rendering |

All owner code, assets, configuration, tests, notes, migrations and state live
under `personal/extensions/ID/`, outside release source. The registry is
`personal/extension-registry.json`; immutable reviewed runtime/config copies
are under `personal/extension-reviews/ID/DIGEST/`. Unknown personal files are
retained and inventoried. Neither registry nor status discovery imports owner
Python/JavaScript or evaluates owner schema patterns.

An installed extension has 128 runtime files, 256 runtime directory entries,
1 MiB runtime bytes and depth 8 at most; the registry admits 32 installed entries
and 32 retained reviews per entry. Installation separately bounds the complete
package at 512 entries, 8 MiB and depth 10. Source, assets, manifest and config
are review-bound. Edits there become `needs_review`; edits to tests, notes,
migrations or authoritative state remain durable without silently enabling
new runtime code. Descriptors/configuration reject unknown shape and oversized
input. Broken optional entries have safe diagnostic codes and leave built-in
logging available.

## Current read and view boundary

The finite canonical operations are `extensions.list`, `extensions.read` and
`extensions.asset`, exposed as GET `/v1/extensions`, `/v1/extensions/{id}` and
`/v1/extensions/{id}/view.js`. They require current `records:read` authority.
Metric and view-asset admission enforce the caller's source, kind and field
restrictions against the selected extension input. No owner-wide derived cache
can widen those restrictions. The external worker bootstrap asset is also
protected, at `/extension-worker.js`. There is no HTTP install, enable, disable,
revert, native inventory, prepare or job execution route.

Metric reads accept `sourceId` and optional `from`/`to`. Both endpoints of an
explicit window are required; at most seven selected local calendar dates are
included. The default is today and the preceding six local dates. Multiple
source bindings require an explicit selection; overlapping sources are never
silently summed. Inputs contain at most 500 canonical rows plus selected
source, timezone, window, revision, freshness, missingness and truncation.
The host checks output values/units, count, unique IDs and provenance subset.
It supplies the authoritative revision/window/source metadata. Missing values
stay null. Bounded or unavailable reads remain explicit instead of claiming
complete totals. The existing canonical read limit may require a narrower
metric window for a dense optional-source history.

A metric returns `value`, `unit`, `count`, `recordIds` and `missingness`.
The view hook receives that typed metric and non-secret config and returns
`{schemaVersion:1,title,rows:[{label,value,unit}],status}`. The host permits at
most eight rows, validates string/value bounds and renders text with DOM text
nodes. No extension HTML enters the document. The JavaScript module is loaded
only from its exact reviewed asset in a same-origin external worker, limited
to two seconds. A failed/invalid/hung view shows an actionable card while the
built-in workspace remains usable. Reload reads the current canonical revision;
this implementation does not push live extension updates into an open page.

Python hooks and owner-schema validation run in an owned `-I -B` child with a
single bounded JSON exchange, no inherited credentials/environment search path,
three-second wall limit, two-second CPU limit, 256 MiB address limit and 64 KiB
output limit. The host reaps the child and terminates its process group.
Schema validation rejects external references and uses an empty local reference
registry. Invalid config and timeout have separate safe codes. Reviewed native
code is owner-trusted: these controls isolate failures, not hostile code.
Declarations and approved egress/secret-reference bindings document intent;
they do not enforce a network sandbox or automatically inject vendor secrets.

## Native lifecycle and connector preparation

Install is create-only. Enable validates config outside the writer lock, then
rechecks every reviewed byte under that lock before publishing the selection.
File data and directory ancestry are flushed before registry publication;
retry repeats barriers for existing directories/snapshots. A partial snapshot
cannot be selected. Preserve and inspect incomplete owner files; do not delete
them automatically to force activation.

Disable preserves every owner file and does not import code, including when a
manifest is broken. Revert first snapshots the present runtime/config, then
selects a retained review without overwriting working files or state. Thus the
editable files can differ from the selected review deliberately; a subsequent
edit requires fresh review. No automatic rebase, state migration or destructive
rollback is hidden in these commands.

`extension prepare` runs in an explicit native OS-owner process with a private
owner credential. It registers one exact canonical connector source and creates
one records:write-only agent for that source, with empty read restrictions and
no provider permission. Preparation never enables code, starts a schedule or
passes owner credentials/Service handles into a hook. `extensions:manage` on a
health credential is not code-activation authority.

A durable non-secret preparation record identifies the source, stable grant
name, starting actor inventory, retained actor and private output reference.
Source registration may remain if a later phase fails. A crash after grant
creation reconciles the named new actor before requiring an exact match of
role, sources, read restrictions and current active state. Revoked, changed or
ambiguous actors produce an explicit reconciliation error; no second actor is
minted. An existing matching token handoff is reused. Lost or incomplete output
requires `--rotate-existing` on the same actor and a NEW create-only private
path. Never overwrite an existing handoff or guess plaintext from a digest.
For a revoked/ambiguous preparation, inspect the owner grant inventory and
preserved preparation record before deliberate native reconciliation. There is
no automatic repair/reset command that discards that evidence.

## Original event retries and backup ordering

`extension run` consumes one explicit source event. It calls the connector to
normalize value/unit and the workflow hook to retain the same event and
provenance, then sends a canonical `records.put`. There is no implicit scheduler
or vendor lookup. Current grant revocation/source restrictions apply before
replay and writes. Write-only grants may obtain the health-free capabilities
identity/revision needed for CAS; they cannot list records, open the dashboard,
read context or request personal views. `sourceStatusOperation` remains null.

`WorkflowNamespace(extension_id,event_id)` stores the COMPLETE original request
and receipt under `personal/extensions/ID/state/requests/<event-digest>.json`.
The event/resource identity is deterministic. The original idempotency key is
generated once and persisted before send, then reused byte-for-byte with the
original CAS, payload and identity on retry. The abstract conformance example's
illustrative key spelling is not a native key-format requirement. A replay
never reruns changed normalization code or invents a new key. Changed content
for a retained event yields `source_event_conflict`, including after success.
A distinct event requires a distinct event ID. Cursors advance only after a
validated receipt. Capacity is 1,024 event identities per extension; refuse
before allocating a new event lock, with no implicit evidence eviction.

Lock order is job lock -> event lock -> brief workspace lock for client state.
Release workspace before calling Operations (workspace -> security). Native
backup holds workspace/security and never acquires a job/event lock. An atomic
workspace snapshot may contain pending client state plus a committed canonical
receipt: unchanged replay reconciles it before cursor advancement. A failed
mkdir/fsync cannot bypass ancestor barriers on retry. External editors do not
cooperate with these locks; inventory reports changing/unsupported files as
incomplete instead of claiming a coherent snapshot.

Compatible source replacement and fresh-process behavior are local acceptance
cases. Actual container replacement/architectures are CES-1068, copied/encrypted
restore and credential rotation CES-1070, upgrade/migration CES-1071, shared
agent tools CES-1072, scaffolding/guides CES-1086 and cross-agent/device release
qualification CES-1083. This implementation does not claim those later gates.

Retained native receipts exclude the transport-only `Idempotency-Replayed`
header. The canonical status, body and other headers remain unchanged on
identical replay; the wire response may still expose the replay marker.
Successful replay retains the original envelope/key/cursor and clears any
previous safe error without changing otherwise identical retained bytes.
