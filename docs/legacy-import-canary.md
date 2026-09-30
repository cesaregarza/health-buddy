# Synthetic measurement import tracer

This first CES-1081 slice exports only the retained measurement CSV schema
and adopts it into a new isolated native workspace. It is not a live migration
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

Remaining CES-1081 scope includes other manual record families, plans,
preferences, HealthKit and connector stores, device replay, richer reconciliation
and full reversible-canary qualification. None is claimed by this measurement
tracer, and no real source, private Git history or actual cutover was exercised.
