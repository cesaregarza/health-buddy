# Compatible upgrade staging checkpoint

The native owner command prepares a candidate without changing a running service,
container, active workspace, or unrelated host service. It is the first recovery
checkpoint; activation and rollback are not implemented by this checkpoint.

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

The candidate receipt describes the selected target and explicitly says activation
is pending. It does not claim that the target image is installed or running. Never
serve the original and candidate as concurrent writers. Keep the original intact
until the supported activation checkpoint is available and verified.
