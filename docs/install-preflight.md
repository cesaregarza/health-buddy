# Native installer preflight: first dry-run tracer

CES-1077 first provides read-only preflight for an explicitly selected native
Linux host. It makes no downloads, Docker calls, SSH calls, Tailscale changes,
service writes, workspace initialization or agent/client connection. Checkpointed
installation and recovery remain pending until the verified CES-1071 interface
is available. Named-client acceptance remains open in CES-1073/1074.

Select an already retained immutable runtime-manifest.json, its two image
archives/source archive and matching verified source bundle. Obtain the expected
manifest SHA256 independently through the publisher trust channel; copying the
hash from untrusted downloaded content does not establish trust. This tracer
checks an operator-supplied pin and existing archive/source verifiers, not a
publisher signature or external release qualification. Do not execute arbitrary
release scripts merely because this dry-run accepts archive integrity.

Supply an existing native mode-0700 owner workspace outside the source bundle.
An empty workspace is reported as `empty_not_initialized`. Any existing entry
produces `existing_state_requires_review`, without reading config, personal notes,
secrets or health records. Diagnose existing installations with
[operator status/doctor](operator-diagnostics.md), then use the verified upgrade
path; never empty a workspace to make this gate pass. Future installation must
retain persistent personal source, config, assets, extension notes/tests/state,
and reconnect agents to matching source/documented development commands.
Nonzero service UID/GID and ownership admission are still required before runtime
loading, as described in [runtime packaging](runtime-packaging.md).

```sh
export PYTHONPATH="$SOURCE/src"
"$PYTHON" -m health_buddy.install_preflight --bundle "$BUNDLE" --manifest "$ARTIFACTS/runtime-manifest.json" --trusted-manifest-sha256 "$TRUSTED_MANIFEST_SHA256" --workspace "$OWNER_WORKSPACE" --docker "$INSPECTED_NATIVE_DOCKER"
# Optional separate host listener check, not a published runtime port:
"$PYTHON" -m health_buddy.install_preflight --bundle "$BUNDLE" --manifest "$ARTIFACTS/runtime-manifest.json" --trusted-manifest-sha256 "$TRUSTED_MANIFEST_SHA256" --workspace "$OWNER_WORKSPACE" --docker "$INSPECTED_NATIVE_DOCKER" --port 8791
```

The supported architecture mapping is x86_64→amd64 and aarch64/arm64→arm64, native
Linux only; 32-bit Raspberry Pi OS and emulation are refused. Initial planning
bounds are two logical CPUs, 2 GiB physical RAM and 6 GiB free workspace space.
These are conservative first-tracer policy bounds, not measured capacity for
personal history. Free space on the Docker data root, staging filesystem and
future data growth still require separate admission. Physical host facts do not
prove cgroup/container quotas. A present native Docker executable and local
socket are metadata checks only: daemon reachability/version, Compose discovery,
rootless/remapped ownership and CLI/plugin trust remain unqualified. Nothing is
started or reconfigured automatically.

JSON schema 1 returns fixed actionable diagnostic codes, architecture/resource
facts, archive/source verification state and future requirements. It emits no
selected private paths, config values, identities, records or credential bytes.
Exit 0 means the measured preflight checks passed; exit 2 means a refusal.
`installed` and `readyForActivation` remain false even on success. An optional
loopback port check reuses doctor's temporary bind probe and releases it; this
does not reserve a port or terminate an existing listener. The current runtime
uses network-none containers and a private Unix socket, with no host port
published by Compose. Phone/private HTTPS reachability remains unknown.

Queue-owned focused validation from the immutable source checkpoint:

```sh
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m pytest -p no:cacheprovider tests/test_install_preflight.py tests/test_operator_diagnostics.py
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 "$PYTHON" -m ruff check src/health_buddy/install_preflight.py src/health_buddy/operator_diagnostics.py tests/test_install_preflight.py
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 "$PYTHON" -m ruff format --check src/health_buddy/install_preflight.py src/health_buddy/operator_diagnostics.py tests/test_install_preflight.py
"$PYTHON" -m mypy src/health_buddy/install_preflight.py src/health_buddy/operator_diagnostics.py
```

Synthetic fixtures reuse the existing real archive/source verifiers, check pin
and archive tamper refusal, preserve existing personal files on refusal, reject
linked targets and occupied ports, and prove that repeated dry-runs create no
installation files. This is not actual host installation, interruption/resume,
Docker runtime activation or one-URL product acceptance. Later steps require the
frozen upgrade seam, publisher trust admission, private routing/ownership and
accepted matching guide/client source rather than improvised host modifications.

## Durable local preparation checkpoint

After preflight, `health_buddy.install_prepare` can prepare the real empty local
workspace and matching source references without loading/starting a runtime.
The journal must be an explicit mode-0600 file in an existing private native
mode-0700 directory **outside** the workspace and release bundle. Keep that
journal and the originally pinned bundle/artifacts across restarts.

```sh
"$PYTHON" -m health_buddy.install_prepare --journal "$PRIVATE_INSTALL/install.json" --bundle "$BUNDLE" --manifest "$ARTIFACTS/runtime-manifest.json" --trusted-manifest-sha256 "$TRUSTED_MANIFEST_SHA256" --workspace "$OWNER_WORKSPACE" --docker "$INSPECTED_NATIVE_DOCKER"
```

The first write durably binds the original workspace, source bundle, release pin
and inspected Docker path before invoking the existing create-only initializer.
A stopped invocation can repeat the same command: known initialization files are
retained, then the source/guide/development-lock/catalog/test pointers are retained
in private `personal/INSTALLATION.json`. A resume refuses changed target binding,
release bytes or locally edited source profile. Unrelated owner notes, extensions
and stores remain in the workspace. No unrelated existing workspace is adopted
without its original preparation journal. Keep external writers/editors stopped
during initialization; this does not authorize recursive chown/chmod or manual
rewriting of a journal to adopt an unrelated installation.

Source preparation reuses the frozen CES-1071 pinned-release/personal-compatibility
preflight. Existing installations still use [backup/staging and recoverable
upgrade](recoverable-upgrade.md), never this empty-workspace initializer. No new
migration, restore identity, backup implementation or runtime API is introduced.
`prepared` means local workspace/source files exist; it does not mean a container,
private HTTPS origin, owner security, client or phone is operational. Default
workspace config remains local/native until the owner separately configures the
reviewed private ingress/security workflow. Nonroot UID/GID/runtime ownership,
loader/Compose admission and activation remain pending.

After the owner independently establishes the matching private origin/authority,
an explicit reviewed MCP adapter/settings file and credential may be supplied:

```sh
"$PYTHON" -m health_buddy.install_prepare --journal "$PRIVATE_INSTALL/install.json" --bundle "$BUNDLE" --manifest "$ARTIFACTS/runtime-manifest.json" --trusted-manifest-sha256 "$TRUSTED_MANIFEST_SHA256" --workspace "$OWNER_WORKSPACE" --docker "$INSPECTED_NATIVE_DOCKER" --client codex --client-config "$CONFIG" --skill-directory "$SKILL_DIRECTORY" --settings "$PRIVATE_SETUP/adapter.json" --python "$PYTHON"
```

Use `--client claude` with the `.mcp.json` private launcher and supported skill
location from [Claude setup](claude-integration.md), or [Codex setup](codex-integration.md).
Preparation verifies the settings receiver tuple/origin matches this retained
workspace before reusing the accepted connect-agent helper; it does not create
agent grants, read bearer bytes, call a model or authenticate the configured token.
The private journal binds that one chosen client configuration; a later change of
client/target requires deliberate lifecycle review rather than silent adoption.
The existing helper refuses unowned edits and interrupted partial multi-file
setup; inspect/reconcile those owned files explicitly instead of overwriting them.

Structured phase output distinguishes `clientConfigurationPrepared` from
`connected:false` and `installed:false`. Configuration evidence does not close
fresh named-client acceptance. A fresh native maintenance session can read the
private saved source profile/journal and existing guide, preserving one canonical
extension-maintenance path. Don't expose these private path-bearing files through
health tools, public logs or chat.

Queue-owned targeted checkpoint checks:

```sh
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m pytest -p no:cacheprovider tests/test_install_prepare.py tests/test_install_preflight.py tests/test_agent_guide.py
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 "$PYTHON" -m ruff check src/health_buddy/install_prepare.py tests/test_install_prepare.py tests/test_install_preflight.py
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 "$PYTHON" -m ruff format --check src/health_buddy/install_prepare.py tests/test_install_prepare.py tests/test_install_preflight.py
"$PYTHON" -m mypy src/health_buddy/install_prepare.py
```

The synthetic flow interrupts after create-only initialization and after source
profile publication, then resumes identical input without changing canonical
identity/revision or owner notes. Separate Codex/Claude configuration cases reuse
real helper writes and refuse a changed restore epoch, without client/model/daemon
execution. Existing verified guide/client/upgrade proofs are retained separately;
this checkpoint does not rerun whole native/runtime/device qualification.

## Explicit runtime activation checkpoint

`health_buddy.install_activation` is the next owner-controlled action. It contacts
only the explicitly admitted native CLI and local Unix Docker socket. Preparation
and preflight commands still perform no activation. No tool or queue receipt here
establishes an actual deployment: this checkpoint is verified with synthetic
subprocess responses only.

Preparation alone does not establish runtime readiness. A default prepared
loopback workspace refuses activation with
`install_activation_requires_managed_owner_setup`; missing authority refuses
with `install_activation_requires_ready_owner_authority`, before any daemon
contact. Recovery is the existing reviewed managed UDS configuration and explicit
OS-owner security initialization, preserving this same workspace. The guided
owner command below supplies that local setup before activation. It creates no
agent grant or Tailscale sign-in.

Run preparation and activation as the private workspace's nonroot OS owner, with
that existing workspace's nonzero UID/GID. The workspace must already be mode
0700 with matching ownership. The installer never changes ownership, repairs
permissions or adopts an existing installation. Independently inspect/admit the
local Docker CLI, Compose plugin, daemon, quotas and owner-controlled Docker
socket access first; do not expose an engine remotely. Keep writers, editors and
other operations on the selected project stopped during this action.

```sh
"$PYTHON" -m health_buddy.install_activation --journal "$PRIVATE_INSTALL/install.json" --environment "$PRIVATE_INSTALL/runtime.env" --project health-buddy-personal --uid "$OWNER_UID" --gid "$OWNER_GID" --confirm-local-daemon --confirm-quiesced
```

The environment file must be new and outside both workspace and source bundle.
The original preparation journal supplies the pinned manifest, native Docker
path and persistent workspace. Before any daemon observation/action, activation
retains the workspace receiver identity, immutable target, selected project,
environment path, UID/GID and matching maintained Compose digest. It refuses a
project containing any existing container at first admission. It never stops or
removes an unrelated service. Only the maintained `api` service is started; its
Compose profile keeps jobs separate and pulls disabled.

`admitting` → `loading` → `starting` → `active` are durable stages. Repeat exactly
the same command after interruption: loading reuses the verified tag-free archive
loader, and a lost start acknowledgement is reconciled against the exact running
image, workspace mount, runtime user and healthy status before accepting success.
A `starting` or `unhealthy` observation returns retryable
`install_activation_runtime_not_healthy`; repeat only observes the pending runtime
and never issues another load/up while that bound container exists. An already
active repeat only observes that binding. An absent recorded active runtime,
stopped/unrelated container, edited environment, changed identity/release or
changed project/UID/GID refuses further writes and requires owner inspection.
Retain the original journal and environment; do not edit them to adopt another
runtime. No canonical workspace, personal note or configuration is replaced.

CLI failures expose fixed stage codes, such as
`install_activation_load_interrupted`, `install_activation_start_interrupted`,
`install_activation_project_not_empty` and
`install_activation_environment_changed`, with no paths or captured daemon
output. `runtimeActivated:true` records only the observed local API runtime.
`connected:false` remains explicit: private HTTPS/Tailscale sign-in, owner grants,
phone pairing and named-client qualification are still pending. The source
profile's `runtimeActivated:false` remains its preparation-time description;
current activation evidence lives in the private journal's `activation` record.

Queue-owned activation checks:

```sh
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m pytest -p no:cacheprovider tests/test_install_activation.py tests/test_install_prepare.py
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 "$PYTHON" -m ruff check src/health_buddy/install_activation.py tests/test_install_activation.py tests/test_install_preflight.py
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 "$PYTHON" -m ruff format --check src/health_buddy/install_activation.py tests/test_install_activation.py tests/test_install_preflight.py
"$PYTHON" -m mypy src/health_buddy/install_activation.py
```

The synthetic tracer executes real preparation, archive loading logic and
maintained Compose/inspection command construction through bounded fake CLI
responses. It simulates nonroot workspace metadata without changing host
ownership, interrupts load/start acknowledgements, then repeats without another
start or changed canonical identity/revision/personal data. This is separate from
real native image qualification and actual owner-host deployment evidence.


## Guided native owner setup

After default preparation, admit an exact HTTPS origin and exact owner subject
for managed UDS ingress. Supplying them configures local trust expectations; it
does not sign in to Tailscale, provision HTTPS or verify a remote identity. Run as
the existing nonroot workspace owner, with its nonzero group and private native
credential-output directory. Stop other writers/editors during this checkpoint.

```sh
"$PYTHON" -m health_buddy.install_owner --journal "$PRIVATE_INSTALL/install.json" --owner-token "$OWNER_WORKSPACE/secrets/native-owner-token" --origin "$PRIVATE_HTTPS_ORIGIN" --owner-subject "$EXACT_OWNER_SUBJECT" --confirm-owner-setup
```

This action accepts only the prepared workspace's default security fields. It
preserves timezone, goals, equipment, storage, integrations and other personal
config, changing only ingress mode, HTTPS origin, owner subject and managed socket
path. Before writes, the private checkpoint binds original/target config digests,
workspace identity, OS owner and private token-output path. Config and token paths
must not contain links. The output must be new; unrelated existing authority or
credentials are refused. No permissions, ownership or personal data are repaired.

`configuring` → `authority_preparing` → `ready` resumes the exact selection. A lost
config acknowledgement is reconciled against exact original/target bytes. A lost
security acknowledgement authenticates the retained durable owner credential and
rechecks current owner authority and finite local readiness. It never regenerates
a token or initializes an already present authority. Repeated setup retains the
credential, security epoch, canonical identity/revision and owned config. Token
bytes stay in the private output file; they never appear in summaries, logs or the
installation journal. This is a native owner token, not an expiring browser
bootstrap proof; keep it private and use credential-file arguments rather than
pasting it into commands or chat.

An empty output or incomplete/mismatched authority refuses with
`install_owner_partial_requires_explicit_recovery`. Keep the checkpoint and
partial files for inspection. Deliberate existing OS-owner recovery uses a **new**
private credential output and revokes every previous credential:

```sh
health-buddy --workspace "$OWNER_WORKSPACE" security recover --owner-token-file "$NEW_PRIVATE_RECOVERY_TOKEN" --confirm-revoke-all
```

Recovery is a separate explicit operation, never an automatic installer retry.
The installer does not adopt a recovered/foreign authority by rewriting its
checkpoint; post-recovery lifecycle reconciliation still requires owner review.
An edited config, changed origin/subject/output or changed authority also refuses
without overwriting it. `ownerSetupReady:true` means local managed config and
native owner authentication/readiness are verified. It does not mean HTTPS,
client or phone is connected. Continue with the separate admitted activation
command above. Tailscale sign-in/private HTTPS, agent grants, named-client and
phone acceptance remain pending.

Queue-owned owner setup checks:

```sh
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m pytest -p no:cacheprovider tests/test_install_owner.py tests/test_install_activation.py::test_prepare_activation_and_lost_ack_resume_preserve_workspace
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 "$PYTHON" -m ruff check src/health_buddy/install_owner.py tests/test_install_owner.py tests/test_install_activation.py
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 "$PYTHON" -m ruff format --check src/health_buddy/install_owner.py tests/test_install_owner.py tests/test_install_activation.py
"$PYTHON" -m mypy src/health_buddy/install_owner.py
```

The synthetic flow runs default preparation → guided owner setup → activation
with the existing bounded CLI fixture. It uses real config validation, security
initialization, retained credential authentication and readiness under simulated
nonroot metadata. Separate interruption cases retain owned config/token after
lost acknowledgements, preserve unrelated personal choices, and refuse foreign
edits, unowned output and partial authority. No actual host identity change,
Docker daemon, Tailscale sign-in, paid model session or phone action is exercised.

## Scoped private HTTPS Serve checkpoint

This first Serve path targets the exact Tailscale **1.102.5** CLI/daemon interface
from immutable upstream source
[5fb2a81b065b0a0bbbfc67ab20a0d9c6a1108115](https://github.com/tailscale/tailscale/tree/5fb2a81b065b0a0bbbfc67ab20a0d9c6a1108115).
No Tailscale binary was installed or executed for this implementation; fixtures
are source-backed synthetic evidence, not named-host/client qualification or
compatibility with arbitrary patched builds. The owner must separately install,
admit and sign in to a matching supported native CLI/daemon and approve the
machine, MagicDNS and HTTPS certificate permissions. This command never logs in,
enrolls a device, updates binaries or enables certificate provisioning.

Observation requires lowercase version fields `majorMinorPatch`, `short`, `long`,
`gitCommit` and `daemonLong` from
[version metadata](https://github.com/tailscale/tailscale/blob/5fb2a81b065b0a0bbbfc67ab20a0d9c6a1108115/version/prop.go)
and [version command](https://github.com/tailscale/tailscale/blob/5fb2a81b065b0a0bbbfc67ab20a0d9c6a1108115/cmd/tailscale/cli/version.go).
It requires supported clean source/version and matching CLI/daemon long versions.
[Status](https://github.com/tailscale/tailscale/blob/5fb2a81b065b0a0bbbfc67ab20a0d9c6a1108115/ipn/ipnstate/ipnstate.go)
must report that same `Version`, `BackendState:Running`, eligible `Self.DNSName`,
`CurrentTailnet.MagicDNSSuffix`/`MagicDNSEnabled`, matching `CertDomains`, and the
existing literal `"https"` key in `Self.CapMap`. That last gate keeps the supported
[Serve feature flow](https://github.com/tailscale/tailscale/blob/5fb2a81b065b0a0bbbfc67ab20a0d9c6a1108115/cmd/tailscale/cli/serve_legacy.go)
from requesting HTTPS enablement. Origin must equal the configured private HTTPS
DNS origin; no hostname or subject is guessed. Missing admission returns fixed
owner-action codes without raw auth URLs or daemon logs.

Use explicit native executable and local daemon-socket paths. The local API must
already have an owned healthy activation, ready security authority and existing
managed UDS socket. The read-only dry-run observes these states and Serve config;
it writes no journal or workspace files and creates no route:

```sh
"$PYTHON" -m health_buddy.install_https --journal "$PRIVATE_INSTALL/install.json" --tailscale "$ADMITTED_NATIVE_TAILSCALE" --daemon-socket "$LOCAL_TAILSCALED_SOCKET" --action dry-run --confirm-local-tailscale
"$PYTHON" -m health_buddy.install_https --journal "$PRIVATE_INSTALL/install.json" --tailscale "$ADMITTED_NATIVE_TAILSCALE" --daemon-socket "$LOCAL_TAILSCALED_SOCKET" --action setup --confirm-local-tailscale --confirm-serve --confirm-quiesced
```

The mutation is exactly `serve --bg --https=443 --set-path=/ unix:/absolute/socket`.
Only this workspace's HTTPS 443 root handler is owned. Before mutation, the private
journal binds the existing installation/authority/config, CLI bytes and version,
daemon socket, origin and UDS target plus an unrelated-config digest. It refuses
unowned root handlers, TCP/HTTP conflicts, other 443 hostnames, foreground 443
entries, selected-port Funnel, ambiguous port state and changed owner binding.
Other paths on the same private host, other ports, Services and unrelated
Foreground config are preserved. Selected-port Funnel entries are refused even
when already false if the CLI would remove them. No public Funnel is enabled.

`setting` → `enabled` and `removing` → `removed` reconcile lost acknowledgements
by reobserving exact owned handler and unrelated digest. Repeating setup/removal
does not issue another mutation when its desired owned state is already present.
Closed stdin, a 20-second deadline and 64 KiB output cap prevent hidden interactive
flows and unbounded raw output. External Serve editors must be quiesced: the
[CLI's read-modify-write](https://github.com/tailscale/tailscale/blob/5fb2a81b065b0a0bbbfc67ab20a0d9c6a1108115/cmd/tailscale/cli/serve_v2.go)
is not atomic with installer preflight. Before/after fingerprint comparison
detects competing changes but cannot prevent them; a conflict retains all state
for owner inspection and never resets/restores a whole configuration.

Owned removal uses the exact original selection and explicit path flag:

```sh
"$PYTHON" -m health_buddy.install_https --journal "$PRIVATE_INSTALL/install.json" --tailscale "$ADMITTED_NATIVE_TAILSCALE" --daemon-socket "$LOCAL_TAILSCALED_SOCKET" --action remove --confirm-local-tailscale --confirm-serve --confirm-quiesced
```

The resulting command is `serve --bg --https=443 --set-path=/ off`. It removes no
other root/path/port, does not stop the API and keeps all personal data, config and
credentials. An unowned or locally changed root refuses removal. Re-enabling
a removed route requires deliberate lifecycle review; the saved ownership intent
is retained. No `reset`, `set-raw`, whole-config restore or account operation is
used. `privateRouteConfigured:true` records observed configuration only;
`connected:false` remains until actual private HTTPS and named-client/phone
acceptance. Owner grants to agents and those acceptance checks remain pending.

Queue-owned targeted checks:

```sh
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m pytest -p no:cacheprovider tests/test_install_https.py tests/test_install_owner.py::test_default_prepare_guided_owner_activation_and_repeat
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 "$PYTHON" -m ruff check src/health_buddy/install_https.py tests/test_install_https.py
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 "$PYTHON" -m ruff format --check src/health_buddy/install_https.py tests/test_install_https.py
"$PYTHON" -m mypy src/health_buddy/install_https.py
```

All Tailscale/Docker responses in this gate are bounded synthetic fixtures.
Setup/repeat/remove, lost acknowledgements, read-only dry-run, admission/conflict
refusals and preservation of unrelated Serve/personal state are tested separately
from actual installation, network access or host authentication.
