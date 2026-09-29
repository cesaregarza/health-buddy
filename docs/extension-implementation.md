# Extension API 1 implementation seam

This first CES-1085 checkpoint freezes interfaces; lifecycle, renderer, examples
and conformance tests are still being implemented. It does not claim validation.

`extension_api.py` contains finite DTOs, registry statuses and execution bounds.
`extension_manifest.schema.json` describes an installed `extension.json`;
`contracts/v1/extension.schema.json` remains the historical fixture wrapper until
its examples are updated to the installed format in this implementation.
Descriptor schema version, extension API major, semantic extension version and
owned state schema are separate. Dependencies are exact declared versions,
checked without imports or downloads. Compatibility never silently disables or
migrates personal source/state. Schema/config JSON is byte/depth bounded, has no
external references, and is inspected without executing owner code.

## Read and view boundary

The host admits the current caller through canonical Operations and captures
only permitted selected source/kind/fields. A metric receives finite JSON with
an explicit window/timezone, provenance, revision and missingness/truncation.
The host checks output shape, finite units/values, provenance subset and matching
revision/window; no owner-wide metric cache serves a narrower caller.

Native Python entrypoints execute in a bounded owned subprocess using one
finite JSON exchange. The JavaScript `render` entrypoint runs in a same-origin
external worker, returns a bounded ViewSpec and is terminated on deadline.
The built-in renderer uses escaped text, never extension HTML. The runtime
will expose only enabled digest-bound view source through the same canonical
read/asset admission; there is no HTTP install/enable/maintenance endpoint.
No code loads merely because a manifest was discovered. These are failure
boundaries, not a security sandbox: reviewed local code retains OS/browser
rights. Declarations describe intent, not enforced egress restrictions.

## Native lifecycle and connector preparation

Native commands will be `extension install --from PATH`, `enable --id ID`,
`disable --id ID`, `revert --id ID --review DIGEST`, `inspect`, and `compatibility`.
Install is create-only. Enable records reviewed manifest/config/runtime digests
and explicit binding selection; edits yield needs_review before execution.
Disable preserves every personal file and does not import code. Revert selects
one retained reviewed code/config snapshot and never overwrites state; it first
preserves the current files in a reviewable snapshot. Migration remains an
explicit isolated maintenance action owned by CES-1071, not startup behavior.

The preparation command is:

```sh
health-buddy --workspace "$WORKSPACE" --credential-file "$OWNER_FILE" \
  extension prepare --id local.water-import --source-id synthetic-water \
  --credential-reference secrets/local.water-import-token
```

It runs only in an explicit native OS-owner process. Preparation acquires its
extension job lock, authenticates the explicit owner proof, reuses canonical
source registration and existing security grant actions, and retains a bounded
non-secret preparation record under personal/. A source is registered first;
that side effect can remain if later credential preparation fails. The grant
has only records:write for exactly that source, empty read restrictions and no
provider permission. Neither owner proof nor Service/Security handles reach the
extension process. Health extensions:manage never becomes code authority.

A durable create intent names the stable source, grant name, intended output
reference and starting actor inventory before grant creation. If a process dies
between grant creation and recording its actor ID, retry reconciles the exact
new matching grant from the current owner inventory; it refuses ambiguity.
A retained actor is reused and an already valid matching private handoff is
preserved. Lost/unfinished one-time token output requires explicit
`--rotate-existing` on the same retained actor and a new create-only private
handoff path. Never mint a second grant merely because output is missing; never
replace an existing secret file. Each phase and partial outcome is reported
without secrets. Preparation does not enable code or start a schedule.

## One retry engine and lock order

`ClientWorkflow(..., namespace=WorkflowNamespace(extension_id, event_id))`
retains one complete original request/receipt under
`personal/extensions/ID/state/requests/<event-digest>.json`. The native-client
default path/format remains unchanged. Changed content for a retained event
returns source_event_conflict, including after success. Explicit new-write or
discard cannot replace an extension event. A bounded event-count policy refuses
new events at capacity; it never evicts conflict/replay evidence implicitly.

The host holds a per-extension job lock; ClientWorkflow holds its event lock.
Path creation, state read and atomic/fsynced state write briefly acquire the
existing workspace writer lock. Release it before current authentication or
canonical Operations. Operations then acquires workspace -> security itself.
Backup holds workspace only and never takes a job/event lock. A snapshot may
contain pending client state plus a committed canonical receipt: replay must
reconcile before cursor advancement. External editors do not cooperate with
this lock, so changing-file inventory is incomplete rather than falsely atomic.

D1 owns extension_api/schema and this ClientWorkflow seam. D2 will add registry,
host/native lifecycle, finite operation/transport view reads, maintained
examples, personal/fork inventory, conformance tests and the escaped renderer.
No other worker writes these files concurrently. CES-1068 owns real container
replacement/architectures; CES-1070 owns encrypted copy/restore; CES-1071 owns
upgrades/migrations; CES-1072/1086 consume discoverable source and command seams.
