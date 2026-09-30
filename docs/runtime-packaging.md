# Runtime packaging design checkpoint

CES-1068 D1, based on accepted source `8188ebfc65f5285dd1672493d775554d631b16a9`.
This is an interface/design checkpoint, not an existing image or verified install.
No external release, registry publication, deployment or host restart is implied.
The implementation must replace planned behavior below with actual evidence and
keep unsupported architecture/operational boundaries explicit.

## One replaceable runtime, one private workspace

The runtime includes the complete reviewed source bundle, not just today's
wheel. `legacy.RELEASE` resolves the bundle that contains `src`, top-level
`scripts`, `health-runner/dashboard` Python/templates/JavaScript/assets and
script helpers, maintained reference extensions/tests, contracts, documentation,
legal notices and provenance. The build context is an explicit audited allowlist;
Git history, owner workspaces, credentials, generated caches and private data
never enter it. `.dockerignore` must retain the extension worker JavaScript and
legacy scripts that the current deny-by-default file omits.

Planned image layout:

```text
/opt/health-buddy/source/       immutable complete source bundle
/opt/health-buddy/dependencies/ exact selected binary Python dependencies
/opt/health-buddy/release/      generated source/dependency manifests and notices
/workspace/                    external complete private owner workspace
/workspace/security/runtime/   host-visible, non-authoritative listener state
/tmp/                         bounded ephemeral scratch
```

A small fixed module launcher selects this source explicitly (including its
`src` directory), disables Python bytecode writes and calls the existing CLI.
It never imports code from the workspace as a backend override. The same exact
image is used for the API and explicit extension jobs. No daemon scheduler,
queue broker, Redis, Postgres, vendor setup or model call starts implicitly.
The optional `jobs` Compose profile runs the existing `extension run` command
with an explicit extension ID, private event path and scoped credential file.
It has no default scheduled event and no token in arguments or environment.

The service runs as a supplied nonzero UID/GID. The native coding agent and
maintenance CLI use that same host OS identity, with `umask 077`; health tokens
do not authorize filesystem maintenance. Fresh initialization is create-only
and validates the whole workspace root's ownership/type/mode. Existing files
are never recursively chowned/chmodded, adopted by an init container, copied
out of the image over owner files, or reset to make startup succeed. Bind mounts
use `create_host_path: false`; all canonical stores, authority, secrets, source,
assets, config, tests, notes, reviews, pending requests and fork metadata remain
inside the complete external workspace.

The initial supported ownership recipe targets an explicitly inspected native
Linux local Docker daemon without unexpected user-namespace remapping. A
rootless/remapped daemon is not assumed to preserve numeric host ownership;
qualification must establish the effective mapping before using a private
workspace. No daemon configuration is changed by the product or this ticket.

## Shared source and artifact identity

`src/health_buddy/release_identity.py` is the shared immutable value seam with
CES-1072. `ReleaseIdentity()` means unknown. The discovery service may consume
an explicitly supplied validated value; it must not accept user configuration,
health requests or environment strings as verification. CES-1068 owns readers;
CES-1072 owns the admitted discovery response and constructor injection.

Planned source reader signature:

```python
read_source_identity(source_root: Path, manifest_path: Path) -> ReleaseIdentity
```

The reader validates fixed schema/version/types and bounded relative paths,
rejects links/traversal/duplicates/unknown keys, and verifies the actual selected
bundle file inventory. Invalid or unavailable evidence produces unknown
identity for discovery; packaging verification itself fails with a safe explicit
error. No Git status, hooks, remote access, arbitrary imports or build execution
occur during discovery. Verification runs once at controlled runtime startup;
ordinary discovery consumes the injected frozen DTO and never rescans the
source tree. This is startup-snapshot evidence, not continuous attestation of
mutable source. Limit: 4,096 regular files, 64 MiB aggregate content,
16 path components and 2 MiB metadata; increase only through a reviewed contract
change if the real bundle requires it. Input file/byte counts precede allocation.

The generated source manifest, outside the source archive it describes, has:

- `manifestVersion: 1`, actual `packageVersion` and `source` containing exact
  commit/tree IDs plus the separately verified source archive SHA-256;
- `files`: sorted relative path, byte count and SHA-256 entries for the entire
  selected immutable bundle; `docsSha256` is SHA-256 of the canonical UTF-8 JSON
  list of the `docs/` entries (sorted keys, compact separators, no extra newline);
- `interfaces`: implemented API/storage/extensions/pairing/phone payload
  versions, with optional tool support absent until actually packaged;
- relative paths to the dependency lock and legal inventory, themselves covered
  by hashes. No workspace paths, health identity or personal values.

`sourceEvidence='packaged_manifest'` describes verified inventory integrity.
Commit association is backed by the controlled exact-Git-source build receipt;
it is not a signature or independent publisher attestation. Package version
alone proves neither source revision nor artifact identity. Checkouts without
that evidence keep the corresponding fields unknown.

An external release manifest records real local source archives and OCI layout
index/manifests/blobs only after they exist and every recorded digest/size has
been verified. It also records platform, package/release version, compatibility
interfaces, dependency/base input digests and actual build/execution evidence.
Local qualification is distinct from external publication. The existing
contract-only `contracts/v1/compatibility.json` remains the normative target;
this generated artifact manifest has its own schema and actual status.

Planned external reader signature:

```python
verify_artifact_identity(oci_root: Path, descriptor_path: Path) -> ArtifactIdentity
```

This is an offline verification tool, not an active-image attestation. A running
process must leave `ReleaseIdentity.artifact` absent unless its launcher has an
independently established binding to the verified artifact. In particular the
image cannot contain its own eventual digest; labels, user-supplied JSON or an
environment variable do not establish that binding. A missing ARM artifact
remains missing, never a placeholder digest or copied amd64 claim.

## Exact build inputs and actual architecture evidence

Select an actual CPython 3.12 Linux base manifest and both linux/amd64 and
linux/arm64 descriptors. Pin the resolved index/per-platform SHA-256 digests;
never use `latest` or an unresolved tag for installation. Runtime Python direct
and transitive dependencies are selected per architecture as binary wheels,
with exact versions, filenames and SHA-256 hashes. Existing application pins
remain unless a separately reviewed incompatibility requires a change.

Git is required by the canonical manual store. A slim base therefore needs an
explicitly pinned per-architecture OS Git/runtime-library closure. Exact base
and distribution package source/version/checksum/license evidence must precede
build admission; installing today's unconstrained apt packages is not pinned
packaging. The final image carries applicable base/OS/Python/native legal text
and inventories. Existing amd64 Granian/RPDS evidence is input to this process,
not proof of other-wheel, base-image or ARM notice closure.

Preferred assembly uses verified binary inputs and avoids target Rust/source
compilation. The queue first determines whether a native local builder can
export OCI artifacts and whether ARM execution is available. If emulation is
absent, building a target through architecture-neutral extraction/COPY may be
possible, but an assembled ARM image is not an executed ARM runtime. Native or
emulated execution receipts name the distinction; a physical Pi remains a
separate release qualification gate. No unapproved emulation registration,
remote builder, image pull or installation is a fallback.

The bounded source/OCI tooling writes create-only native output directories and
never invents an artifact entry when a build is incomplete. Compose uses either
an actually verified registry digest (publication separately authorized) or an
explicit verified local immutable image ID with pull disabled. A local tag is
not accepted as immutable identity just because it contains a source hash.

## Host-visible private listener and narrow restart recovery

The container-private listener tmpfs proposed in the first D1 is withdrawn:
it is not reachable as a host pathname by the external Serve proxy, and a lock
inside it cannot serialize other containers. The corrected topology uses the
**same native workspace bind** for all API containers and the host operator:

```text
native host: HOST_WORKSPACE/security/runtime/http.sock  <- host Serve/root
                               same bind-mounted inode
container:   /workspace/security/runtime/http.sock     <- Granian
shared lock: HOST_WORKSPACE/operations/http-listener.lock
```

No second mount obscures this directory. Host Serve is explicitly configured
to the real host path; Granian verifies its container path and inode. The
inspected local daemon must actually bind this host filesystem. A Desktop/VM
socket proxy with inaccessible storage is not equivalent. Both path spellings
must satisfy Unix socket length limits. The host proxy runs as the selected
service UID or a deliberately trusted root daemon; neither ordinary local
users nor a health credential gain filesystem access. No proxy container,
public TCP bypass, nsenter, daemon configuration change or hidden shared mount
is assumed. Compose does not configure Serve itself.

Fresh packaged initialization explicitly selects `security/runtime/http.sock`
and creates the empty private0700 child as the maintenance UID; configuration
accepts only this additional managed location. Existing `security/http.sock`
keeps its backward-compatible refusal of any existing path. No automatic
migration/change to an existing config occurs. All authority/epoch/store and
personal data stay persistent. The managed runtime directory contains only
this socket and fixed bounded listener metadata, never credential material.

Every supported packaged API launcher first opens the same owned private0600
`operations/http-listener.lock` with no link following, checks file identity,
and holds its nonblocking flock for the supervisor's entire lifetime. The lock
file is never unlinked or replaced. It is not the canonical writer lock and
never nests with health/security locks. A competing launcher fails before
checking or removing a socket. Explicit jobs do not take this listener lock.
This serializes launchers in different containers because the lock inode is
on their common persistent workspace bind, not container-private scratch.

Managed listener ownership is recorded in strict private0600
`security/runtime/listener.json`: schemaVersion1, generation UUID, fixed relative
socket path, captured device/inode/ctime_ns/UID/mode. Unknown keys/types, symlinks,
oversized metadata, unexpected parents/owners/modes or a changed lock inode
fail closed. The marker grants no application authority. It is atomically
written/fsynced with its parent in Granian's pinned2.8.3 startup hook, after bind
and capture but **before** workers are spawned. The current child/per-request
captured socket checks remain unchanged; raw proxy headers never prove ingress.

An existing managed socket is removed automatically only while holding the
shared launcher lock and only after all of these facts hold:

1. The complete bounded marker matches the exact relative path and current
   socket device/inode/ctime/UID/mode under the still-private owned parent.
2. A bounded AF_UNIX connection attempt returns exactly ECONNREFUSED. A
   successful connection, timeout, permission error or any other error refuses
   recovery; no HTTP request or token is sent during this probe.
3. A second nonfollowing stat confirms the identical recorded socket and
   unchanged parent/lock identities immediately before unlink. Only that exact
   path is unlinked, and the directory link removal is fsynced before rebinding.

No PID-only inference, process-name scan, broad cleanup or unconditional unlink
is permitted. Same-UID/root maintenance remains the existing trust boundary;
this protocol is not protection against a malicious process with that identity.
Normal shutdown preserves the captured-inode-only cleanup and updates/removes
only its own matching marker. A stale marker with no socket is validated before
replacement; an invalid marker is an explicit reconciliation error.

A normal container recreate or host boot leaves no live listener; the original
managed socket may persist, and the above evidence permits narrow recovery.
A parent-only kill with a surviving worker must refuse while the old listener
still accepts connections. The operator must stop that old service/container,
not start a second writer. A kill between bind and durable marker creation, a
replaced socket, ambiguous connection result, or an interrupted metadata write
remains fail-closed: the OS owner inspects/stops the exact prior container and
reconciles those exact paths deliberately. The product never claims all startup
crash windows are automatically recoverable or deletes an unrecorded socket.

Queue evidence must exercise shared-bind host reachability, two distinct API
process/container launchers sharing one lock, SIGTERM, full-container SIGKILL
and recreate after durable marker, parent-only kill with live child, startup
kill before marker refusal, wrong owner/mode, replaced/unknown path preservation,
and actual record/personal/pending-state retention. Process/container-crash
proof does not claim a physical host reboot or power-loss test. Listener socket,
lock and marker are non-authoritative runtime inventory; CES-1070 must explicitly
exclude/recreate only these declared runtime artifacts during backup/restore,
while preserving every authority/epoch/credential and personal file and rotating
the restored epoch. No backup/restore implementation is added here.

## Liveness, readiness and resource controls

Reuse existing `/livez`: validated ingress and finite `{"status":"ok"}` without
health credentials or data. Add `/readyz` through the same connection/path/
Host/proxy checks and bounded off-loop admission, returning only `ready` or
`not_ready` and HTTP200/503. It must never disclose identity, revisions, source
names, paths, grants, records or failure text. A local container health command
uses the configured UDS/loopback target and its validated headers, not an owner
credential, and bounds connection/response bytes/time.

The runtime supplies an optional readiness callback to the transport. Default
absence is not-ready. The callback checks canonical journal/manual identity and
security authority binding under workspace -> security lock order with a short
deadline; it does not mint identities, initialize security, invoke a provider,
scan personal code or read optional histories. Recovery remains the canonical
service's startup/operations responsibility. An unresolved durable decision is
not ready; an unavailable optional source with intact core state does not make
manual logging unready. A required cross-store recovery failure does.

API and jobs share one canonical writer/journal. Compose uses read-only release
filesystem, no capabilities, no-new-privileges, an init/reaper, finite memory/
CPU/PID/tmpfs budgets and a stop grace longer than the serving child's bounded
shutdown. Core Compose has no public TCP port, no Docker socket mount and no
optional egress by default. Tailnet HTTPS/Serve is an explicitly configured
external boundary, not automatically installed or enabled. Optional egress needs
a deliberate separate profile/override and never activates a provider itself.
An unhealthy status reports state; Compose restart policy is not falsely
claimed to restart an unhealthy but running process.

## Ownership and evidence plan

CES-1068 owns `release_identity.py`, packaging/readiness/lifecycle helpers,
Dockerfile/build context, runtime Compose/locks/manifests/scripts, related source
wiring and runtime tests/docs. CES-1072 owns the optional `[mcp]` extra,
`health-buddy-mcp` entrypoint, shared-tool modules, safe discovery projection and
MCP client namespace. Coordinate any shared pyproject/Service/Runtime edits;
no lane edits the other's checkout. Core packaging does not require MCP extras.

D2 verification through the sole queue must establish: complete bundle/import
and asset availability; strict manifests and actual immutable digests; both
architecture builds and separately labeled execution; non-root ownership and
read-only image; no-key/no-vendor startup; readiness/degraded states; shutdown/
crash/recreate; native editing + explicit review without image rebuild; same
canonical records and unchanged personal source/config/tests/notes/state across
replacement; pending-before-send and committed-before-receipt replay with the
same key/envelope/cursor; all baseline functional/auth/browser/package/privacy
checks appropriate to changes. Existing manual-only CI policy stays unchanged;
local package/runtime commands become documented repeatable targets, with
hosted container execution held for the authorized publication batch.

Primary references consulted for this design:
[Compose service controls](https://docs.docker.com/reference/compose-file/services/),
[host bind mounts](https://docs.docker.com/engine/storage/bind-mounts/),
[tmpfs visibility limits](https://docs.docker.com/engine/storage/tmpfs/),
[Granian2.8.3 startup ordering](https://github.com/emmett-framework/granian/blob/v2.8.3/granian/server/common.py),
[immutable base pins](https://docs.docker.com/build/building/best-practices/), and
[multi-platform building and execution choices](https://docs.docker.com/build/building/multi-platform/).
These describe mechanisms, not verification of this not-yet-built runtime.
