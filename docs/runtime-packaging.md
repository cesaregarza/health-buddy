# Immutable runtime and private workspace

CES-1068 supplies source packaging, a pinned binary-input context, one API/job
image, Docker archive verification, Compose and a repeatable synthetic runtime
qualification command. The presence of these files is not evidence that an
image was built. Exact check/build receipts establish tested commits and actual
AMD64/ARM64 artifact bytes. Candidate artifacts remain private; this workflow
neither publishes a registry image nor creates an external release.

## Source and runtime map

| Source | Responsibility |
| --- | --- |
| `runtime/bundle.py`, `runtime/manifest.py` | Exact Git commit/tree, complete bounded source archive, safe inventory and startup verification |
| `packaging/runtime-inputs.json`, `runtime/inputs.py` | Fixed per-architecture base/wheel/Debian inputs and explicit hash-checked download |
| `runtime/context.py`, `packaging/runtime.Dockerfile`, `install_runtime.py` | Complete build context and offline binary assembly |
| `runtime/artifact.py`, `runtime/release.py` | Bounded Docker-save archive admission, actual artifact manifest and fixed-ID loading |
| `packaged_runtime.py`, `scripts/runtime_entrypoint.py` | Explicit nonroot API/CLI/job/health entrypoints |
| `transport/listener.py`, `security/readiness.py` | Shared listener lifetime and read-only core readiness |
| `packaging/compose.yaml` | Same immutable image for API and explicit finite jobs |
| `packaging/verify_runtime.py`, `runtime_synthetic.py` | Admitted isolated-runner build/replacement/retry qualification |

The image contains the complete selected source: `src`, top-level scripts,
legacy dashboard Python/templates/JavaScript/assets, maintained personal
extension examples/tests, contracts, documentation, notices and provenance.
It excludes Git history, dirty/untracked files, owner workspaces and credentials.
Git replacement objects are disabled. Newly created immutable source files are
0644, tracked executables 0755 and directories 0755, even under umask 077; the
outer native staging directory is private 0700. Existing files are unchanged.
The root `.dockerignore` refuses a direct repository build; the supported command
creates a separate verified context with an explicit allowlist.

```text
/opt/health-buddy/source/        complete immutable source
/opt/health-buddy/dependencies/  exact binary Python closure
/opt/health-buddy/release/       source/input/installed-resource evidence
/workspace/                    complete external private owner workspace
/tmp/                          bounded ephemeral scratch
```

The fixed Python launcher uses `-I -B`. A root-owned fixed `.pth` makes only the
immutable dependencies/source available to isolated Python extension children.
The reviewed JavaScript view runs in the browser WebWorker; Node is not a
runtime dependency. No vendor, model key, scheduler, broker or optional database
starts implicitly. The core image does not require optional MCP dependencies;
shared discovery is integrated and verified before final combined artifacts.

## Exact source and artifact identity

`verify_source_identity(source, manifest)` checks the complete file inventory,
source archive binding, package/interface versions and canonical docs hash.
Limits are 4,096 files, 8,192 entries, 64 MiB source content, 16 path components
and 2 MiB metadata. It rejects links, traversal, duplicate/unknown fields and
changed inputs. The inventory ignores interpreter bytecode (`__pycache__`
directories, `*.pyc` and `*.pyo` files) because the installer imports the
extracted bundle in place; a link with one of those names still fails. Bundle
creation refuses a commit that tracks bytecode (`bytecode_source_entry`), and a
mismatch reports the first differing path relative to the bundle. Runtime
construction verifies the immutable bundle once and injects `ReleaseIdentity`;
ordinary discovery consumes that frozen value.
This proves a startup snapshot, not continuous mutable-checkout attestation.
The controlled Git build receipt proves commit association. A user configuration
value, image label or version string alone is not independent publisher proof.

The candidate manifest is generated only after both actual
`health-buddy-linux-amd64.docker.tar` and
`health-buddy-linux-arm64.docker.tar` files exist and verify. It records their
actual hashes/sizes, source commit/tree/archive, input lock and implemented
interface versions. `SHA256SUMS` covers the actual artifacts, the manifest
and `health-buddy-bundle.tar`, which the same `manifest` command writes: the
verified source bundle a fresh host installs from, one top-level `bundle/`
directory holding `source/` and `release/{source.tar,source-manifest.json}`,
packed with zero owner, group and mtime so the same bundle always yields the
same bytes. The manifest does not list it; the
[bootstrap](install-preflight.md#before-the-first-stage) checks it against an
owner-confirmed SHA-256. There are no placeholder digests or fabricated download
URLs. This is Docker `image save` format, not an OCI-layout directory or
published registry index.

Archive admission checks physical tar headers before parsing, bounded regular
entries and metadata, one Docker `manifest.json` image, config filename/content
hash, Linux architecture, source labels and ordered rootfs diff IDs. Classic
uncompressed layers and narrowly supported OCI descriptor/blob exports are
accepted. For an OCI-shaped save, the index/layout and Docker manifest must
select the same sole platform/config/layer sequence. Stored compressed layer
SHA-256 is distinct from the bounded uncompressed diff ID; gzip is supported.
Unsupported compression, PAX/GNU extended headers, sparse/link/special entries,
extra images/referrers, unreferenced content, repository tags and OCI name/ref
annotations are refused. A fixed-ID, single-platform, provenance/SBOM-disabled
save avoids unrelated daemon tagging. Archive verification performs no extraction.
Limits: 1 GiB archive, 256 tar entries, 128 layers, 2 MiB total JSON metadata,
512 MiB expanded per layer and 2 GiB expanded aggregate. These limits are
explicit compatibility constraints; an unsupported actual producer is reported,
never silently loaded through a weaker path.

Docker classic storage commonly uses a config digest as image ID. Containerd
storage may use a validated manifest/index digest instead. The artifact DTO
therefore retains the config digest, descriptor-derived eligible IDs, stored
layer digests and diff IDs separately. After loading, the loader inspects the
actual resolved immutable ID, Linux platform and rootfs, and supplies that exact
ID to Compose. Build producer ID and loaded ID remain separate receipt fields.
A process cannot embed its own eventual artifact digest; running discovery leaves
artifact identity unknown until an independently established binding exists.

## Build and local loading

The lock pins CPython 3.12.14, the real multi-architecture base index/platform
manifests and binary wheels, plus Git and its finite Debian package closure.
Debian selection is backed by retained signed repository metadata for an explicit
snapshot. Historical signature validity does not assert current vulnerability
or release qualification. Downloads require exact HTTPS hosts, sizes and hashes;
no ambient proxy, resolver, source build or startup download is used. Approved
same-host redirects close the prior response without draining its body; redirect
count and URL bounds remain enforced. The entire download runs in an owned isolated worker. A 240-second parent
deadline covers connection, headers, redirects, chunk framing and body reads;
on expiry the parent kills that process group and reaps it with a separate
two-second cleanup bound. Socket timeouts/body checks are secondary controls,
not a claimed absolute urllib deadline. Failed partial output is retained
and never reused implicitly.

A native maintainer can inspect source and create a bundle without Docker:

```sh
python scripts/package_runtime.py bundle --repository "$REPO" --revision "$EXACT_COMMIT" --output "$STAGING/bundle"
python scripts/package_runtime.py fetch-inputs --lock "$STAGING/bundle/source/packaging/runtime-inputs.json" --architecture amd64 --directory "$STAGING/inputs"
python scripts/package_runtime.py context --bundle "$STAGING/bundle" --downloads "$STAGING/inputs" --architecture amd64 --output "$STAGING/context"
```

All destinations are create-only native directories. The Docker RUN installs
only verified local Debian packages and hashed binary wheels with network none;
there is no `apt update`, unconstrained pip install, Rust fallback or model setup.
Actual image legal files and shipped wheel SBOMs remain in the image. The final
qualification captures those texts/hashes and exact installed versions; a
wheel-shipped SBOM is not proof every listed crate is linked. Source notice
closure alone does not establish complete final-image license closure.

`runtime-candidate.yml` runs only on manual dispatch or on a push to a
`validation/ces1068-*` ref, under the publication policy in
[AGENTS.md](../AGENTS.md#checks-and-publication). It uses native
`ubuntu-24.04` and `ubuntu-24.04-arm`, one matrix job at a time,
25-minute jobs and read-only repository permission. Ordinary branches and PRs
cannot trigger it. The special `validation/ces1068-*` ref is a deliberate
publication gate, needed when the unmerged workflow is absent from the
default branch; it is not permission to merge. No Apple build or external image
publication occurs. Artifacts expire after 14 days; download and retain the
accepted manifest/archive/receipt set in the private release inventory before
expiry. Failing diagnostic artifacts expire after seven days.

Each build requires six GiB free initially and preserves a one-GiB reserve.
The installer checks actual cgroup-v1 or cgroup-v2 memory (at most 2 GiB) and
CPU quota (at most one CPU) before installing inputs. These are Docker RUN limits,
not a claim to constrain the Docker daemon, export buffers or BuildKit cache.
The isolated runner VM and job timeout bound that wider workload. No local
Docker build is admitted merely because its CLI runs in a cgroup.

The manifest loader accepts an explicitly inspected native executable and local
Unix-socket daemon only. The operator must establish native storage/ownership
and safe CLI/plugin discovery before invocation; the loader does not reconfigure
Docker, install emulation or silently use a remote context. After admission:

```sh
python scripts/package_runtime.py load --manifest "$ARTIFACTS/runtime-manifest.json" --architecture amd64 --workspace "$OWNER_WORKSPACE" --uid "$SERVICE_UID" --gid "$SERVICE_GID" --output-env "$PRIVATE/runtime.env"
docker compose --env-file "$PRIVATE/runtime.env" -f "$SOURCE/packaging/compose.yaml" up -d api
```

Compose uses that verified immutable ID with `pull_policy: never`. It never
resolves a mutable tag such as `latest`. Each service has a read-only image,
nonzero owner UID/GID, no capabilities, no-new-privileges, network none, an init
process, one CPU, 512 MiB memory, 128 PIDs and 64 MiB temporary scratch. One API
plus one finite job has an explicit aggregate two-CPU/one-GiB service budget.
The host image/daemon cache is separate. Optional connectors needing network
access require a separately reviewed network policy; a health grant cannot
bypass the container's network isolation.

## Private ownership and explicit maintenance

The complete owner workspace is an existing native 0700 directory owned by the
selected nonzero host UID/GID. The native coding agent, CLI and container service
use that OS identity and `umask 077`. Health credentials do not grant filesystem
maintenance authority. The default recipe assumes an inspected local daemon
without unexpected UID remapping; rootless/remapped storage needs its own
ownership qualification. No recursive chown/chmod, automatic adoption or init
container resets existing data. Bind mounts use `create_host_path: false`.

The fresh packaged initializer accepts only an empty private directory:

```sh
docker compose --env-file "$PRIVATE/runtime.env" -f "$SOURCE/packaging/compose.yaml" run --rm api init --external-origin https://health.example.invalid --owner-subject owner@example.invalid
docker compose --env-file "$PRIVATE/runtime.env" -f "$SOURCE/packaging/compose.yaml" run --rm api cli -- security bootstrap --owner-token-file /workspace/secrets/owner-token
```

The hostname/subject above are synthetic placeholders. The commands do not
configure TLS, Tailscale or Serve. Native owner setup and browser/proxy setup
remain explicit; see [authorization](authorization.md). The proxy must already
use the exact configured HTTPS origin and a currently reviewed patched version.
Existing configurations are not silently changed to enable managed sockets.
The canonical CLI's global options precede its command:

```sh
docker compose --env-file "$PRIVATE/runtime.env" -f "$SOURCE/packaging/compose.yaml" run --rm api cli -- --credential-file /workspace/secrets/owner-token context --scopes training
```

The wrapper accepts one optional `--` delimiter, forwards legitimate credential
options, and refuses workspace/development overrides; canonical abbreviation
is disabled. No token is passed as an argument or environment value.

Personal source/assets/config/tests/notes/state, registry reviews, pending
requests, credential references and fork patches stay under the external
workspace. Edit as its OS owner, inspect changes and explicitly review/enable
an extension; do not overwrite it with image files. The `jobs` profile runs a
finite existing connector event, with no implicit schedule:

```sh
docker compose --env-file "$PRIVATE/runtime.env" -f "$SOURCE/packaging/compose.yaml" run --rm jobs job --id local.water-import --event-file /workspace/personal/state/event.json --credential-file /workspace/secrets/water-agent
```

Prepare that private scoped credential and enable the reviewed extension using
[the native extension workflow](extensions.md). API and jobs share the same
canonical journal and whole workspace. Provider setup remains disabled unless
separately configured and authorized.

## Host-visible listener and read-only readiness

The external host Serve proxy and all API containers see the same bind-mounted
`OWNER_WORKSPACE/security/runtime/http.sock`; there is no container-private
socket tmpfs, public TCP bypass or hidden proxy container. Both host and container
path lengths must fit Unix socket limits. A shared persistent
`operations/http-listener.lock` serializes launcher lifetimes across containers;
it is never replaced/unlinked and is separate from the canonical writer lock.
A competing launcher fails before touching the socket. Jobs do not take it.

The managed socket's private `security/runtime/listener.json` records its exact
path, generation, device/inode/ctime/UID/mode. Recovery requires unchanged private
ancestors/lock/marker, a matching socket, a bounded connection returning exactly
ECONNREFUSED and a second nonfollowing identity check. Only that proven stale
socket is removed. The old marker is durably retired before every subsequent
bind, even when the old socket is absent; inode/ctime reuse is not proof of a
new socket's ownership. Fsync failure refuses startup. An active/ambiguous or
replaced socket, missing/mismatched marker, bind-before-marker crash, or live
child after parent-only kill stays fail closed. The OS owner deliberately stops
and reconciles that exact service; there is no broad cleanup. Existing legacy
`security/http.sock` retains its fail-closed behavior. Same-UID/root native
maintenance remains trusted and is not a hostile-code sandbox.

`/livez` reports admitted transport liveness. `/readyz` returns only HTTP200
`ready` or HTTP503 `not_ready`, through the same UDS/Host/proxy admission.
Readiness uses a bounded read-only workspace→security lock sequence, validates
existing canonical journal/main-ref/authority identity and refuses pending or
corrupt core state. It neither initializes/recovers/writes state nor scans
personal code, invokes models or reads optional histories. Valid symbolic or
detached Git HEAD metadata is accepted independently of explicit main-ref/journal
CAS binding; it does not follow HEAD's referent. Optional source failure does
not make intact manual workflows unready. An unhealthy status does not itself
cause Compose to restart a still-running process.

## Replacement, evidence and downstream gates

`make runtime-test runtime-lint` and normal configured typing are repeatable
source gates. The isolated native container driver additionally checks real
readonly/nonroot/service limits,
actual host-visible UDS, protected dashboard/worker assets, same-image concurrent
API/finite jobs, full container kill/recreate, service stop/recreate and personal
source/config/assets/tests/notes survival. Its two synthetic interruption cases
exit before send and after canonical commit before receipt retention; fresh jobs
must retain the original normalized envelope/key, reconcile the immutable receipt,
advance one cursor and leave exactly one record across replacement.

A service/container restart is not an actual host reboot or power-loss test.
Physical ARM/Pi qualification, host reboot, external proxy deployment,
production migration/cutover, external release and real cross-agent client
qualification remain separately owned gates. CES-1070 backup/restore must
preserve the whole authority/health/personal workspace, explicitly exclude and
recreate only declared listener socket/marker/lock runtime artifacts, and rotate
the restored epoch. This ticket does not implement restore or authorize a cutover.
Private artifacts and receipts must be retained before CI expiration; no published
version is inferred from a successful local or hosted verification candidate.
