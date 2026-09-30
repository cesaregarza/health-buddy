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
