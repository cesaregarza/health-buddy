# Health Buddy

Health Buddy is a self-hosted health tracking workspace for one person who
wants their records on their own machine. It tracks workouts, body
measurements, meals and more through a web dashboard, CLI, HTTP API and MCP
tools, and is built for Codex or Claude Code to install, maintain and extend.

## Install

Run as the owner on a 64-bit Linux host with Docker Compose and, for private
HTTPS, Tailscale 1.102.5. [docs/install-preflight.md](docs/install-preflight.md)
gives every command. In order, a step is done when this output field is
`true`: acquire (`artifactsVerified`), preflight (`preflightPassed`),
prepare (`workspacePrepared`), owner setup (`ownerSetupReady`), activate
(`runtimeActivated`), private HTTPS (`privateRouteConfigured`), agent setup
(`clientConfigurationPrepared`). `connected: false` is expected throughout.
Take the manifest SHA-256 from the publisher through a separate channel, never
from downloaded files. On a refusal, keep the journal and inputs and follow
the printed `recovery` and the doc's notes on that `code`; rerun the identical
command to resume an interrupted step.

## Status

Private pre-release: no release, tag or published manifest exists. Manually
dispatched CI builds amd64 and arm64 runtime images and runs them under Compose
on synthetic data. Not done: installer distribution, a released phone
companion, the public site, an outside pilot.

## Try it locally

From the repository root, in a Python 3.12 virtual environment after
`python -m pip install .`:

```sh
export PYTHONPATH="$PWD/src"
python -m health_buddy.cli --workspace "$HOME/.local/share/health-buddy" init
python -m health_buddy.cli --workspace "$HOME/.local/share/health-buddy" --development serve
```

Open http://127.0.0.1:8791. Development mode has no authentication and binds
only to loopback; never expose it.

## Documentation

### Use

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
