# Contributing

Read `AGENTS.md`, `docs/architecture.md`, `docs/v1-contract.md` and
`docs/extraction.md`. Both Codex and Claude Code use this same source map,
contract corpus and synthetic examples. State the exact baseline commit in a
handoff; keep durable decisions in source-controlled docs/tests, not only chat.

Use native Linux and Python 3.12+. Core extracted ingest/render code uses the
standard library. SleepIQ, PostgreSQL and browser tooling are optional extras.
No health account, agent provider key or private repository is needed for the
synthetic checks. Never import personal data to repair a failing test.

During project orchestration, submit these commands to the single testing
queue with a frozen SHA; do not execute them in an implementation worker:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[dev,sleepiq]'
make PYTHON=.venv/bin/python contracts test lint typecheck package
make PYTHON="$PWD/.venv/bin/python" dashboard-test
.venv/bin/python scripts/audit_distribution.py --root . --archives dist
make PYTHON=.venv/bin/python preview
```

The preview uses `dashboard_fixture.py` and renders fabricated data into the
ignored `health-runner/dashboard/design/preview/` directory. Open that local
HTML only; it is not an installation, live health endpoint or release demo.
No secret store, account sync or provider is consulted by the synthetic render.
Browser suites require the optional `browser` extra and a queue-owned Chromium
installation; run the six `check_*` scripts listed in `docs/verification.md`.

CI is present as an explicitly dispatched Linux verification workflow. Pushes
and pull requests do not run it during the user's GitHub-minutes hold. The
coordinator controls the eventual Actions batch. No Apple runner, signing,
merge, deploy or publication is part of these commands.

GitHub requires a manual workflow to exist on the default branch for initial
discovery ([GitHub event documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch)).
This unmerged PR therefore does not promise hosted execution. Do not merge or
write directly to the default branch to bypass that gate. Normal push/PR CI
triggers are a later explicit policy decision after the minutes hold ends.

Preserve behavioral tests for retries, tombstones, validation, missing values,
source provenance, progression, context and UI. Change one owning interface and
its callers/tests together. Use the canonical API and client-workflow maps before adding behavior. Retained
legacy helpers are pure calculations or explicit compatibility adapters; they
must not introduce a second authoritative writer.
