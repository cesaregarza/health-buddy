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

Remaining CES-1081 scope includes other manual record families, plans,
preferences, HealthKit and connector stores, device replay, richer reconciliation
and full reversible-canary qualification. None is claimed by these retained-record
tracers, and no real source, private Git history or actual cutover was exercised.
