# Encrypted backup and clean-host restore

These native owner commands back up the private workspace, not the replaceable
runtime image. Stop external personal editors, manual Git maintenance, imports
and independently scheduled connector jobs before confirming quiescence. The
canonical backup context holds the shared writer/security lock while taking the
snapshot; hashing and directory-change checks reject detected external edits.
This is a supported-writer consistency guarantee, not an arbitrary filesystem
snapshot of an actively edited tree. Never copy a running workspace with `cp`.

Use an existing owner credential with `operations:admin`. An ordinary read/write
agent grant cannot create a backup; no HTTP/MCP command performs maintenance.
Choose owned native Linux directories with mode 0700 and files with mode 0600.
The key is 32 CSPRNG bytes; it is not a password and never appears on stdout.
Keep the key separate from the workspace, archives and their storage account.

```sh
health-buddy --workspace "$WORKSPACE" backup keygen --key-file "$KEY_FILE"
health-buddy --workspace "$WORKSPACE" --credential-file "$OWNER_FILE" \
  backup create --key-file "$KEY_FILE" --archive "$NEW_ARCHIVE" --confirm-quiesced
health-buddy --workspace "$WORKSPACE" backup verify \
  --key-file "$KEY_FILE" --archive "$NEW_ARCHIVE"
```

`create` never overwrites an archive. It verifies the authenticated encrypted
bytes and inventory before publishing a mode-0600 file. The envelope is version 1,
AES-256-GCM with a fresh 96-bit nonce and full 128-bit tag, using the maintained
[PyCA authenticated encryption API](https://cryptography.io/en/latest/hazmat/primitives/aead/).
The fixed envelope header is authenticated; wrong keys, altered headers/nonces,
truncated ciphertext and changed tags fail closed. There is no homemade cipher,
password derivation or OpenSSL ABI wrapper.

The archive contains configuration, dataset/revision/identity metadata, every
canonical CSV/JSON/plan/private Git object and reference, HealthKit database and
sidecars, journal, security authority, private secrets, enabled optional source
stores, and the complete personal tree: extension source/assets/config/tests/
notes/migrations/state, reviewed snapshots, locks, forks and unknown owner files.
Configured optional stores live within the workspace. Missing enabled source
files or required authority metadata fail verification. The configured cache is
regenerable and excluded, as is the live configured Unix socket. Other symlinks,
special entries, public/foreign-owned files and hard-linked files fail closed;
no owner file is silently dropped. A private ZIP inventory names each captured
file's length and SHA256, exact directories, authoritative required paths,
identity and revision. Neither the inventory nor health values are in plaintext
outside the encrypted archive.

Version 1 caps plaintext archives at 256 MiB and 8192 workspace entries and
uses memory for complete authenticated encryption/decryption. Size-limit errors
preserve the source and publish no archive. Plan sufficient memory (archive
bytes plus encryption/verification copies) and free disk (at least twice restored
file bytes plus a 64 MiB safety margin). A full archive is authenticated before
any workspace is staged. Disk checks are estimates; an actual write failure
still preserves the original workspace and prevents publication.

For a clean host, install compatible verified runtime/source artifacts first;
keep the receiver unexposed and bring the key and encrypted archive through a
private channel. Select an **absent** workspace path under an owned private
native parent, rather than an existing directory to merge or empty. Restore is
local OS-owner maintenance and does not take an old health credential.

```sh
health-buddy --workspace "$ABSENT_WORKSPACE" backup restore \
  --key-file "$KEY_FILE" --archive "$ARCHIVE" --confirm-revoke-all
```

The command validates the full authenticated inventory, writes a private sibling
staging tree, verifies canonical identity/revision and receiver bindings,
advances `restoreEpoch`, refreshes the authority/security epoch, fsyncs the tree,
and publishes the restored workspace. The original installation/dataset IDs,
record/stream/object IDs, tombstones, plans and configuration are preserved;
health revision remains the archive's revision. The canonical marker changes in
its authoritative Git adapter. Corrupt/incomplete/wrong-key/low-disk failures
never modify the original workspace or an existing destination. A destination
that appears concurrently is not replaced. Restore never downloads or executes
personal extensions, manifests, tests, hooks or migration commands.

The private result names `ownerCredentialReference`, a newly generated owner
handoff under `secrets/`. Old owner/device/agent/session/pairing credentials and
proofs do not enter the active replacement authority. The old authority is
retained privately in the existing retired-state convention as evidence; its
credentials remain invalid. Agent declarations and device lineage are retained
for deliberate recovery, rather than silently giving an old token new access.
A current owner must review and rotate each retained active agent grant or revoke
it. Previously inactive/revoked agent declarations cannot be rotated implicitly.
The archive may predate a revocation; **no old token** is restored to active use.
Keep the restored service private until that review is complete.

Explicitly re-pair each phone with the same device UUID and its retained
predecessor from `devices.list`; it preserves the canonical source stream.
Never pretend old anchors/checkpoints belong to the fresh epoch. Reset phone
receiver-scoped checkpoints, replay available stable-ID history, then advance
anchors only after acknowledged sync. A checkpoint newer than the backup cannot
prove that those newer observations exist in the restored receiver. Existing
stable observations reconcile once, missing later observations catch up, and
same-batch retries remain idempotent. If device history is gone, report the
history gap; restoring a backup cannot recover data it never contained. This
repository's protocol tests are synthetic, not physical iPhone qualification.
Use the [pairing contract](authorization.md) and supported companion workflow.

For a prepared connector, preserve its original source and actor binding; create
a new private handoff path and deliberately rotate that retained actor:

```sh
health-buddy --workspace "$WORKSPACE" --credential-file "$NEW_OWNER_FILE" \
  extension prepare --id local.water-import --source-id "$ORIGINAL_SOURCE" \
  --credential-reference secrets/rekeyed-water --rotate-existing
```

Installed/reviewed extension source/config/tests/notes and state remain intact.
Old pending/completed client envelopes and cached receipts stay tied to their
old identity/actor namespace and cannot be presented as current success.
Review original event IDs and canonical records before deliberately creating
new work. Do not delete retry evidence to bypass `client_identity_changed`,
blindly replay a stale envelope, or rename an event merely to force a repeat.
New explicit events work after re-key; pending-intent reconciliation is an owner
review boundary. Disable a broken addition while preserving its files; run its
reviewed synthetic tests through the supported verification workflow before
reactivating incompatible or edited code. Core use requires no agent or site.

Scheduling and retention are owner policy, not an automatic daemon. Use a
private host scheduler with a bounded single job and explicit quiescence;
choose a daily/weekly interval based on acceptable data loss. Keep several
verified generations, including one on separate failure-domain storage. Only
remove the oldest archive after a replacement was verified and a periodic
clean-host restore rehearsal succeeds. Check backup age and free space; a
failed backup never counts as fresh. Retain keys for every retained generation,
store a separate recovery copy offline, and deliberately create a fresh key
for new archives when rotating keys. Existing archives remain readable only
with their original key. This ticket adds no automatic deletion or cutover.
