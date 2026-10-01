# Verification commands for the source bundle

The recorded output of an actual run, not this document, establishes observed
outcomes. Who runs these checks, and what must pass, is set in
[AGENTS.md](../AGENTS.md#checks-and-publication). Use an exact clean candidate
and Python 3.12+. Run one check job and one test process at a time; limit
numerical-library threads to one, and set `RAYON_NUM_THREADS=1` and
`RUFF_NUM_THREADS=1` for Ruff. Synthetic fixtures use an
explicit `America/Chicago` timezone to retain date-boundary regression cases;
product configuration defaults to UTC.

Native Linux socket tests require `HEALTH_BUDDY_TEST_SOCKET_ROOT` to name an
existing, short, private directory owned by the current user with mode `0700`.
The fixture's ownership, permissions, symlink and complete socket-path length
checks remain authoritative. If the variable already names such a directory,
keep it and run the `make` line below directly. Otherwise this subshell creates
a private directory, runs the required checks from the repository root and
removes only that empty directory afterward:

```sh
(
  test -z "${HEALTH_BUDDY_TEST_SOCKET_ROOT:-}" || exit 1
  HEALTH_BUDDY_TEST_SOCKET_ROOT="$(mktemp -d /tmp/hb-uds.XXXXXXXX)" || exit 1
  export HEALTH_BUDDY_TEST_SOCKET_ROOT
  trap 'rmdir -- "$HEALTH_BUDDY_TEST_SOCKET_ROOT"' EXIT
  make PYTHON="$PWD/.venv/bin/python" contracts test dashboard-test lint typecheck
)
```

The manual-only workflow creates `${{ runner.temp }}/hb-uds` with mode `0700`
before its test/build step. Cleanup uses `rmdir` under `always()` only if that
create-only step succeeded; an existing directory is never adopted or removed.
The UID/GID `65534` permission-negative case intentionally skips on a non-root
hosted runner. Only a run as root exercises it; hosted validation does not
provide equivalent evidence for it.

1. Install `.[dev,sleepiq,mcp]` in a native venv; record package versions
   and installed license metadata. No credentials or live sources are required.
2. Run contract validation, root pytest, dashboard unittest, existing typed-source
   Ruff/mypy checks, and whitespace inspection.
3. Build wheel/sdist sequentially. Inspect every archive entry and source file
   using `scripts/audit_distribution.py --root CHECKOUT --archives DIST`;
   `--root` requires a Git inventory, not an unpacked sdist. An archive check may
   use an ephemeral Git index containing only its exact source snapshot.
   Archives are inspected without extraction or link traversal. Run the
   fabricated hostile-entry tests and scan the new history independently.
4. Render the default synthetic preview. Run `check_browser.py`,
   `check_followups.py`, `check_training_views.py`, `check_fast_mode.py`,
   `check_portable_browser.py`, `check_client_workflow.py` and
   `check_auth_browser.py` and `check_extensions.py` serially from the dashboard directory, using an
   existing Playwright Chromium installation. They use
   fabricated sources/mocked optional provider responses. Never run a live Jev
   probe or connect a personal data source. The auth browser uses a routed
   synthetic security model; root pytest separately exercises the real security
   authority and canonical service over the pinned Unix-socket server.
5. Record exact commands, SHA, dependency versions, failures, raw logs and skipped
   checks. Package/browser/fixture failures require repair before acceptance.

Root pytest includes exact generic/HK replay, source collisions, stable IDs,
scoped extension conformance, direct-ID/window/cache behavior, >1,000-row adoption,
separate-process writer/read/backup exclusion and hard-exit recovery. Record
actual request/manifest lengths for the maximal-schema500+500case from pytest
JUnit properties. This case proves a >1MiB recoverable manifest below the request
cap; it does not claim to fill the 4MiB wire budget.

Use the pinned runtime/dependency environment. An offline wheel/sdist build may
use the already installed backend with `--no-isolation`; identify that command
and its difference from an isolated hosted build in the receipt. Validation does
not authorize downloads beyond the pinned dependencies, or any hosted CI run.

Security authority, synthetic pairing and native/HTTP admission are source
verification gates; the actual-authority UDS case is separate from fake-authority
wire fixtures and the routed-model auth browser. Deferred evidence: runtime images and
ARM64 execution (1068), copied archive/restore qualification (1070), physical
phone/Pi, private migration/cutover, cross-agent/upgrade qualification and release.
Process hard exits plus fsync source review do not prove physical power-loss
survival on arbitrary filesystems. The receipt records exact tested commit and
raw outcomes; this document does not imply those gates have passed.

Personal extension checks are part of the repeatable entrypoints: `make test`
runs root tests plus both packaged reference suites without bytecode/cache
writes; `make extension-test` selects the focused extension suites and those
same reference cases. `make lint` retains its existing source scope and adds
extension test/support and browser files, plus the MCP tests/support and
workspace discovery and canonical source-status regression tests. Full
verification installs the optional `mcp` extra
so root pytest and configured mypy see the actual SDK/HTTPX2 types; backend
runtime installation alone does not need this extra. Strict mypy retains
all current source modules, including the maintained examples; the matching
pinned `types-jsonschema` is a development-only type dependency.

The eighth browser script, `check_extensions.py`, uses real same-origin workers
with a routed synthetic metric API. It verifies escaped text, unit/config
changes, source/missingness/stale/limited notices, connector exclusion and failed
or hung view isolation at two viewport widths. It is not real-authority wire
evidence: `test_extension_boundaries.py` separately runs the pinned Granian
server with the actual security authority and canonical service, including
owner/agent denial of nonexistent code-activation routes. Browser installation
and all eight serial browser jobs are manual steps; the manual CI workflow runs
no browser job and downloads no browser.

Preservation evidence distinguishes fresh-process source selection with the
same external owner workspace from copied restore/container replacement.
Directory-fsync failure/retry ordering, bounded child cleanup, registry graph
limits, capacity-before-lock allocation and actual backup/job exclusion have
separate cases. Exact raw check output must establish which assertions ran;
source authorship alone is not verification.
