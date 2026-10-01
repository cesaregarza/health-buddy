# Personal extensions and durable owner workspace

Health Buddy supports API-1 metric/view and connector/workflow packages under
an external owner workspace. Start with either maintained synthetic reference
package, edit its source/config/tests/notes, review the change and enable it.
Daily built-in logging remains useful with no extension, Jev or agent running.

The installed descriptor schema is
[`src/health_buddy/extension_manifest.schema.json`](../src/health_buddy/extension_manifest.schema.json).
The accepted `{manifest,cases}` examples in `contracts/v1/examples` remain an
abstract conformance oracle for numeric/provenance/missingness/retry semantics.
Their wrapper is not the installable descriptor format. The executable packages
in `src/health_buddy/reference_extensions` implement those semantics with their
own maintained tests. See [implementation and trust boundaries](extension-implementation.md).

## Discover the active workspace

```sh
health-buddy --workspace "$WORKSPACE" workspace describe --json
health-buddy --workspace "$WORKSPACE" extension inspect
health-buddy --workspace "$WORKSPACE" extension compatibility --extension-api 1
```

These are deliberate native OS-owner maintenance operations, with no health
credential argument. Discovery reports actual source/workspace paths, a known
Git HEAD when available, API interfaces/docs, extension versions/reviews,
personal file inventory/digests, required secret-reference names and recorded
fork status. It never imports personal code, fetches schemas, runs Git status,
executes a build recipe or reads Git history/remotes. Working-tree `dirty` and
release-artifact identity remain unknown where not independently proven. A
source archive can have no Git HEAD; no identity is invented.

The complete inventory is private operator data, not an HTTP/ordinary-agent
health response. Shared tools must expose a separately filtered discovery DTO.
An incomplete inventory blocks any claim of preservation; coordinate external
editors or use a consistent filesystem snapshot. Never reduce backup to listed
canonical data paths: include the whole workspace, security authority, secrets
and personal source/config/tests/state. Restore tooling must invalidate restored
credentials; this ticket does not perform a restore.

```text
workspace/
  config.json                   # actual storage, timezone and optional settings
  stores/ operations/ security/ # canonical stores, journal and digest authority
  personal/
    WORKSPACE.md                # create-only owner intent and local conventions
    extensions/<namespace.name>/
      extension.json
      src/ assets/ config/ tests/ notes/ migrations/ state/
    extension-registry.json
    extension-reviews/<id>/<digest>/
    forks/<id>/fork.json        # owner-recorded core changes and evidence
  secrets/                     # private credentials, never source/Git/browser storage
```

Every extension ID is namespaced; runtime paths are relative and owned by that
extension. No package may claim a canonical store or another addition's state.
Source, assets, config, tests, notes, migrations, unknown owner files and state
survive disable/revert and compatible source replacement. Reinitialization
never overwrites an existing `WORKSPACE.md` or other owner file.

## Install and adapt the metric/view

Use the private owner or scoped read credential established in
[authorization](authorization.md). The shell variables below are paths chosen
by the operator; do not put token bytes in commands.

```sh
health-buddy --workspace "$WORKSPACE" extension install --example local.weekly-mass
health-buddy --workspace "$WORKSPACE" extension enable \
  --id local.weekly-mass --source-id manual
health-buddy --workspace "$WORKSPACE" --credential-file "$READ_FILE" \
  extension preview --id local.weekly-mass
```

`preview` prints the current caller-filtered metric JSON; it does not start a
server or create synthetic production records. For a reproducible fixture read,
provide both `--from` and `--to` within seven selected local calendar dates.
The built-in dashboard displays enabled personal views on reload. Two synthetic
70/74 kg observations produce 72 kg/count 2; empty input produces null with
`insufficient_data`. Source overlap is not silently combined. The host supplies
canonical IDs, source, window/timezone, revision, freshness and limited/stale
indicators. Text is escaped; a failed view leaves built-in logging available.

For a simple adaptation, edit `config/settings.json` (`title`, `displayUnit`
kg/lb) and its synthetic tests/notes in the installed personal directory.
Changes to runtime/config put the addition in `needs_review`. Inspect the diff,
run its behavioral tests, then explicitly enable it again.

```sh
health-buddy --workspace "$WORKSPACE" extension disable --id local.weekly-mass
health-buddy --workspace "$WORKSPACE" extension revert \
  --id local.weekly-mass --review "$REVIEW_DIGEST"
```

Revert selects retained reviewed code/config while keeping current editable
files and authoritative state intact. Inspect reports the selected digest.
State-schema changes require a separately reviewed migration, never automatic
startup conversion. Declared dependencies are exact versions, not downloads.

## Prepare and run the connector/workflow

```sh
health-buddy --workspace "$WORKSPACE" extension install --example local.water-import
health-buddy --workspace "$WORKSPACE" --credential-file "$OWNER_FILE" \
  extension prepare --id local.water-import --source-id synthetic-water \
  --credential-reference secrets/local.water-import-token
health-buddy --workspace "$WORKSPACE" extension enable \
  --id local.water-import --source-id synthetic-water
health-buddy --workspace "$WORKSPACE" \
  --credential-file "$WORKSPACE/secrets/local.water-import-token" \
  extension run --id local.water-import --event-file "$PRIVATE_EVENT_FILE"
```

The private event file is a mode-0600 JSON object, for example a deliberately
fabricated fixture:

```json
{"eventId":"synthetic-water-001","sourceId":"synthetic-water","observedAt":"2030-01-03T09:00:00Z","value":250,"unit":"mL"}
```

This explicit command writes a canonical observation. Use a synthetic workspace
for the example. It normalizes 250 mL to 0.25 L. Repeating the same event reuses
the original request/receipt and writes once; changed content with the same ID
is a conflict. Unknown outcomes preserve the request before cursor advancement.
The scoped credential is write-only for the named source, with no provider or
read access. No plaintext credential enters event state, health receipts or
extension input. A lost handoff requires deliberate same-actor rotation to a
new private path; partial or revoked/changed grants require explicit review.
See the [recovery contract](extension-implementation.md#native-lifecycle-and-connector-preparation).

No schedule starts on install/enable/discovery. API 1 provides an explicit job
entrypoint; external scheduling requires separate operator configuration.
The reference connector needs no vendor, network or AI key. Approved egress and
secret references are review declarations/inventory, not a hostile-code sandbox
or automatic vendor-secret injection. Native extension code is owner-trusted.
Even an owner health token cannot activate code over HTTP.

## Tracked core forks

Prefer supported additions for personal behavior. If core changes are required,
retain source independently and write `personal/forks/<id>/fork.json` with exact
`schemaVersion:1`, `upstreamBase`, nullable `sourceCommit`/`sourceTree`, relative
`patchFiles`, boolean `dirty`/`conflicted`, `buildRecipe` and `tests` strings.
Git identities are 40-character lowercase hashes. Keep the associated patches
and notes in that directory; do not copy private Git history into a release.

`workspace describe` compares the recorded base with the known installed HEAD,
or `--upstream-base COMMIT` for an explicit target. Changed base reports
`core_fork_requires_rebase`; unknown target reports `target_unknown`. A matching
clean record means only `recorded_compatible`, not live Git/build evidence.
Before recording a review, inspect the chosen native checkout with hooks,
fsmonitor, external diff/textconv and submodule helpers disabled; record its
exact commit/tree, deliberate local changes and unresolved conflicts. Run
its documented relevant tests/build, then update owner metadata with that
evidence. Discovery never runs those commands or repairs/rebases code.

The [canonical agent guide](agent-guide.md) reuses these installed examples as
scaffolds and maps edits to their checks. Installer/upgrade execution remains
CES-1071 work. Source replacement evidence here is separate from container
replacement (1068), encrypted copied restore (1070), shared tools (1072), and
fresh cross-agent/device/release qualification (1083). None is implied by an
extension test or merged PR.

## Native editor permissions

Use the service owner's OS account and `umask 077` before creating personal
source/config/tests/notes. The runtime requires owned mode-0700 directories and
mode-0600 files, including newly added files; a health credential does not grant
filesystem maintenance rights. An editor that creates a mode-0644 file makes
that addition unavailable until explicitly repaired. Inspect the exact path,
owner, file type and intended content first, then repair only that selected
file/directory (for example `chmod 600 "$FILE"` or `chmod 700 "$DIRECTORY"`).
Do not recursively chmod an owner tree or follow a symlink to make validation
pass. Reinspect and review the runtime digest after deliberate edits.
