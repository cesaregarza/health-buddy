# Synthetic retained-record import tracers

These bounded CES-1081 slices export the retained measurement CSV schema or
workout sessions and their sets together, adopting either snapshot into a new
isolated native workspace. It is not a live migration
or cutover. Do not select personal data for this development tracer.

All paths must be explicit absolute native paths, with private owner directories
and files. The selected CSV must have the exact retained `log_measurement`
headers; records pass its shipped validators, canonical time checks and natural
key uniqueness checks. Limits are 1,000 nonempty records and 4 MiB per input or
snapshot. Optional empty fields stay empty; values, source labels and notes
remain in the private snapshot and canonical CSV. Treat the export as sensitive
unencrypted data. Do not publish it or pass it to an agent.

Review the exact selected CSV SHA256 and give the source a stable identifier.
Only that file is read; Git metadata, history, remotes and hooks are not exported.
Example paths and hash placeholders below are synthetic operator inputs:

```sh
health-buddy --workspace /native/private/canary legacy-import export-measurements \
  --source-csv /native/private/legacy/measurements.csv \
  --source-id synthetic-legacy \
  --expected-source-sha256 REVIEWED_CSV_SHA256 \
  --snapshot /native/private/measurements.json
health-buddy --workspace /native/private/canary legacy-import adopt-measurements \
  --snapshot /native/private/measurements.json \
  --expected-snapshot-sha256 REVIEWED_SNAPSHOT_SHA256
```

The snapshot includes explicit stable record IDs derived from the source namespace
and retained measurement natural key. Import preserves these IDs and row source
labels; canonical records belong to the built-in manual source. Private import
metadata retains the export source ID and both digests for reconciliation.
A new installation/dataset/epoch is created through the canonical bootstrap
journal in staging, then published as one new workspace. Optional integrations
and AI remain disabled; credentials require separate explicit owner setup.

Replies contain only counts, digests, duplicate state and canonical revision.
Repeating the exact import against its unchanged owned workspace does not create
records or advance its revision. Changed input, different snapshot, occupied or
unowned destination, or post-import canonical writes are refused. Do not erase an
occupied destination to retry. The legacy source remains unchanged, so abandoning
this isolated canary never requires writing back to the original source.

The `workout-sessions-sets` snapshot family exports exactly sessions and sets,
with separate reviewed SHA256 inputs and a combined 1,000-record/4 MiB limit.
Session IDs are retained exactly as canonical session IDs. Child rows retain
those parent IDs and matching session dates; set observation IDs are explicit,
stable source-namespace/natural-key IDs. Duplicate IDs/keys, foreign parents and
mismatched dates fail before destination creation. CSV values and notes remain
unchanged. Validation reuses the retained canonical workout validators; the
retained dashboard's `partial` status is preserved. Date-only rows use the new
workspace's explicit default UTC timezone, with no inferred legacy timezone.
The measurement snapshot schema/commands remain compatible.

```sh
health-buddy --workspace /native/private/workout-canary legacy-import export-workouts \
  --sessions-csv /native/private/legacy/sessions.csv \
  --sets-csv /native/private/legacy/sets.csv \
  --source-id synthetic-legacy \
  --expected-sessions-sha256 REVIEWED_SESSIONS_SHA256 \
  --expected-sets-sha256 REVIEWED_SETS_SHA256 \
  --snapshot /native/private/workouts.json
health-buddy --workspace /native/private/workout-canary legacy-import adopt-workouts \
  --snapshot /native/private/workouts.json \
  --expected-snapshot-sha256 REVIEWED_WORKOUT_SNAPSHOT_SHA256
```

The paired source inputs are rechecked before export publication. Quiesce legacy
writers while selecting/reviewing them; this does not establish a snapshot of an
arbitrary running legacy service. Exact-repeat, destination preservation and
redacted receipt rules are the same for both explicit snapshot families.

The `manual-canary` family composes the already validated measurement and
workout snapshots with the current 16-column retained intake CSV, one retained
schema-2 plan and a finite reviewed preference mapping. Its combined limit is
1,000 records and 4 MiB, including embedded input snapshots. Each of the five
inputs requires its separately reviewed SHA256. No source directories, Git
metadata or running services are inspected.

The portable preferences JSON contains exactly `schemaVersion: 1`, `displayName`,
`timezone`, `goals` and `equipment`, with the existing config schemas for each.
An explicit supported IANA timezone is required: date-only workout observations
use that selected zone. Earlier standalone workout imports retain their documented
UTC behavior. Security, credentials, absolute legacy storage paths, optional
integrations and arbitrary legacy preferences are not accepted. All optional
integrations and AI stay disabled in the new workspace.

The retained plan passes the supported plan validator and is stored in its
existing schema-2 format; unsupported fields, schemas and references are refused
before publication. Current intake fields pass the retained intake validator.
The historical pre-sodium intake header is outside this slice; it must be reviewed
through its existing missing-field normalization before being included.

```json
{"schemaVersion":1,"displayName":"Synthetic canary","timezone":"America/Chicago","goals":[],"equipment":[]}
```

```sh
health-buddy --workspace /native/private/manual-canary legacy-import export-manual-canary \
  --measurements-snapshot /native/private/measurements.json \
  --expected-measurements-sha256 REVIEWED_MEASUREMENTS_SNAPSHOT_SHA256 \
  --workouts-snapshot /native/private/workouts.json \
  --expected-workouts-sha256 REVIEWED_WORKOUTS_SNAPSHOT_SHA256 \
  --intake-csv /native/private/intake.csv \
  --expected-intake-sha256 REVIEWED_INTAKE_SHA256 \
  --plan-json /native/private/plan.json \
  --expected-plan-sha256 REVIEWED_PLAN_SHA256 \
  --preferences-json /native/private/preferences.json \
  --expected-preferences-sha256 REVIEWED_PREFERENCES_SHA256 \
  --source-revision OWNER_DECLARED_LEGACY_GIT_REVISION \
  --snapshot /native/private/manual-canary.json
health-buddy --workspace /native/private/manual-canary legacy-import adopt-manual-canary \
  --snapshot /native/private/manual-canary.json \
  --expected-snapshot-sha256 REVIEWED_COMBINED_SNAPSHOT_SHA256
```

Both nested record snapshots must name the same export source namespace.
Cross-family record ID collisions are refused. Input digests and the owner-declared
40-hex legacy revision are retained privately as provenance. The legacy revision
is explicitly **unverified**: this helper never reads Git history. New canonical
revision zero is a bootstrap revision, not the old Git revision. A changed portable
preference file blocks exact repeat rather than overwriting owner changes.

Remaining CES-1081 scope includes other manual record families, historical intake
normalization, HealthKit and connector stores, device replay, richer reconciliation
and full reversible-canary qualification. None is claimed by these retained-record
tracers, and no real source, private Git history or actual cutover was exercised.

## Synthetic legacy receiver data tracer

The `legacy-healthkit-receiver` family exports the clean retained SQLite schema
1: device IDs, current records, tombstones and acknowledged batch hashes/counts.
It excludes device labels, token salts and token hashes. Select a closed,
quiesced, private native database snapshot with no SQLite sidecars and review
its exact SHA256. Supported input has only the four retained tables and indexes;
foreign schemas, generated columns, views and triggers are refused. Limits are
4 MiB and 1,000 combined rows, with at most eight devices. No live/private receiver
or actual phone is qualified by the synthetic tests.

The separately reviewed mapping JSON is exactly a list of
`{deviceId, sourceId, streamId}` entries. Every retained UUID device needs one
unique supported source ID and UUID stream. The mapping binds data to a new
canonical stream; **it does not authorize the old phone** or establish continuity
with any physical receiver identity. Observation IDs use the existing canonical
stream/record identity rule; underlying HealthKit record and device IDs remain
unchanged. Current records retain received timestamps, digests and metadata;
tombstones preserve deletion semantics, including when a deleted record arrives
again. Delivery provenance represents the retained current rows, not a complete
historical delivery trace that the legacy schema did not store.

```sh
health-buddy --workspace /native/private/receiver-canary legacy-import export-receiver \
  --source-db /native/private/closed-legacy.db \
  --expected-source-sha256 REVIEWED_DATABASE_SHA256 \
  --mapping-file /native/private/device-streams.json \
  --expected-mapping-sha256 REVIEWED_MAPPING_SHA256 \
  --confirm-quiesced --snapshot /native/private/receiver.json
health-buddy --workspace /native/private/receiver-canary legacy-import adopt-receiver \
  --snapshot /native/private/receiver.json \
  --expected-snapshot-sha256 REVIEWED_RECEIVER_SNAPSHOT_SHA256
```

Adoption creates an isolated empty receiver, registers the explicitly selected
streams through canonical operations, seeds current records through the retained
HealthRepository transitions, and publishes only the verified new workspace.
Ordinary startup still refuses a nonempty unadopted legacy receiver. Old device
credential material never enters current authority: retained FK device rows have
empty credentials and are disabled. The new workspace defaults to denied access;
this command does not mint phone credentials or insert security actors.

Imported acknowledgements are explicitly bound to the reviewed snapshot and
retained normalized payload hash/counts. Under newly issued **synthetic fixture
policy** authority, exact old replay returns the existing acknowledgement schema
with `duplicateBatch: true` and the current canonical identity tuple, without
advancing its revision. Changed same-batch content conflicts; new batches commit
normally. The legacy schema stores hashes rather than original batch payloads:
the replay fixture supplies known fabricated payloads. It does not invent old
canonical journal entries, canonical revisions or unavailable original payloads.

A native bearer owner can now explicitly admit one reviewed imported device into
current security authority. Set up the new workspace owner separately using the
supported `security bootstrap --owner-token-file PATH` command and keep its credential file
private. Admission validates the private import receipt and database adoption
marker against the selected snapshot, current installation/dataset/restore epoch,
and the exact canonical journal/receiver stream. It creates an inactive upload-only
actor with no credential. Exact repeats preserve the actor and credentials;
changed selections, foreign bindings and non-owner callers are refused.

```sh
health-buddy --workspace /native/private/receiver-canary \
  --credential-file /native/private/receiver-canary/secrets/owner.token \
  legacy-import admit-device --device-id RETAINED_DEVICE_UUID \
  --expected-snapshot-sha256 REVIEWED_RECEIVER_SNAPSHOT_SHA256 \
  --name "Reviewed imported phone"
```

Use the returned actor `id` as the explicitly selected predecessor in ordinary
replacement pairing, retaining the device UUID. Existing pairing provisions the
already matching source/stream and issues a fresh credential; old credentials are
never copied or admitted. This native operation is not a public transport action.
The data-adoption receipt's `realPairingBridge: unimplemented` describes its initial
data-only phase; admission does not rewrite that source receipt or health records.
The actual SecurityStore synthetic fixture proves replacement pairing, old-token
refusal, exact acknowledged replay and a genuinely new batch. Real phone identity,
checkpoint/epoch reconciliation and cutover still require separate device evidence.
This checkpoint does not complete CES-1081 or authorize a personal/deployed receiver.

## One unified synthetic canary

`adopt-canary` composes a reviewed manual-canary snapshot, reviewed receiver
snapshot and exact retained SleepIQ nightly CSV in one new private workspace.
The accepted manual bootstrap and receiver adapter remain the storage owners.
Known historical intake headers without `sodium_mg` normalize through the existing
adoption helper; added sodium values remain empty/unknown, never zero. Manual
stable IDs, workout parent links, plan and explicitly reviewed preferences survive.
Original snapshot/revision provenance remains distinct from the new canonical
revision. Inputs share a 4 MiB total limit and at most 1,000 combined record rows.
Receiver mappings and acknowledgements retain their existing bounded contracts.

Select exactly one reviewed sleeper ID and an explicit IANA timezone matching
the reviewed workspace timezone. The finite SleepIQ mapping accepts the shipped
`NightlyRecord` CSV columns and verifies stable record keys and normalized hashes.
It requires an aware session end, an aware fetched timestamp, and integer duration
seconds from 0 through 86,400. The local date of session end becomes the existing
canonical `date,sleep_hours` export; duration seconds divided by 3,600 becomes
hours. `night_date` is the start-of-night identity and is **not** reused as wake
date. Missing/naive timestamps, foreign sleepers, invalid durations, duplicate
keys or conflicting wake dates are refused. A retained export that lost timezone
information cannot pass this mapping; no offset or missing date is inferred.

The exact reviewed nightly file, including original keys, source versions and
metadata, remains private at `stores/imported-sleepiq-nightly.csv`. The two-column
canonical view intentionally exposes only daily duration. This enables a local
read-only connector export; it does not configure upstream login, networking,
scheduled synchronization, credentials or optional AI. Core CLI loading requires
no optional connector dependencies. Reconciliation replies contain counts,
digests and revision only, never sleeper IDs/names, notes or health values.

```sh
health-buddy --workspace /native/private/unified-canary legacy-import adopt-canary \
  --manual-input /native/private/manual.json --expected-manual-sha256 MANUAL_SHA256 \
  --receiver-input /native/private/receiver.json --expected-receiver-sha256 RECEIVER_SHA256 \
  --sleepiq-input /native/private/nightly.csv --expected-sleepiq-sha256 NIGHTLY_SHA256 \
  --sleeper-id REVIEWED_SLEEPER_ID --timezone America/Chicago
```

Publication is a create-only rename of the verified staged workspace. Exact
repeat against the same unchanged workspace is inert; changed inputs, different
selection, occupied/unowned destinations or changed canonical/config/personal
files are refused. Regenerable cache is excluded from this comparison. Review
ordinary canonical reads, plan/preferences and reconciliation before accepting
this isolated result. Original exports stay unchanged, so abandoning the canary
never requires restoring over the old deployment.

Encrypted backup is a **separate explicit owner readiness guard**, not a
prerequisite for exporting legacy data or creating an isolated canary. After
owner setup and any deliberate pairing/customization, use the existing backup
key generation and quiesced backup commands documented in [backup and restore](backup-restore.md).
Then verify the reviewed encrypted archive against the current canary:

```sh
health-buddy --workspace /native/private/unified-canary \
  --credential-file /native/private/unified-canary/secrets/owner.token \
  legacy-import canary-backup-readiness \
  --archive /native/private/canary.hbb --key-file /native/private/backup.key \
  --expected-archive-sha256 REVIEWED_ARCHIVE_SHA256
```

The guard fully authenticates the existing encrypted archive, compares its
identity/revision and complete non-cache files with the current workspace, and
refuses stale or changed backups. A successful reply performs no cutover and
still reports real phone qualification pending. Required backup of an actual
legacy deployment before live cutover, actual phone checkpoint/epoch handling,
connector synchronization and private deployment qualification are separate
remaining gates. These synthetic commands do not authorize live migration.
