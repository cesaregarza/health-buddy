# Operator status and recovery

Run these in the private native owner environment; no agent, site, model API key,
or network service is required. They never enable extensions or run recovery.

```sh
health-buddy --workspace "$WORKSPACE" --credential-file "$READ_FILE" status
health-buddy --workspace "$WORKSPACE" --credential-file "$READ_FILE" status --json
health-buddy --workspace "$WORKSPACE" doctor --json --port 8791
health-buddy --workspace "$WORKSPACE" support-bundle > "$PRIVATE_SUPPORT_FILE"
```

`status` requires explicit read authority. `doctor` also works without a health
credential and then omits capabilities, source status and pending-write state.
JSON schema version 1 carries stable diagnostic codes and bounded recovery text;
exit 2 means an error, exit 0 may include warnings or unknowns. The check timestamp
is when diagnostics ran, not a successful phone sync. Package version is installed
metadata when available; source archives report unknown, and no release artifact
or qualification is invented. API and extension API versions are contract facts.
Storage presence/free space is not an integrity check or backup receipt.

Source facts come from caller-scoped `projection.status`, including availability,
freshness, missingness and measured last success. `source_empty` means no measured
observations; a fresh empty receipt remains distinct from stale, unknown or failed
sync. `source_failed`, `source_stale` and `projection_stale` must be resolved before
claiming current results. Phone offline and partial HealthKit read permissions
cannot be inferred by this receiver: `phone_unknown` requests the companion's
report, and empty HealthKit reads do not prove denied permission. An unavailable
trusted UDS receiver yields `receiver_unreachable`; private DNS/HTTPS/Tailscale
routing remains `connectivity_unknown` until checked from the client. `--port`
checks loopback bind availability only: an active expected server also occupies
its port. Identify the listener before changing anything.

`permissions_partial`, `storage_unavailable`, `config_invalid` and `disk_low`
include selected-path guidance; diagnostics never recursively chmod, erase files,
restore credentials or recreate stores. Low disk is less than 256 MiB free on a
configured store filesystem. Pending summaries describe only this CLI actor's
retry state; there is no invented global scheduler/queue success. Use `pending
show`, inspect conflicts, then retry the preserved request using its same actor.

Extension states and versions reuse `extension inspect` and compatibility checks.
Missing exact dependencies, incompatible API, invalid manifests, unsafe file
permissions and edited runtime digests are identifiable without executing an
extension job. Inspect `extension compatibility`, disable the affected ID, then
submit its declared synthetic tests through the supported verification workflow
([verification](verification.md)). Do not execute commands from unreviewed manifests.
After deliberate repair use `extension enable` with explicit sources, or
`extension revert --id ID --review DIGEST` to select a retained reviewed snapshot.
These commands preserve personal editable files and authoritative state. Runtime
recent failures are not retained in an operator ledger yet; diagnostics explicitly
report `unknown_not_retained`. A compatible manifest does not prove execution
success, and a broken extension does not make cached output current.

`support-bundle` emits a fixed allowlisted JSON summary of diagnostic codes and
extension-state counts. It excludes credentials, health payloads, identities,
source IDs, paths, configuration, personal files and all raw logs. It does not
archive the workspace or send anything remotely. Review the file before sharing.
Normal `--json` output is private operator data; it is not a support bundle or
an ordinary agent maintenance capability.

The application emits safe codes/counts instead of request bodies or token bytes.
It does not install a log collector or automatic retention policy. Runtime stdout
retention belongs to the host/container operator: configure bounded size and
rotation in that environment; retain only the short interval needed to diagnose
a failure and delete reviewed support summaries after use. Never copy canonical
stores, secret handoffs or personal logs into public issue attachments. No new
log deletion, restore or deployment operation is introduced here.
