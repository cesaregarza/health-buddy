# Contributing

Read `AGENTS.md`, `docs/architecture.md`, `docs/v1-contract.md` and
`docs/extraction.md`. Both Codex and Claude Code use this same source map,
contract corpus and synthetic examples. State the exact baseline commit in a
handoff; keep durable decisions in source-controlled docs/tests, not only chat.

Use native Linux and Python 3.12+. Core extracted ingest/render code uses the
standard library. SleepIQ, PostgreSQL and browser tooling are optional extras.
No health account, agent provider key or private repository is needed for the
synthetic checks. Never import personal data to repair a failing test.
`make test` is the fast tier and includes both maintained extension examples;
`make test-slow` runs the slow tier and `make extension-test` the
focused personal-extension suite. The private socket-root prerequisite
and exact commands are in [verification](docs/verification.md).

Run the checks yourself under the policy in
[AGENTS.md](AGENTS.md#checks-and-publication), with
`HEALTH_BUDDY_TEST_SOCKET_ROOT` set first:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[dev,sleepiq,mcp]'
make PYTHON="$PWD/.venv/bin/python" contracts test dashboard-test lint typecheck
# Packaging or distribution changes also build and audit the archives:
make PYTHON="$PWD/.venv/bin/python" package
.venv/bin/python scripts/audit_distribution.py --root . --archives dist
# Synthetic dashboard preview:
make PYTHON="$PWD/.venv/bin/python" preview
```

The preview uses `dashboard_fixture.py` and renders fabricated data into the
ignored `health-runner/dashboard/design/preview/` directory. Open that local
HTML only; it is not an installation, live health endpoint or release demo.
No secret store, account sync or provider is consulted by the synthetic render.
Browser suites require the optional `browser` extra and an installed
Playwright Chromium; run the eight `check_*` scripts listed in
`docs/verification.md`.

`.github/workflows/ci.yml` runs the same checks plus `package`, the
distribution audit and `git diff --check`, only on manual dispatch; the Actions
policy is in [AGENTS.md](AGENTS.md#checks-and-publication). No Apple runner,
signing, merge, deploy or publication is part of these commands.

Preserve behavioral tests for retries, tombstones, validation, missing values,
source provenance, progression, context and UI. Change one owning interface and
its callers/tests together. Use the canonical API and client-workflow maps before adding behavior. Retained
legacy helpers are pure calculations or explicit compatibility adapters; they
must not introduce a second authoritative writer.
