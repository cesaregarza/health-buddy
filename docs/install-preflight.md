# Native owner installer lifecycle

The supported flow is bootstrap → pinned acquisition → preflight → prepare →
explicit owner setup → API activation → scoped agent configuration/status and,
for the phone and browser, private HTTPS Serve → owned removal with retained
data. Agent setup does not wait for HTTPS. Each mutation requires owner
admission; preflight alone is read-only. Apart from the bootstrap's
bundle and pinned Python packages and acquire's pinned release archives, no
step downloads software, enrolls a host, signs in to Tailscale or launches a
model client automatically.

Health Buddy is installed only when the activation stage prints
`"runtimeActivated": true` and `health_buddy.install.status` then prints
`"runtimeLastActive": true`. Development mode (`--development`) is never an
installation, an agent installing for an owner never uses it, and it refuses a
workspace an installation has prepared
(`development_mode_refused_on_installed_workspace`).

Verification uses synthetic host responses and real local workspace/authority/
client files. Actual owner-host/HTTPS/phone acceptance remains unqualified, and
named-client gates remain open in CES-1073/1074; CES-1083 owns cross-agent/final
upgrade qualification. Owners run the documented native commands under their
own authorization.

## Before the first stage

A fresh host has neither the installer nor its Python packages. These steps
download the published source bundle, check it against the owner's hash and
give it a Python environment that runs the installer from the bundle without
changing it. Run them in order, in Bash, as the owner rather than root. Each
step says what done looks like and what to do on failure; for any failure it
does not cover, stop and tell the owner instead of working around it.

### 1. Get four values from the owner

Ask the owner for:

- the URL of the release's `health-buddy-bundle.tar`;
- that archive's SHA-256;
- the URL of the same release's `runtime-manifest.json`;
- that manifest's SHA-256.

For a GitHub Release the URLs are
`https://github.com/<owner>/<repo>/releases/download/<tag>/health-buddy-bundle.tar`
and `.../<tag>/runtime-manifest.json`, naming a fixed tag, never `latest`. The
owner reads both hashes from the release's `SHA256SUMS` or notes and confirms
them through a separate channel before giving them to you. Done: two HTTPS URLs
and two 64-character lowercase hex strings. If any is missing, stop and ask;
never use a hash computed from a downloaded file as the expected value.

### 2. Download the bundle into a private directory

Replace the placeholder with the bundle URL, then run:

```sh
umask 077
mkdir -m 0700 "$HOME/health-buddy"
curl -fL -o "$HOME/health-buddy/health-buddy-bundle.tar" '<bundle URL from the owner>'
```

Done: no line starting `curl: (` is printed. On a curl error, run
`rm -f "$HOME/health-buddy/health-buddy-bundle.tar"`, confirm the URL with the
owner and run the curl line again; if curl is missing, ask the owner to install
it. `File exists` from `mkdir` on a rerun is fine when
`stat -c '%a %U' "$HOME/health-buddy"` prints `700` and your user name;
otherwise stop and ask the owner. Keep everything in this directory, never in
`/tmp`: the installation goes on using these files after a reboot, and the
stages refuse directories that are not private.

### 3. Check the archive against the owner's hash

Replace the placeholder with the bundle SHA-256, then run:

```sh
printf '%s  %s\n' '<bundle SHA-256 from the owner>' "$HOME/health-buddy/health-buddy-bundle.tar" | sha256sum -c -
```

Done: it prints `.../health-buddy-bundle.tar: OK`. On `FAILED` or any other
output, run `rm "$HOME/health-buddy/health-buddy-bundle.tar"` and tell the
owner; do not extract it, and never swap in the hash you computed. This check
is what makes the bundle trustworthy: you are about to run code from it, and
that code cannot vouch for itself.

### 4. Extract the bundle

```sh
tar -xf "$HOME/health-buddy/health-buddy-bundle.tar" -C "$HOME/health-buddy"
ls "$HOME/health-buddy/bundle" "$HOME/health-buddy/bundle/release"
```

Done: `ls` lists `release` and `source`, then `source-manifest.json` and
`source.tar`. Extract only once. If an earlier attempt may have stopped part
way, first move its result aside with
`mv "$HOME/health-buddy/bundle" "$HOME/health-buddy/bundle.old"`, then run this
step again. Never move a bundle that a stage has already used: the stages
record its path.

### 5. Create a Python 3.12 environment outside the bundle

```sh
python3.12 -m venv "$HOME/health-buddy/venv"
"$HOME/health-buddy/venv/bin/python" --version
```

Done: it prints `Python 3.12.` and a patch number. If `python3.12` is not found,
stop and ask the owner to install CPython 3.12; the installer does not install
Python. If venv reports that `ensurepip is not available`, ask the owner to
install the OS package it names (on Ubuntu 24.04,
`sudo apt-get install python3.12-venv`), then run this step again. Never
install Python packages into the system Python.

### 6. Install the pinned dependencies into that environment

The installer imports third-party packages that a fresh host lacks. Check the
architecture:

```sh
uname -m
```

On `x86_64`, install the repository's hash-locked wheels, the 55-package
closure the [canonical guide](agent-guide.md) also uses. Nothing is resolved,
and every wheel must match its recorded SHA-256:

```sh
"$HOME/health-buddy/venv/bin/python" -m pip --isolated --disable-pip-version-check --no-cache-dir install --only-binary=:all: --require-hashes --no-deps --no-compile -r "$HOME/health-buddy/bundle/source/packaging/dev-cp312-linux-x86_64.lock"
"$HOME/health-buddy/venv/bin/python" -m pip --isolated --disable-pip-version-check check
```

Done: the install ends with `Successfully installed` and the check prints
`No broken requirements found.` A hash mismatch or `No matching distribution`
means a changed download, a host that is not x86_64 with glibc 2.34 or newer,
or no route to PyPI: stop and tell the owner. Never drop `--require-hashes`.

On `aarch64` the repository has no hash-locked closure yet. Tell the owner, and
only with their approval run this fallback. It installs the bundle's exact
top-level pins from `pyproject.toml` and the MCP extra, but lets pip choose the
remaining versions without hash checks:

```sh
"$HOME/health-buddy/venv/bin/python" -m pip --isolated --disable-pip-version-check --no-cache-dir install --no-compile "$HOME/health-buddy/bundle/source[mcp]"
"$HOME/health-buddy/venv/bin/python" -m pip --isolated --disable-pip-version-check check
```

Done and failure are as for `x86_64`. This is a plain, non-editable install;
never use `pip install -e`.

### 7. Save the variables and point Python at the bundle

Agent shells often forget variables between commands, so the rest of the
installation reads them from one file. Replace the two placeholders with the
manifest URL and SHA-256, then create it once. On a resumed installation, keep
the existing `env.sh` and run only its source/verification commands; recreating
it would discard reviewed selections saved later:

```sh
umask 077
cat > "$HOME/health-buddy/env.sh" <<'EOF'
umask 077
export HB_HOME="$(realpath "$HOME")/health-buddy"
export BUNDLE="$HB_HOME/bundle"
export SOURCE="$BUNDLE/source"
export PYTHON="$HB_HOME/venv/bin/python"
export ARTIFACTS="$HB_HOME/artifacts"
export PRIVATE_INSTALL="$HB_HOME/install"
export OWNER_WORKSPACE="$HB_HOME/workspace"
export PRIVATE_CLIENT="$HB_HOME/client"
export PRIVATE_HTTPS_ORIGIN='https://health-buddy.local'
export EXACT_OWNER_SUBJECT='owner'
export OWNER_UID="$(id -u)"
export OWNER_GID="$(id -g)"
export NEW_PRIVATE_RECOVERY_TOKEN="$PRIVATE_INSTALL/recovered-owner-token"
export PUBLISHER_MANIFEST_URL='<manifest URL from the owner>'
export TRUSTED_MANIFEST_SHA256='<manifest SHA-256 from the owner>'
export PYTHONPATH="$SOURCE/src"
export PYTHONDONTWRITEBYTECODE=1
EOF
. "$HOME/health-buddy/env.sh"
"$PYTHON" "$SOURCE/scripts/package_runtime.py" verify-source --source "$SOURCE" --manifest "$BUNDLE/release/source-manifest.json"
"$PYTHON" -m health_buddy.install.acquire --help
```

Step 2's `umask 077` makes the downloaded archive private; repeating it before
writing `env.sh` protects that file even in a fresh shell. The saved setting
keeps every file the installer and Git create private to you:
Ubuntu's default `002` would leave Git refs group-writable, which the runtime's
readiness check refuses.

`PYTHONPATH` makes Python import the installer from the bundle, and
`PYTHONDONTWRITEBYTECODE=1` stops it writing `__pycache__` files into it, which
keeps the extracted bundle byte-identical. The stages also turn bytecode
writing off themselves, and the source identity check ignores interpreter
bytecode (`__pycache__`, `*.pyc`, `*.pyo`) either way. Done: the first check
prints `runtime packaging operation completed; publication and qualification
remain separate` and the second a usage line starting `usage: acquire.py`. A
`ModuleNotFoundError` means step 6 did not finish: run it again. If the first
check prints `runtime_packaging_failed`, the bundle has changed since
extraction: move it aside as in step 4, then repeat extraction and the two
checks above using the retained `env.sh`.

Start every later installation command block with
`. "$HOME/health-buddy/env.sh"`. An `export` in a previous agent shell is not
retained; save choices in this file before starting a stage. `HOME` is the
owner shell's existing native home directory. `OWNER_UID` and `OWNER_GID` come
from `id -u` and `id -g`; both must be nonzero for activation. Do not run these
commands as root or substitute a different user's IDs.

Keep the bundle byte-exact. Never run `pip install -e`; never install into,
edit, format, test or build inside `$BUNDLE`; never run Python against it
without `env.sh` loaded. Acquire, preflight and prepare verify the bundle
before they act and refuse one that differs from its manifest; the cure is to
re-extract it from the checked archive (steps 4 and 7).

### 8. Choose and persist the host and client values

Review this table before preflight or owner setup. The bootstrap above selects
the working local-only origin/subject example; it does not discover or guess
the owner's Tailscale identity.

| Variable | Exact rule and example | Discovery or owner choice |
| --- | --- | --- |
| `INSPECTED_NATIVE_DOCKER` | Absolute native path to a regular executable with no symlinks in the path, for example `/usr/bin/docker`. `/var/run/docker.sock` is a socket, not the CLI. | Run `command -v docker`, inspect the result and `stat` its ownership/mode. If it is a link, inspect the native target from `readlink -f` before selecting that target. No Docker daemon is contacted by preflight. |
| `PRIVATE_HTTPS_ORIGIN` | Canonical HTTPS origin, at most 500 ASCII characters: lowercase DNS host, no trailing dot, whitespace, credentials, path (even `/`), query, fragment, backslash, percent escapes or `:443`. Canonical IPv4/bracketed IPv6 and nondefault ports are supported by config; this Serve flow uses port 443, omitted from the origin. Local-only example: `https://health-buddy.local`. Synthetic HTTPS example: `https://health-buddy.example.test`; replace it with the real Serve DNS origin. | For local-only use, keep the saved example. For phone/browser access, inspect `tailscale status --json` on the admitted host: use `https://` plus `Self.DNSName` without its trailing dot; confirm the machine's HTTPS name in the Tailscale admin console. |
| `EXACT_OWNER_SUBJECT` | Exact, case-sensitive owner subject: 1–254 printable ASCII characters, no spaces/control characters and no `=?` sequence. Local-only example: `owner`. Tailscale login example: `user@github`; it is not an agent/client name. | For local-only use, keep `owner`. For Serve, inspect `tailscale whois <owner-device-tailnet-IP>` and use the owner's exact login identity, or read it from the Tailscale admin console. Do not use the device name or display name. |
| `PRIVATE_CLIENT` | Native owner-controlled 0700 directory **outside** the workspace and bundle: `$HB_HOME/client`. | The saved path is beside the workspace. Step 9 creates only this directory and its `skills` parent. The owner later writes `policy.json`; the agent stage writes its own outputs. |

Discover the Docker executable:

```sh
. "$HOME/health-buddy/env.sh"
command -v docker
```

Done: an absolute executable path, commonly `/usr/bin/docker`. If none is
printed, stop and have the owner install/admit native Docker and Compose.
After inspecting that executable, replace `/usr/bin/docker` below if necessary
and persist it with this exact append-and-reload step:

```sh
. "$HOME/health-buddy/env.sh"
echo 'export INSPECTED_NATIVE_DOCKER=/usr/bin/docker' >> "$HOME/health-buddy/env.sh"
. "$HOME/health-buddy/env.sh"
```

For a host without Tailscale, keep the saved `https://health-buddy.local` / `owner`
pair and proceed through owner setup, activation and agent setup. These are
stored origin/identity expectations, **not a configured HTTPS route**. The
same-host MCP adapter uses `security/runtime/http.sock` and its scoped bearer
token; this connection requires neither Tailscale nor a network HTTPS request.
Phone/browser access waits for a real private HTTPS route.

If the owner already knows the real future origin and subject, replace those
two exports in `env.sh` with the reviewed values **before owner setup**, reload
the file, and then proceed; the Tailscale daemon may still be deferred. Owner
setup binds both values and retries require the original selection. Changing
a completed local-only installation to its real origin and login subject later
uses the explicit [local-to-HTTPS transition](install-rebind.md). It preserves
records, owner credentials and retained agent grants. Reinstall/rearm does not
change this binding; never edit the bound config or journal by hand.

### 9. Create only the staging, journal, workspace and client parents

```sh
. "$HOME/health-buddy/env.sh"
mkdir -m 0700 "$ARTIFACTS" "$PRIVATE_INSTALL" "$OWNER_WORKSPACE"
mkdir -m 0700 "$PRIVATE_CLIENT" "$PRIVATE_CLIENT/skills"
stat -c '%a %U %n' "$ARTIFACTS" "$PRIVATE_INSTALL" "$OWNER_WORKSPACE" "$PRIVATE_CLIENT" "$PRIVATE_CLIENT/skills"
printf '%s\n' "$OWNER_WORKSPACE" | grep -Ex '/[A-Za-z0-9_./-]+'
```

Done: five lines starting with `700` and your user name, then the workspace
path once more. Use these explicit modes even if a fresh shell has forgotten
the private umask. Plain `mkdir` or `mkdir -p` under the default `002` leaves
a mode the stages refuse, reported by acquire as
`install_acquire_staging_unavailable` for the staging directory, by preflight
as `permissions_partial` for the workspace and by prepare as
`install_preparation_refused` for the journal directory. `File exists` on a
rerun is fine when all five lines still show `700` and your user; otherwise
stop and ask the owner, and never change the mode, owner or contents of an
existing directory. If the last command prints nothing, the path holds
characters activation refuses (anything but letters, digits and `_./-`): stop
and ask the owner where to install.

Leave staging, journal and workspace empty. Acquire downloads the manifest and
image archives into `$ARTIFACTS` itself and refuses files it did not put there,
and preflight treats a non-empty workspace as an existing installation.

Under `$PRIVATE_CLIENT`, only the empty `skills` parent may exist at this point
(the Claude Code variant of the agent stage adds its launcher directory then).
Do not precreate `agent-token`, `adapter.json`, `retries`, `config.toml` or
`skills/health-buddy`. The policy block in the agent section creates the one
owner-authored input, `policy.json`, after review. The agent stage creates the
token, settings, client configuration and managed skill files; the adapter
creates `retries` when it needs durable retry state. It must also be absent on
the first handoff. For this fresh-client example, nothing else goes under
`PRIVATE_CLIENT` before that stage.

| Variable | Path | Used as |
| --- | --- | --- |
| `BUNDLE` | `$HB_HOME/bundle` | `--bundle` in every stage |
| `SOURCE` | `$BUNDLE/source` | the installer on `PYTHONPATH`, and the Compose file |
| `PYTHON` | `$HB_HOME/venv/bin/python` | every stage, and the agent client's `--python` |
| `ARTIFACTS` | `$HB_HOME/artifacts` | acquire's `--staging`; holds `runtime-manifest.json` afterwards |
| `PRIVATE_INSTALL` | `$HB_HOME/install` | the `install.json` journal and `runtime.env` |
| `OWNER_WORKSPACE` | `$HB_HOME/workspace` | the owner's data, `--workspace` |
| `PRIVATE_CLIENT` | `$HB_HOME/client` | owner-authored policy and stage-created client outputs |

`HB_HOME` is `$HOME/health-buddy` with symbolic links resolved. Next, run the
acquire stage.

## Acquire pinned release artifacts

Run these commands directly in the supported native Linux host's owner shell,
or after the owner opens their own SSH session to that host. An SSH shell uses
that host's native paths, Python environment, resources and local daemon; the
installer never handles SSH credentials, connects remotely or forwards commands.
Use the bundle, staging directory and manifest pin from
[Before the first stage](#before-the-first-stage). A hash copied from the same
untrusted response is not publisher trust. TLS and archive integrity do not
imply a publisher signature or qualified deployment.

```sh
. "$HOME/health-buddy/env.sh"
"$PYTHON" -m health_buddy.install.acquire --manifest-url "$PUBLISHER_MANIFEST_URL" --trusted-manifest-sha256 "$TRUSTED_MANIFEST_SHA256" --bundle "$BUNDLE" --staging "$ARTIFACTS"
```

Done: it prints `"artifactsVerified": true`. Do not download the manifest or the
image archives yourself; this command fetches them. These refusals come before
any network activity and point back to the bootstrap:
`install_acquire_staging_unavailable` means `$ARTIFACTS` is missing or is not
a mode-0700 directory you own (step 9);
`install_acquire_source_identity_mismatch` means the bundle no longer matches
its manifest, so re-extract it from the checked archive (steps 4 and 7);
`install_acquire_unowned_staging_entries` means something other than acquire
put files into `$ARTIFACTS`, so move them out;
`install_acquire_https_url_required` and
`install_acquire_requires_trusted_manifest_pin` mean the manifest values in
`env.sh` are not the owner's URL and hash.

The supported source is a GitHub Release. Pass the manifest asset's URL,
`https://github.com/<owner>/<repo>/releases/download/<tag>/runtime-manifest.json`,
naming a fixed tag rather than `latest`. The same release carries the three
fixed neighboring assets fetched beside it: `health-buddy-source.tar` and
`health-buddy-linux-{amd64,arm64}.docker.tar`. It also carries
`health-buddy-bundle.tar`, which the bootstrap downloads, and `SHA256SUMS`,
which lists all five; acquire fetches neither. Read the manifest SHA-256 from
the release page or its notes, then confirm it through a separate channel
before passing it as the pin. The URL must be HTTPS on port 443 with no
userinfo, query or fragment; no cookies, ambient proxies or authorization
headers are sent. GitHub answers each download with a 302 to a short-lived
signed URL on `release-assets.githubusercontent.com`. The installer admits that
hop, and only that hop, across origins: from exactly `github.com` to exactly
that host, over HTTPS on port 443, keeping its signed query string. Every other
redirect must stay on the same HTTPS origin without query/fragment/userinfo, so
a maintainer-run same-origin publisher still works. The maintained redirect
count and closed original bodies apply to every hop. No other download host is
silently adopted.

The journal binds origin URL/pin/source identity before network activity. Each
file streams at most 64 KiB per read; metadata is capped at 2 MiB, source archive
at 64 MiB and each Docker archive at 1 GiB. A fixed isolated source worker gives
each file a 120-second whole-operation deadline, including headers/redirects;
the parent kills/reaps only its own worker. A cooperative body/socket budget is
additional protection, not a claimed header-parser deadline. No fetched code is
executed and no Docker/Tailscale/SSH tooling is downloaded or installed.

Existing exact private files are reverified and reused without transport;
changed files or binding refuse without overwrite. Completed verified files
are retained after failures. Owned partial inode/path intent is retained before
download, cleaned immediately on handled failure/interruption and reconciled
on restart; unknown/replaced partials refuse deletion and require owner inspection.
A crash before its ownership intent is durable can leave an unowned file which
also refuses automatic deletion. Fixed `install_acquire_*` errors omit URL,
headers, paths, responses and credentials. Retry the original command after the
owner resolves network/admission errors. Acquisition still requires the
bootstrap's bundle and checks its source identity against the pinned manifest;
it does not reconstruct that bundle or infer a signing key. Both image archives
pass the maintained archive/manifest validator before `artifactsVerified:true`.
`installed:false` remains explicit. Then use the same bundle/staging/pin below.

## Read-only preflight

Use the manifest and archives that acquire verified in `$ARTIFACTS`, the same
bundle and the same independently confirmed manifest SHA-256; copying the hash
from untrusted downloaded content does not establish trust. This preflight
checks an operator-supplied pin and existing archive/source verifiers, not a
publisher signature or external release qualification. Do not execute arbitrary
release scripts merely because this dry-run accepts archive integrity.

The workspace is the empty mode-0700 `$OWNER_WORKSPACE` from the bootstrap,
outside the source bundle; any other mode is refused as `permissions_partial`.
An empty workspace is reported as `empty_not_initialized`. Any existing entry
produces `existing_state_requires_review`, without reading config, personal notes,
secrets or health records. Diagnose existing installations with
[operator status/doctor](operator-diagnostics.md), then use the verified upgrade
path; never empty a workspace to make this gate pass. The installation retains persistent personal source, config, assets, extension notes/tests/state,
and matching source/documented development commands for agent maintenance.
Nonzero service UID/GID and ownership admission are still required before runtime
loading, as described in [runtime packaging](runtime-packaging.md).

```sh
. "$HOME/health-buddy/env.sh"
"$PYTHON" -m health_buddy.install.preflight --bundle "$BUNDLE" --manifest "$ARTIFACTS/runtime-manifest.json" --trusted-manifest-sha256 "$TRUSTED_MANIFEST_SHA256" --workspace "$OWNER_WORKSPACE" --docker "$INSPECTED_NATIVE_DOCKER"
# Optional separate host listener check, not a published runtime port:
"$PYTHON" -m health_buddy.install.preflight --bundle "$BUNDLE" --manifest "$ARTIFACTS/runtime-manifest.json" --trusted-manifest-sha256 "$TRUSTED_MANIFEST_SHA256" --workspace "$OWNER_WORKSPACE" --docker "$INSPECTED_NATIVE_DOCKER" --port 8791
```

The supported architecture mapping is x86_64→amd64 and aarch64/arm64→arm64, native
Linux only; 32-bit Raspberry Pi OS and emulation are refused. Initial planning
bounds are two logical CPUs, 2 GiB physical RAM and 6 GiB free workspace space.
These are conservative preflight policy bounds, not measured capacity for
personal history. Free space on the Docker data root, staging filesystem and
future data growth still require separate admission. Physical host facts do not
prove cgroup/container quotas. A present native Docker executable and local
socket are metadata checks only: daemon reachability/version, Compose discovery,
rootless/remapped ownership and CLI/plugin trust remain unqualified. Nothing is
started or reconfigured automatically.

JSON schema 1 returns fixed actionable diagnostic codes, architecture/resource
facts, archive/source verification state and future requirements. It emits no
selected private paths, config values, identities, records or credential bytes;
the one file it can name is relative to the bundle's source tree. A source tree
that differs from its manifest reports `release_invalid` together with
`source_inventory_mismatch`, whose recovery names the first differing file;
re-extract the bundle from its checked archive (bootstrap steps 4 and 7).
Exit 0 means the measured preflight checks passed; exit 2 means a refusal.
`installed` and `readyForActivation` remain false even on success. An optional
loopback port check reuses doctor's temporary bind probe and releases it; this
does not reserve a port or terminate an existing listener. The current runtime
uses network-none containers and a private Unix socket, with no host port
published by Compose. Phone/private HTTPS reachability remains unknown.

## Durable local preparation

After preflight, `health_buddy.install.prepare` can prepare the real empty local
workspace and matching source references without loading/starting a runtime.
The journal must be an explicit mode-0600 file in an existing private native
mode-0700 directory **outside** the workspace and release bundle: prepare
creates `install.json` in the bootstrap's `$PRIVATE_INSTALL`. Keep that
journal and the originally pinned bundle/artifacts across restarts.

```sh
. "$HOME/health-buddy/env.sh"
"$PYTHON" -m health_buddy.install.prepare --journal "$PRIVATE_INSTALL/install.json" --bundle "$BUNDLE" --manifest "$ARTIFACTS/runtime-manifest.json" --trusted-manifest-sha256 "$TRUSTED_MANIFEST_SHA256" --workspace "$OWNER_WORKSPACE" --docker "$INSPECTED_NATIVE_DOCKER"
```

The guided install passes no `--client` arguments here: the agent stage below
configures the client. `--client` is only for the optional existing-credential
variant further down, and then needs all four of its companion arguments.

A source tree that no longer matches its manifest refuses as
`install_preparation_source_identity_mismatch` before the first write.
Both preflight and prepare report `docker_cli_unavailable` for a missing,
empty or invalid `--docker`: reload `env.sh` and select the inspected native
executable, for example `/usr/bin/docker`, not `/var/run/docker.sock`.
Prepare checks that argument before writing any intent. Other preparation
refusals are `install_preparation_refused`.

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
reviewed private ingress/security workflow. The owner setup and runtime activation steps below supply the next explicit
admissions; preparation starts no service.

Optional existing-credential workflow: skip this variant for the default guided
install, which uses the agent stage below to create its grant and settings.
After the owner independently establishes matching origin/authority and authors
an explicit reviewed `$PRIVATE_CLIENT/adapter.json` plus credential, prepare can
connect that existing profile. It does not create those inputs:

```sh
. "$HOME/health-buddy/env.sh"
"$PYTHON" -m health_buddy.install.prepare --journal "$PRIVATE_INSTALL/install.json" --bundle "$BUNDLE" --manifest "$ARTIFACTS/runtime-manifest.json" --trusted-manifest-sha256 "$TRUSTED_MANIFEST_SHA256" --workspace "$OWNER_WORKSPACE" --docker "$INSPECTED_NATIVE_DOCKER" --client codex --client-config "$PRIVATE_CLIENT/config.toml" --skill-directory "$PRIVATE_CLIENT/skills/health-buddy" --settings "$PRIVATE_CLIENT/adapter.json" --python "$PYTHON"
```

Use `--client claude` with the `.mcp.json` private launcher and supported skill
location from [Claude setup](claude-integration.md), or [Codex setup](codex-integration.md).
Preparation verifies the settings receiver tuple, origin and API socket match this
retained workspace before reusing the accepted connect-agent helper; it does not create
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

## Guided native owner setup

After default preparation, admit an exact HTTPS origin and exact owner subject
from step 8 for managed UDS ingress. On a no-Tailscale host the saved local-only
pair is sufficient for activation and same-host agent setup. Supplying it
configures local trust expectations; it does not sign in to Tailscale, provision
HTTPS or verify a remote identity. Run as the existing nonroot workspace owner,
with its nonzero group and private native credential-output directory. Stop
other writers/editors during this action.

The prepared default has HealthKit disabled with mode `read-only`. A reservation
or private pairing proof does not prove redemption or ingest can work. Before
`health_buddy.install.owner`, explicitly edit only the intended HealthKit configuration to
`integrations.healthkit: {enabled:true,mode:"receiver"}` using the supported
[owner configuration](configuration.md#owner-configuration), validate it through
`health_buddy.core.config.load` via the native workspace command, and review it:
`"$PYTHON" -m health_buddy.cli --workspace "$OWNER_WORKSPACE" workspace describe --json`.
Keep that owner path inventory private. Owner
setup preserves nonsecurity configuration and binds its exact resulting bytes.
Never silently enable the receiver or edit a bound running installer config to
bypass that binding; an already bound change requires explicit owner lifecycle
review. Status exposes only receiver enabled/mode/configured booleans/enum.
Phone setup remains the existing owner login and `/security` page headed
“Connect a phone”; real HealthKit permission/device/build acceptance stays open.


```sh
. "$HOME/health-buddy/env.sh"
"$PYTHON" -m health_buddy.install.owner --journal "$PRIVATE_INSTALL/install.json" --owner-token "$OWNER_WORKSPACE/secrets/native-owner-token" --origin "$PRIVATE_HTTPS_ORIGIN" --owner-subject "$EXACT_OWNER_SUBJECT" --confirm-owner-setup
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

An authority that authenticates while the workspace fails the local readiness
check refuses with `install_owner_workspace_not_ready`. The authority is intact:
make the workspace private (no group or other write access) and quiescent, then
run the same owner command again. `security recover` is not the remedy.

An empty output or incomplete/mismatched authority refuses with
`install_owner_partial_requires_explicit_recovery`. Keep the checkpoint and
partial files for inspection. Deliberate existing OS-owner recovery uses a **new**
private credential output and revokes every previous credential:

`NEW_PRIVATE_RECOVERY_TOKEN` is already saved in `env.sh` as
`$PRIVATE_INSTALL/recovered-owner-token`. It must be absent; review a different
new native path and persist it before recovery if that path already exists.

```sh
. "$HOME/health-buddy/env.sh"
"$PYTHON" -m health_buddy.cli --workspace "$OWNER_WORKSPACE" security recover --owner-token-file "$NEW_PRIVATE_RECOVERY_TOKEN" --confirm-revoke-all
```

Recovery is a separate explicit operation, never an automatic installer retry.
The installer does not adopt a recovered/foreign authority by rewriting its
checkpoint; post-recovery lifecycle reconciliation still requires owner review.
An edited config, changed origin/subject/output or changed authority also refuses
without overwriting it. `ownerSetupReady:true` means local managed config and
native owner authentication/readiness are verified. It does not mean HTTPS,
client or phone is connected. Continue with the separate admitted activation
command below, followed by agent configuration and private HTTPS. Tailscale
sign-in is an independent owner prerequisite of HTTPS; live client/phone acceptance stays open.

## Explicit runtime activation

`health_buddy.install.activation` is the next owner-controlled action. It contacts
only the explicitly admitted native CLI and local Unix Docker socket. Preparation
and preflight commands still perform no activation. No tool or check receipt here
establishes an actual deployment: this source flow is verified with synthetic
subprocess responses only.

Preparation alone does not establish runtime readiness. A default prepared
loopback workspace refuses activation with
`install_activation_requires_managed_owner_setup`; missing authority refuses
with `install_activation_requires_ready_owner_authority`, before any daemon
contact. Recovery is the existing reviewed managed UDS configuration and explicit
OS-owner security initialization, preserving this same workspace. The guided
owner command above supplies that local setup before activation. It creates no
agent grant or Tailscale sign-in.

Run preparation and activation as the private workspace's nonroot OS owner, with
that existing workspace's nonzero UID/GID. The workspace must already be mode
0700 with matching ownership. The installer never changes ownership, repairs
permissions or adopts an existing installation. Independently inspect/admit the
local Docker CLI, Compose plugin, daemon, quotas and owner-controlled Docker
socket access first; do not expose an engine remotely. Keep writers, editors and
other operations on the selected project stopped during this action.

```sh
. "$HOME/health-buddy/env.sh"
"$PYTHON" -m health_buddy.install.activation --journal "$PRIVATE_INSTALL/install.json" --environment "$PRIVATE_INSTALL/runtime.env" --project health-buddy-personal --uid "$OWNER_UID" --gid "$OWNER_GID" --confirm-local-daemon --confirm-quiesced
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
`connected:false` remains explicit: next configure the scoped agent grant and,
for the phone and browser, private HTTPS. Phone pairing and named-client qualification require live acceptance. The source
profile's `runtimeActivated:false` remains its preparation-time description;
current activation evidence lives in the private journal's `activation` record.

## Scoped private HTTPS Serve

Skip this section for the local-only origin/subject selection and continue with
agent setup below. If owner setup already bound the real Tailscale values,
private HTTPS may be configured now or later without replacing the agent grant.
The local-only example does not match a Tailscale Serve DNS name; see step 8's
lifecycle review requirement before changing a bound selection.

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
managed UDS socket. Discover the CLI with `command -v tailscale`; inspect its
native executable target and the daemon's `--socket` setting (for a systemd
installation, `systemctl cat tailscaled`). The usual native paths are
`/usr/bin/tailscale` and `/run/tailscale/tailscaled.sock`. Replace them below if
the admitted installation differs, then save them once before the dry-run:

```sh
. "$HOME/health-buddy/env.sh"
cat >> "$HOME/health-buddy/env.sh" <<'EOF'
export ADMITTED_NATIVE_TAILSCALE=/usr/bin/tailscale
export LOCAL_TAILSCALED_SOCKET=/run/tailscale/tailscaled.sock
EOF
. "$HOME/health-buddy/env.sh"
```

The read-only dry-run observes these states and Serve config;
it writes no journal or workspace files and creates no route:

```sh
. "$HOME/health-buddy/env.sh"
"$PYTHON" -m health_buddy.install.https --journal "$PRIVATE_INSTALL/install.json" --tailscale "$ADMITTED_NATIVE_TAILSCALE" --daemon-socket "$LOCAL_TAILSCALED_SOCKET" --action dry-run --confirm-local-tailscale
"$PYTHON" -m health_buddy.install.https --journal "$PRIVATE_INSTALL/install.json" --tailscale "$ADMITTED_NATIVE_TAILSCALE" --daemon-socket "$LOCAL_TAILSCALED_SOCKET" --action setup --confirm-local-tailscale --confirm-serve --confirm-quiesced
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
. "$HOME/health-buddy/env.sh"
"$PYTHON" -m health_buddy.install.https --journal "$PRIVATE_INSTALL/install.json" --tailscale "$ADMITTED_NATIVE_TAILSCALE" --daemon-socket "$LOCAL_TAILSCALED_SOCKET" --action remove --confirm-local-tailscale --confirm-serve --confirm-quiesced
```

The resulting command is `serve --bg --https=443 --set-path=/ off`. It removes no
other root/path/port, does not stop the API and keeps all personal data, config and
credentials. An unowned or locally changed root refuses removal. Re-enabling
a removed route requires deliberate lifecycle review; the saved ownership intent
is retained. No `reset`, `set-raw`, whole-config restore or account operation is
used. `privateRouteConfigured:true` records observed configuration only;
`connected:false` remains until actual private HTTPS and named-client/phone
acceptance. The agent-grant slice below prepares configuration; live acceptance remains pending.

## Explicit agent grant and redacted owner status

Agent setup needs the active runtime, not private HTTPS: the configured client
runs on this host and its MCP adapter reaches the API over the workspace's
managed socket. Private HTTPS remains for the phone and browser, before or after
this step. Until activation is `active`, setup refuses with
`install_agent_requires_active_runtime`. After owned runtime activation, review a
private 0600 policy file with exactly `name`, `grants`, `sourceIds`, `readSources`,
`readKinds`, and `readFields`. Review the manual-only example below, changing its
name/scopes to the owner's selection before running it once. The subshell's
`noclobber` refuses an existing policy instead of overwriting it; on a retry,
keep and review the original policy rather than recreating it:

```sh
. "$HOME/health-buddy/env.sh"
(
  set -o noclobber
  cat > "$PRIVATE_CLIENT/policy.json" <<'EOF'
{
  "name": "Health Buddy manual agent",
  "grants": ["records:read", "records:write"],
  "sourceIds": ["manual"],
  "readSources": ["manual"],
  "readKinds": ["body-mass", "water-intake", "workout-session", "workout-set", "cardio-segment"],
  "readFields": null
}
EOF
)
stat -c '%a %U %n' "$PRIVATE_CLIENT/policy.json"
```

Done: `600` and your user name. This policy is the owner-authored input.

`readKinds` uses canonical record names: `body-mass` for weight,
`water-intake` for hydration, and `workout-session`, `workout-set` and
`cardio-segment` for workout records. These are the names reported by
`capabilities.recordKinds`; display labels such as `weight` or `workout` do not
match them. This example does not authorize reading other manual record kinds.

`null` read fields deliberately authorizes all fields within those selected
sources/kinds. Select only the scopes you want to disclose to the AI client;
`providers:invoke` is a separate optional grant. This uses the existing canonical
`grants.create/list` validator and current native owner token authentication.
No new authority or source registration is created by the installer.

Use the saved external `PRIVATE_CLIENT` and its 0700 `skills` parent from step 9.
Keep token/settings/retry paths and the fresh example's client config and
`skills/health-buddy` directory absent. Do not `mkdir retries` or create empty
token/settings files; the installer/adapter owns those outputs. Existing unrelated
client settings, when deliberately selecting an existing native config instead,
are preserved by `connect_agent`; owned local edits refuse further changes.
Stop competing config/grant editors
while running this explicit setup. No client process is launched:

```sh
. "$HOME/health-buddy/env.sh"
"$PYTHON" -m health_buddy.install.agent --journal "$PRIVATE_INSTALL/install.json" --policy "$PRIVATE_CLIENT/policy.json" --agent-token "$PRIVATE_CLIENT/agent-token" --settings "$PRIVATE_CLIENT/adapter.json" --retry-root "$PRIVATE_CLIENT/retries" --client codex --client-config "$PRIVATE_CLIENT/config.toml" --skill-directory "$PRIVATE_CLIENT/skills/health-buddy" --python "$PYTHON" --confirm-grant --acknowledge-ai-egress
"$PYTHON" -m health_buddy.install.status --journal "$PRIVATE_INSTALL/install.json"
```

That block is the Codex variant. If the client on this host is Claude Code, run
this variant instead. Its config must be a file named `.mcp.json` inside a
private launcher directory that Claude Code is started from. The target check
rejects a differently named file before setup writes; the first accepted run
binds the client and config path in the journal, so choose them before then:

```sh
. "$HOME/health-buddy/env.sh"
mkdir -m 0700 "$PRIVATE_CLIENT/claude"
"$PYTHON" -m health_buddy.install.agent --journal "$PRIVATE_INSTALL/install.json" --policy "$PRIVATE_CLIENT/policy.json" --agent-token "$PRIVATE_CLIENT/agent-token" --settings "$PRIVATE_CLIENT/adapter.json" --retry-root "$PRIVATE_CLIENT/retries" --client claude --client-config "$PRIVATE_CLIENT/claude/.mcp.json" --skill-directory "$PRIVATE_CLIENT/skills/health-buddy" --python "$PYTHON" --confirm-grant --acknowledge-ai-egress
"$PYTHON" -m health_buddy.install.status --journal "$PRIVATE_INSTALL/install.json"
```

Start Claude Code from `$PRIVATE_CLIENT/claude` so it reads that `.mcp.json`.

Done: `agentGrantRetained: true` and `clientConfigurationPrepared: true`.
Before journal progress, grant creation or managed-file writes, setup checks the
exact selected `PYTHON` against the retained source's MCP imports. A ten-second
import probe reads no settings or credentials, connects to no API or model, and
writes no bytecode. It also runs for direct `connect_agent` setup; removal does
not need a working SDK. This check does not establish a live client connection.

`agent_python_not_ready` reports `python`, the failing `dependency` import and
`reason` (`missing_or_incompatible`, `probe_timeout` or `probe_failed`). Use that
same environment for [bootstrap step 6](#6-install-the-pinned-dependencies-into-that-environment),
including the x86_64 `--require-hashes` install and `pip check`, then rerun the
same setup command. A core-only install omits the MCP extra and is insufficient;
do not substitute `pip install -e .` or remove hash checks. On other architectures
follow step 6's separate owner approval and dependency guidance. Keep all retained
journal, credential and client files while repairing the environment. Setup
never installs dependencies for you. After setup succeeds, restart the named
client and verify tool discovery and an authenticated read in a fresh session.

`claude_project_config_required` means Claude's `--client-config` must be the
`.mcp.json` file in the directory from which Claude Code is launched.
`invalid_codex_skill_directory` means `--skill-directory` must have the basename
`health-buddy`. Both are checked before owner authentication or any grant,
journal, token or settings write. Correct the command and retry the original
first setup.

`install_agent_unowned_handoff_or_grant` with `conflictingPath` names the actual
pre-existing token/settings/retry path in this private owner CLI response; its
contents are never printed. Retain the path and journal, inspect which files
belong to an earlier installation, then select new absent outputs for a first
handoff or move unrelated files only after owner review. Do not delete recovery
material to force progress. A same-name grant conflict keeps that code without
a path and needs owner grant reconciliation. Keep path-bearing output private.

`install_agent_resume_requires_original_binding` reports only the names in
`differingFields`; it never includes selected values or client config contents.
For an already bound `configuring` handoff, keep the journal and use the explicit
owner [removal and re-arm procedure](install-reinstall.md). Do not auto-rebind,
rewrite ingress or delete the workspace. `install_agent_owner_config_changed`
requires owner inspection of the changed config and a deliberate owner lifecycle
review before retrying. That review must account for the retained exact config
binding; do not edit ingress, delete the journal, or rebind to satisfy it.

For Claude, choose `--client claude` and its supported project `.mcp.json`;
see [Codex integration](codex-integration.md) and
[Claude integration](claude-integration.md) for exact supported client versions,
permissions, reload/update/removal and shared maintenance paths. A fresh named
client must still be verified; generated configuration alone proves no connection.

Before grant creation, the private installation journal binds policy, workspace
identity, security epoch, original actor inventory and selected handoff paths.
The journal contains no credential values. A retained token authenticates the
same actor after an interrupted write or repeated setup; scope changes, foreign
actors and locally edited settings/config refuse adoption. If a one-time secret
is lost before it reaches its create-only private file, retry discovers the
original grant and returns `install_agent_private_handoff_requires_owner_rotation`
without creating a duplicate. Retain the journal. The owner must explicitly
reconcile/rotate or revoke that actor using existing security operations; this
first slice does not automatically adopt a replacement credential or epoch.

Status authenticates existing owner/agent authority and lists canonical devices;
it does not create pairings or fetch health records. Authentication can update
existing security budgets, so this is not a byte-for-byte read-only workspace
operation. Optional `--pairing-id <reviewed-pairing-ID>` reports only the finite
pairing status, omitting identifiers, approval paths, names and secret values.
Log in as owner and open `/security` (heading “Connect a phone”) for deliberate owner approval
and private short-lived proof delivery to the phone. No installer log includes
that proof. Recorded runtime/Serve/client stages are labeled as last configured,
not live network, daemon or named-client observations. `connected:false` remains.

## Log and verify a measurement

From the owner's shell, capture the actual current instant for a measurement
taken now. `150` is a synthetic example in pounds; replace it with the intended
value before writing. This uses the saved installation environment and owner
credential, then reads the canonical record at that exact instant:

```sh
. "$HOME/health-buddy/env.sh"
MEASURED_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf 'Measurement UTC timestamp: %s\n' "$MEASURED_AT_UTC"
"$PYTHON" -m health_buddy.cli --workspace "$OWNER_WORKSPACE" --credential-file "$OWNER_WORKSPACE/secrets/native-owner-token" log measurement --measured-at-local "$MEASURED_AT_UTC" --timezone UTC --weight-lb 150 --source synthetic-test
"$PYTHON" -m health_buddy.cli --workspace "$OWNER_WORKSPACE" --credential-file "$OWNER_WORKSPACE/secrets/native-owner-token" records --source-ids manual --kinds body-mass --from "$MEASURED_AT_UTC" --to "$MEASURED_AT_UTC" --limit 10
```

The write receipt reports `data.saved: true`, `data.sourceId: "manual"`,
`data.kind: "body-mass"` and the normalized `data.observedAt` for the saved
measurement. Use that `observedAt` as both `--from` and `--to` in the bounded,
authenticated `records` command; keep `--source-ids manual` and `--kinds
body-mass`. The logger's `--source synthetic-test` remains provenance on the
measurement row and is not a `records` selector. In the read response, verify
the `records` entry's `value: 150`, `unit: "lb"`, `kind: "body-mass"`,
`sourceId: "manual"` and `observedAt` equal to the write receipt's
`data.observedAt` (and the printed UTC timestamp).
`records` uses the authenticated `records.list` operation; its bounded window
includes both endpoints and can explicitly include a future timestamp. It
returns exact values and timestamps, unlike a context date summary. An agent
uses the same operation through MCP `list_records` with `sourceIds: ["manual"]`,
`kinds: ["body-mass"]`, and the same `from`/`to` timestamp. Use these canonical
reads to verify a write; private storage files are not the client read API.

`log measurement --help` lists accepted fields. A timestamp with `Z` or a numeric
UTC offset identifies an instant; the offset takes precedence over the timezone
field. A timestamp without an offset uses `--timezone` (an IANA zone such as
`America/Chicago`), or the workspace's configured timezone when omitted. For a
historical measurement, supply its actual time and zone. Do not substitute noon
or another guessed clock time for "today".

A normalized measurement timestamp strictly after the server's receipt time is
saved unchanged with `data.warnings[].code: "future_measurement_timestamp"`.
The warning identifies `fields.measuredAtLocal`, the supplied value, timezone,
normalized `observedAt`, `serverTime` and a recovery hint. Check the time and
timezone and use an explicit read window containing `observedAt`; current
context excludes future observations. A warning does not mean the write failed.
If the write reply is lost, use the same credentialed CLI with `pending retry`;
do not rerun the block to generate a new timestamp. Retries return the original
receipt and warning even after the server clock passes that measurement time.

## Explicit missing-secret recovery and retained-data removal

Ordinary handoff retry never rotates credentials. If the retained pending grant
has no credential output, explicitly repeat the original `health_buddy.install.agent` command
with `--rotate-pending-missing-secret`. It uses canonical `grants.rotate` on that
same recorded actor, revoking its previous credentials without changing the
workspace identity, actor or security epoch. This flag refuses a new/unbound grant,
a later configured stage with a missing token, and existing empty/unknown files.
A retained valid token resumes inertly, even with the flag. If the rotation's
one-time reply is lost before file creation, another rotation requires that same
explicit flag; ordinary retries continue to refuse. No other actor is rotated.


Removal requires deliberate owner consent and quiesced external Docker/Serve/
client/grant editors. It removes the recorded private root route, managed client
entry and managed skill files, revokes only the recorded grant, then stops and
removes only the exact admitted API container ID. If private HTTPS was never
configured there is no route to remove: the journal records Serve removal as
`not_applicable` and every other step runs unchanged. Before its first mutation,
the private journal binds that ID and existing activation/agent/HTTPS selections.
The API inspection checks immutable image, writable workspace bind mount,
nonroot UID/GID and Compose project/service labels whether running or stopped.
It refuses a replacement container, changed mounts/user/image/labels, edited
client entry/skill, changed Serve configuration or original policy mismatch.
Preflight and the external CLIs are not an atomic transaction: quiescence is an
owner precondition, and unsafe drift stops subsequent actions without restoring
an old whole configuration.

```sh
. "$HOME/health-buddy/env.sh"
"$PYTHON" -m health_buddy.install.remove --journal "$PRIVATE_INSTALL/install.json" --policy "$PRIVATE_CLIENT/policy.json" --confirm-remove --confirm-local-daemon --confirm-serve --confirm-quiesced
"$PYTHON" -m health_buddy.install.status --journal "$PRIVATE_INSTALL/install.json"
```

The Docker mutations are only `stop --time 30 EXACT_ID` and `rm EXACT_ID`.
There is no `compose down`, force removal, volume removal, data deletion or
workspace reinitialization. Unrelated services/routes/client settings/grants
remain. The workspace, personal source/assets/tests/notes/state, source bundle,
installer journal, private tokens/settings/policy and runtime environment remain
private recovery material. A revoked token is retained but confers no authority.
After lost action acknowledgements, the journal reobserves owned component state;
repeat issues no mutations once removed. Status remains usable with the retained
owner credential after grant revocation and container removal. Reactivation or
replacement credentials require separate explicit owner lifecycle review.

Client removal writes a private `.health-buddy-remove.json` intent before changing
configuration. It binds original/remaining config hashes, original block/file
hashes and the installation manifest hash, never unrelated config values or
secret bytes. After an internal interruption, absent owned entries are accepted
only under that retained intent; changed present entries still refuse. Unrelated
config edits during the intent also refuse without overwrite. The intent is
removed only after config and all owned files are durably removed.

## Synthetic checks and acceptance boundary

Use the canonical guide's pinned setup in a separate native development
checkout, never in the installed bundle. Select relevant checks for the component
being maintained; from that checkout with its `.venv`, the installer synthetic
gate is:

```sh
PYTHON="$(realpath .venv/bin/python)"
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m pytest -p no:cacheprovider tests/test_install_acquire.py tests/test_install_preflight.py tests/test_install_prepare.py tests/test_install_owner.py tests/test_install_activation.py tests/test_install_https.py tests/test_install_agent.py tests/test_install_remove.py tests/test_connect_removal.py tests/test_agent_guide.py tests/test_runtime_bundle_asset.py
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 "$PYTHON" -m ruff check src/health_buddy/install src/health_buddy/connect_agent.py tests/test_install_*.py tests/test_connect_removal.py tests/test_runtime_bundle_asset.py
RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 "$PYTHON" -m ruff format --check src/health_buddy/install src/health_buddy/connect_agent.py tests/test_install_*.py tests/test_connect_removal.py tests/test_runtime_bundle_asset.py
"$PYTHON" -m mypy src/health_buddy/install src/health_buddy/connect_agent.py
```

Fixtures cover the documented bootstrap from the published bundle asset,
pinned source/archive refusal, durable prepare/owner/activation, real
credentials and scoped grants, fake Serve setup/removal, interrupted client
files and exact-container stop/remove acknowledgements. They preserve identity,
revision, personal notes, unrelated settings/routes/grants and private recovery
material. They never run Docker/Tailscale/client/model processes. These checks do
not qualify an actual daemon, HTTPS connection, named agent or phone; required
live acceptance remains open for the owning tickets, so CES-1077 is not complete.
