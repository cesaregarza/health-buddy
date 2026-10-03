# Health Buddy

Health Buddy is a self-hosted health tracking workspace for one person who
wants their records on their own machine. It tracks workouts, measurements,
meals and more through a web dashboard, CLI, HTTP API and MCP tools, and is
built for Codex or Claude Code to install, maintain and extend.

## Install

Coding agents start at [docs/onboarding.md](docs/onboarding.md): the ordered
stage checklist with its completion test, linking each stage to the guide
below.

Run as the owner on 64-bit Linux with Docker Compose and, for private HTTPS,
Tailscale 1.102.5. [docs/install-preflight.md](docs/install-preflight.md) gives
every command. Step zero, its "Before the first stage" section, gets the source
bundle from URLs and SHA-256s the owner supplies; never take a hash from
downloaded files. Then each step is done when this field is `true`: acquire
(`artifactsVerified`), preflight (`preflightPassed`), prepare
(`workspacePrepared`), owner setup (`ownerSetupReady`), activate
(`runtimeActivated`), agent setup (`clientConfigurationPrepared`), private HTTPS
for the phone and browser (`privateRouteConfigured`). Installed means activation reported
`runtimeActivated: true` and `install.status` confirms
`runtimeLastActive: true`; `connected: false` is expected throughout. On a
refusal, keep the journal and inputs, then follow the printed `recovery` and
the doc's notes on that `code`. Rerunning the identical command resumes an
interrupted step.

Without Tailscale, use the guide's explicit local-only origin/subject values and
connect a same-host agent through the managed Unix socket. Those values do not
configure HTTPS. If future phone/browser values are known, choose them before
owner setup; changing that bound selection later needs lifecycle review.

## Status

Private pre-release: no release or tag exists. Manually dispatched CI builds
amd64 and arm64 images and runs them under Compose on synthetic data. Not done:
installer distribution, a released phone companion, public site, outside pilot.

## Developer loopback mode (not an installation)

For developing Health Buddy itself; never use it to install for an owner.
From a checkout, in a Python 3.12 venv after `python -m pip install .`:

```sh
export PYTHONPATH="$PWD/src"
python -m health_buddy.cli --workspace "$HOME/.local/share/health-buddy" init
python -m health_buddy.cli --workspace "$HOME/.local/share/health-buddy" --development serve
```

Open http://127.0.0.1:8791. Development mode has no authentication and binds
only to loopback; never expose it. It refuses a workspace an installation has
prepared (`development_mode_refused_on_installed_workspace`); there, run
commands with the owner's `--credential-file` instead of `--development`.

## Documentation

### Use

- [Install with a coding agent: stage checklist and completion test](docs/onboarding.md)
- [Local first run and owner configuration](docs/configuration.md)
- [Command-line client and pending writes](docs/canonical-clients.md)
- [Owner, agent and device authorization](docs/authorization.md)
- [Codex setup](docs/codex-integration.md)
- [Claude Code setup](docs/claude-integration.md)

### Develop

- [Agent boundaries, checks and publication policy](AGENTS.md)
- [Maintenance guide for Codex and Claude Code](docs/agent-guide.md)
- [Contributor setup and synthetic preview](CONTRIBUTING.md)
- [Verification commands](docs/verification.md)
- [Source map and data ownership](docs/architecture.md)
- [Personal workspace and extensions](docs/extensions.md)
- [Repository responsibilities and release gates](docs/delivery.md)

### Operate

- [Install, status and retained-data removal](docs/install-preflight.md)
- [Reinstall with retained personal work](docs/install-reinstall.md)
- [Status, doctor and recovery](docs/operator-diagnostics.md)
- [Upgrade staging](docs/recoverable-upgrade.md)
- [Runtime image and Compose packaging](docs/runtime-packaging.md)
- [Encrypted backup and restore](docs/backup-restore.md)
- [Synthetic record import tracers](docs/legacy-import-canary.md)

### Contracts

- [Product, API, identity and security contract](docs/v1-contract.md)
- [Versioned compatibility manifest](contracts/v1/compatibility.json)
- [API operations and ownership](docs/api-implementation.md)
- [Contract acceptance history](docs/validation.md)
- [Extraction provenance and exclusions](docs/extraction.md)
- [Dependency and asset notices](THIRD_PARTY.md)

## License and data

Repository: `cesaregarza/health-buddy`. Owned code is MIT licensed
([LICENSE](LICENSE)); extracted third-party material must retain its notices.
No private records, credentials, private defaults or source repository history
belong here. Daily tracking works without an agent session or a model API key.
The code needs Python 3.12+; the contract checks alone run on 3.11+.
