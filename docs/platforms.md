# Platform support and prerequisites

This page owns host prerequisites, preparation authority and platform evidence.
Use the [installation checklist](onboarding.md) and
[stage commands](install-preflight.md) after selecting a path below. Preflight
admission and recorded installation evidence are separate columns: an admitted
architecture still needs its own complete installation qualification.

## Platform matrix

Resource figures are current preflight floors; glibc figures apply to the
host's architecture-specific CPython 3.12 wheel lock, not the container's libc.

| Platform path | Required host facts | What must already exist | What the agent may install / authority | Transport | Verification and model tier | Open blocker |
| --- | --- | --- | --- | --- | --- | --- |
| Linux x86_64 native | x86_64 → amd64; glibc ≥2.34; ≥2 logical CPUs, ≥2 GiB physical RAM, ≥6 GiB free workspace space | CPython 3.12 with venv; native Docker Engine and Compose; tools and storage below | Owner-authorized verified bundle and hash-locked wheels in the private venv; current boundary below | Same-host owner client → managed Unix socket; container `network_mode: none` | Recorded on Ubuntu 24.04 amd64: Sonnet 5, `one-url/4`, runs 27 and 29 accepted; run 29 clean. See recorded evidence below. | Bare-host preparation remains planned in CES-1187–1189; other distributions need their own evidence. |
| Linux aarch64 native, including Raspberry Pi | 64-bit aarch64/arm64 → arm64; glibc ≥2.28; ≥2 logical CPUs, ≥2 GiB physical RAM, ≥6 GiB free workspace space; no 32-bit OS or emulation | Same prepared-host prerequisites as x86_64, with the aarch64 wheel lock | Same owner-authorized bundle/wheel bootstrap; no automatic OS preparation | Same-host owner client → managed Unix socket; container `network_mode: none` | Wheel/bootstrap checks only: native Ubuntu 24.04 arm64 container on a Pi; no complete Sonnet `one-url/4` ARM install recorded | Complete real-ARM installation and Docker qualification: [CES-1083](https://linear.app/cegarza/issue/CES-1083). |
| Windows through WSL2 Ubuntu | Planned Ubuntu 24.04 guest; matching Linux architecture/glibc floor above; CPU, RAM and free-space floors measured inside WSL | Planned Docker Engine inside the distro and agent in that same distro; native Linux filesystem outside `/mnt` | No guided WSL host preparation implemented | Proposed same-distro Unix socket; Docker Desktop is not the selected path | Not yet verified; no recorded installation model tier | Guided path, WSL preflight facts and an operator Surface run: [CES-1190](https://linear.app/cegarza/issue/CES-1190). |
| macOS | Darwin is refused by current preflight; no admitted CPU/RAM/disk baseline; glibc not applicable | No supported packaged-install prerequisite set yet | No host preparation or installation path prescribed | Packaged loopback transport and shared-lock ownership need design; development HTTP cannot substitute | Not yet verified; no recorded installation model tier | [CES-1191](https://linear.app/cegarza/issue/CES-1191) transport/lock/preflight design, then [CES-1193](https://linear.app/cegarza/issue/CES-1193) hardware-dependent operator spike. |

## Current prerequisites

CPython 3.12 with venv and Docker Engine with Compose must already be available.
The installer does not install Python or Docker. The ordinary owner also needs
Bash, Git, the host IANA timezone database and the shell/download tools used by
the bootstrap, including curl, tar and sha256sum. Install stages run without
root or sudo, with nonzero owner UID/GID. The chosen client runs on the API host
as that workspace's OS owner; a client on another host is not an admitted v1 path.

The installation request authorizes the documented verified source bundle and
hash-locked Python packages in the private environment. If Python, venv support,
Docker or Compose is missing, stop and ask the owner to prepare and admit the
host before retrying. A venv failure mentioning `ensurepip` needs the host's venv
package; on Ubuntu 24.04 the existing owner remedy is
`sudo apt-get install python3.12-venv`. Do not install packages into system Python,
choose an unpinned Python/dependency fallback, start a daemon or install system
packages as part of the guided installation.

Python preparation without root, a separately owner-authorized Docker step and
bare-host rehearsal are **not implemented**. They are planned under
[CES-1187](https://linear.app/cegarza/issue/CES-1187),
[CES-1188](https://linear.app/cegarza/issue/CES-1188) and
[CES-1189](https://linear.app/cegarza/issue/CES-1189), respectively. Those plans do
not change today's prerequisites or authorize unpublished preparation commands.

For requested phone/browser private HTTPS, the owner must separately admit
Tailscale 1.102.5, sign-in and the Serve route. Admit private connectivity,
DNS/HTTPS reachability, clock validity and application authorization through the
documented installer stages. Local-only installation uses the
stage guide's local origin/subject and the managed socket without Tailscale.
Tailscale connectivity never grants application authorization.

## Transport and admission limits

[Preflight](../src/health_buddy/install/preflight.py) accepts Linux with
x86_64→amd64 or aarch64/arm64→arm64, checks the resource floors above and inspects
the native Docker CLI and `/run/docker.sock` metadata. Its success does not
establish daemon reachability, Compose/plugin trust, effective cgroup quotas,
rootless/remapped ownership or capacity for personal history. Admit Docker data,
staging and future workspace growth separately from the 6 GiB workspace floor.
The glibc floor comes from the selected wheel closure; preflight does not test it.

The installer requires private owner-owned native directories, without symlink
ancestors, outside `/mnt`. Storage must support POSIX permissions, local advisory
locks and atomic rename. The selected Docker CLI must be a regular executable
with native non-symlink ancestors; commands pin
[`--host unix:///run/docker.sock`](../src/health_buddy/runtime/release.py).
A Docker Desktop CLI symlink under `/mnt/wsl` does not satisfy that admission.
The Python launcher has the separately bounded venv-symlink rules in
[client setup](codex-integration.md#connect-repeat-and-update).

[Compose](../packaging/compose.yaml) bind-mounts the owner workspace and uses
`network_mode: none`. The API host and containers share
`security/runtime/http.sock` and POSIX advisory locks. Both host and container
socket paths must fit Unix socket limits. The packaged runtime publishes no TCP
port; the developer listener on `127.0.0.1:8791` is refused for an installed
workspace. macOS support therefore needs the planned transport and lock design,
not a switch to the developer listener. Docker Desktop, Podman and emulation
have no recorded qualification here.

## Source development

The project declares Python 3.12+; contract checks alone can run on 3.11+.
The maintained locked source/bootstrap path selects CPython 3.12 and the native
Linux architecture's 55-package closure:
[x86_64](../packaging/dev-cp312-linux-x86_64.lock) (glibc ≥2.34) or
[aarch64](../packaging/dev-cp312-linux-aarch64.lock) (glibc ≥2.28).
The source lock covers runtime/MCP, development tools and SleepIQ tests; it does
not supply Python, Git, the timezone database, browser binaries or build tooling.
Use the [agent guide](agent-guide.md#first-runnable-change-weekly-mass-display)
and [verification commands](verification.md) for the exact synthetic setup and
check selection. Source tests and native containers alone do not qualify an
owner installation on another platform.

## Rehearsal hosts

The [fresh-agent rehearsal](verification.md#fresh-agent-install-rehearsal) uses
a private native Linux kit with authenticated doctl, OpenSSH, Bash and Python 3.
Its disposable host needs at least 2 vCPU/4 GB, exceeding the service's memory
floor. The separately authorized operator helper prepares the host and agent
harness before the installation session. This prepared-host evidence does not
prove the planned bare-host preparation flow. Node and model-client versions
belong to the rehearsal harness, not the product's runtime prerequisites.

## Recorded evidence

The [CES-1104 rehearsal ledger](https://linear.app/cegarza/issue/CES-1104) and
[CES-1173 acceptance](https://linear.app/cegarza/issue/CES-1173) record the
2026-10-09 amd64 Sonnet 5 `one-url/4` gate. Run 27 on candidate `9e7c4c7` verified
both workflow signatures; the operator accepted it with the retained
`completion_report_not_first` finding. Run 29 on `beab83e` was clean, with the
host-matching completion report, authenticated record read-back and observer
pass. Run 30 refused the wrong commit before downloading; run 31 passed on a
fresh amd64 host with Claude Code 2.1.197. These records establish the stated
prepared-host path and model tier, not real ARM, WSL2 or macOS qualification.

[CES-1111](https://linear.app/cegarza/issue/CES-1111) records the narrower ARM
check: Ubuntu 24.04 arm64 userspace running natively in a Pi container, glibc
2.39 and CPython 3.12.3, all 55 hash-locked wheels installed, `pip check` and
`acquire --help` successful. That was a dependency/bootstrap check without an
installation model session; the complete real-ARM gate remains open.
