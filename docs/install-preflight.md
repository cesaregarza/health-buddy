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
OS-owner security initialization, preserving this same workspace. Guided setup
is pending the next slice; this tracer assumes that owner setup separately
exists. It creates no owner/agent grant or Tailscale sign-in.

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
image, workspace mount and runtime user before accepting success. An already
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
