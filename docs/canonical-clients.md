# Canonical local clients

The native App, CLI and dashboard call the same admitted operations as HTTP.
`App(root)` and the CLI deny health access by default. `App.development(root)`
or the explicit global `--development` flag selects the local development
owner policy. This is not production authentication or a remotely accessible
mode. The HTTP launcher constructs its service inside its one serving process.
`App.authenticated(root, proof=BearerProof(...))` instead uses the durable
authority and reauthenticates before each operation. The CLI accepts only an
explicit private `--credential-file`, never token argv or ambient credentials.
See [private owner setup](authorization.md).

The historical `scripts/log_*.py` command names now require `--workspace` and
delegate to that same CLI; `log_workout.py` retains its start/set/cardio/finish
subcommands. Explicit loose-file output flags are refused. Pure legacy row,
CSV and calculation helpers remain available for format compatibility and
isolated fixtures; they are not supported authoritative write entrypoints.

The standalone context HTTP server and remote Git workout writer are retired.
`health-ingest` retains only explicit-workspace delegation to the canonical
server. Its old database migration, direct token issue/revoke and loose export
commands refuse without touching a repository. Workspace device administration
requires the authorization/pairing implementation; the development owner policy
does not implicitly become a phone upload credential.

For a private owner workspace outside the release checkout:

```text
health-buddy --workspace /path/to/private-workspace init
health-buddy --workspace /path/to/private-workspace --development status
health-buddy --workspace /path/to/private-workspace --development log measurement --measured-at-local 2030-01-01T08:00:00 --weight-lb 150
health-buddy --workspace /path/to/private-workspace --development plan --file /path/to/synthetic-program.json
health-buddy --workspace /path/to/private-workspace --development pending show
health-buddy --workspace /path/to/private-workspace --development pending retry
```

Dates and values above are fabricated. Logger flags retain their existing
field meanings; caller-selected storage paths are rejected. Circumference
logging previews a validated row until `--apply` is supplied. Completed workouts
use `log workout` with the existing schema-v1 JSON on standard input. Plans are
validated in their existing schema-v2 file format and translated to the strict
canonical wire shape. Context is an explicit bounded window of 1–366 days;
the dashboard offers 14, 30, 90 and 366 days.

## Durable retry behavior

The native client writes `personal/state/native-client.json` privately and
atomically under a separate lock before its first submission. It retains the
original payload, method/path, installation/dataset/epoch, revision, API version
and idempotency key. The next same action or `pending retry` reuses that exact
request, including after process restart. A payload builder runs only once for
an unresolved action, so omitted defaults cannot change on retry. The current
retained logger parsers require explicit event timestamps.

A local cursor advances only after a success receipt has the exact identity,
original revision plus one, matching resource ID and ETag. Replaying a completed
action rechecks server policy and must preserve its original status/body.
The fixed receipt may say projection is pending even after projection catches
up; clients read the current projection separately.

Identical completed actions replay by default. Use global `--new-write` for an
intentionally repeated action. A changed plan file is a different intent even
at the same path; it never silently reports the old success. If an earlier plan
write is unresolved, the changed file is refused. `pending retry` can still
retry its captured original bytes when the source file has changed or vanished.

An uncertain response never clears or replaces a pending key. `pending show`
reports only operation/state/cursor and a safe error code, not private payloads.
A definitive conflict or changed epoch can be resolved deliberately:

```text
health-buddy --workspace /path/to/private-workspace --development pending discard --acknowledge-possible-save
```

This records the explicit resolution and permits a newly reviewed action. It
does **not** undo a write: the original request may already have saved. A damaged
native state file fails closed and is preserved for owner inspection/restoration.

## Browser ownership and identity

The dashboard retains an exact pending request before sending it. A lost reply
or reload keeps that request available for unchanged retry; failed local storage
prevents sending. Receipt verification uses the same identity, revision and
session checks. A saved receipt is retained before the UI marks the draft saved.

One page owns the workout editor through an exclusive origin Web Lock. Other
tabs can read the dashboard but must close the owning page and reload before
editing or saving that draft. Closing/reloading releases the lock. A browser
without Web Locks cannot save through this editor; the native client remains
available. This is deliberate serialization of the one browser draft, not an
assumption that localStorage read/compare/write is atomic across tabs.

Drafts, comparison baselines and checklist state are bound to installation,
dataset and restore epoch. A changed tuple gets fresh baseline/check namespaces;
the old draft/request stays intact and blocked until explicit resolution. Invalid
JSON or unsupported draft shapes are also preserved, with no claim that an
earlier request was never submitted. Resolution asks the owner to acknowledge
the possible earlier save before clearing or rebinding local retry state.

## Authenticated retry ownership

Production native retry format2 stores the Security authority's authenticated
actor binding, security epoch and receiver tuple. `Principal.credential_id` is
still only an opaque process-local handle. Same-actor credential rotation can
retry the exact original request after deliberate reauthentication; another
actor/epoch is rejected. Legacy format1 handle hashes remain preserved and
blocked until explicit acknowledged resolution. Development/test compatibility
is separate. Clients never infer actor IDs from token text or mint authority.

## Verification scope

`tests/test_client_workflow.py` uses real private state files and a scripted
synthetic server to test retry/receipt state; it does not prove server atomicity.
Portable App/CLI/HTTP tests exercise the actual canonical service. The existing
browser suites retain their UI checks with canonical mocked envelopes; the
portable browser uses the maintained server. `check_client_workflow.py` separately
checks lost acknowledgements, cross-tab ownership, malformed state and identity
changes against a synthetic server model. Results are bound to a frozen source
commit.

Retained native receipts exclude the transport-only `Idempotency-Replayed`
header. The canonical status, body and other headers remain unchanged on
identical replay; the wire response may still expose the replay marker.
Successful replay retains the original envelope/key/cursor and clears any
previous safe error without changing otherwise identical retained bytes.
