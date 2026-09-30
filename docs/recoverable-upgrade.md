# Compatible upgrade staging checkpoint

The native owner command prepares a candidate without changing a running service,
container, active workspace, or unrelated host service. Activation is a separate
explicit owner command. The candidate is migration verification evidence; compatible
activation switches the binary mounted on the original workspace.

Use the existing encrypted-backup key generation command, retain the key outside
the workspace, and stop external editors before staging. Select an immutable
runtime manifest and obtain its SHA256 through your release delivery channel.
The digest binds the selected bytes; it is not publisher authentication.

```sh
healthbuddy --workspace /native/owner --credential-file /native/owner-token upgrade stage \
  --manifest /native/release/runtime-manifest.json --manifest-sha256 EXPECTED_SHA256 \
  --architecture amd64 --archive /native/private/pre-upgrade.hbb \
  --key-file /native/private/backup.key --candidate /native/private/candidate \
  --confirm-quiesced
```

Preflight uses existing release inspection to verify source/image hashes and
interface versions, and blocks enabled incompatible extensions. Staging first
creates a verified encrypted snapshot, authenticates that snapshot before
materializing it into an absent private candidate, and verifies canonical identity
and revision. Schema version 1 requires no conversion. Personal files and owner
configuration survive; regenerable cache is omitted. Compatible staging preserves
the restore epoch and credentials. Empty-host backup restore deliberately uses a
different policy, rotating the epoch and invalidating credentials.

The candidate receipt describes the selected target and says activation is pending.
Never serve original and candidate as concurrent writers. Stop connector jobs and
other writers and keep editors quiesced during the explicit activation:

```sh
healthbuddy --workspace /native/owner --credential-file /native/owner-token upgrade activate \
  --candidate /native/private/candidate --manifest /native/target/runtime-manifest.json \
  --manifest-sha256 TARGET_SHA256 --architecture amd64 \
  --previous-manifest /native/current/runtime-manifest.json --previous-sha256 CURRENT_SHA256 \
  --runtime-env /native/private/runtime.env --docker /usr/bin/docker \
  --project health-buddy --uid 1000 --gid 1000 --confirm-quiesced
```

The Docker CLI, daemon and Compose plugin require independent owner admission.
The maintained Compose file targets only `api` in the named Health Buddy project;
other services and connector jobs are not managed. The existing environment must
already describe the current installation. Running-image observation must match
the selected previous artifact before stopping it. Unsupported release interfaces
(storage, API/client, pairing/phone payload, extensions) fail before activation.
These are declared protocol versions, not observations of a physical phone.

Changed canonical records, config, personal files, secrets or stores require a new
stage and backup before activation. An incompatible enabled extension blocks
activation: use the supported `extension inspect`, `extension test`, review/enable
or explicit `extension disable` commands, retain its files, then stage again.
Tracked core conflicts require owner rebase and review; recorded compatibility is
owner evidence, not proof that arbitrary private edits automatically work.

Activation writes private durable phases in `operations/upgrade-activation.json`.
Repeat the same command after interruption; stop/load/start steps resume and an
already active target is checked against the observed running image. Safe fixed
failure codes omit exception payloads and credentials. Successful evidence binds
manifest, archive, source commit, interfaces and observed running image. The loader
admits only the verified immutable archive; Compose starts with `--wait`, and the
command observes the running image before recording success.

For rollback, invoke `upgrade rollback` with the previous release as `--manifest`
and the active release as `--previous-manifest`, with their corresponding hashes.
Only the previously recorded compatible release is allowed. `upgrade recover` uses
that same argument reversal after an interrupted activation with no running API.
Both switch binaries on the **current** workspace. Neither copies a candidate or
restores an older backup, so records written after activation remain present and
the compatible restore epoch/credentials remain unchanged. A general downgrade is
rejected; unorderable versions require release review. Schema conversion is not
supported: a schema-incompatible release is rejected rather than inventing a
migration. Any recovery needing backup restore uses the explicit clean-host restore
procedure, its new epoch/re-pair policy, and operator reconciliation of later writes.
Configuration and optional AI enablement are never changed by upgrade commands.
