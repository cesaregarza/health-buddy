# CES-1063 acceptance and evidence boundaries

The checked-in files are a contract target. **No server, agent package, phone,
installer, source extraction, deployment or release is implemented here.** The
finite relational validator checks that examples satisfy independent invariants;
it does not simulate an HTTP service or prove transaction/crypto correctness.

| Acceptance criterion | Reviewable artifact |
| --- | --- |
| Reads/writes/retries/stale updates, pairing/revocation/epochs | `contracts/v1/fixtures/scenarios.json`, `fixtures/lifecycle.json`; `scripts/validate_contracts.py` checks state and receipt relationships |
| Responsibilities/order/compatibility/migration/rollback | `docs/delivery.md`, `docs/v1-contract.md`, versioned compatibility JSON and JSON Schema |
| No mandatory Kubernetes/model key/database rewrite; deferred scope | Product decisions and ownership sections of `docs/v1-contract.md` |
| Naming/host assumptions explicit | Repository chosen; hostname pending deployment; Linux/Pi targets pending real qualification |
| Concrete view/metric and connector/workflow supported paths | `docs/extensions.md`, `contracts/v1/examples/*.json`, extension schema; runtime/scaffolds owned by CES-1085/1086 |
| Fresh-agent creation, different-agent maintenance, update/restore survival | Five-step release qualification in `docs/extensions.md`; lifecycle inventory and compatibility examples; live evidence owned by CES-1083 |

## Corpus conventions

Every exchange carries an explicit request and response plus independently
authored before/after state and verified test context. `context` is harness
input (verified proxy origin, grants, clock); it is never part of client JSON.
`fixture:canonical-reconciliation` is a pure canonical projection example, not
a deployable HTTP endpoint or alternate HealthKit schema. All other paths are
the contract's wire targets. Tokens, IDs, records and times are synthetic.

The 27 cases cover source availability/freshness/missingness, exact writes,
identical/conflicting retries, stale revisions, proxy spoofing, tagged clients,
read/write/maintenance boundaries, pairing expiry/replay/overgrant, revocation
before retry, HealthKit acknowledgement and missing negotiation, restored epochs
and replacement-phone reconciliation. Lifecycle fixtures cover old backup
credentials, preserved personal inventory, compatible/incompatible extensions
and core-fork preflight. Example outputs cover correct units, metric missingness
and deterministic connector event keys.

JSON Schemas enforce structural compatibility. Relational checks reject
duplicate writes, partial receipts, confused grant boundaries, identity drift,
resurrected authorization, dropped personal files and unsafe activation.
Mutation tests deliberately introduce those failures. Do not count a fixture
oracle pass as implementation acceptance for a future server; preserve these
cases as conformance inputs and add tests against real interfaces then.

## Queue-owned verification

Use Python 3.11+ and an isolated native Linux environment with
`requirements-contract.txt`. No network or database is used after installing
the single direct development dependency. The testing queue owns installation
and all commands; no parallel test runners are needed.

```sh
python3 scripts/validate_contracts.py
python3 -m unittest discover -s tests -v
git diff --check BASE..HEAD
```

The queue receipt must name the exact commit, predecessor, Python/dependency
versions, commands, outcomes and raw logs. No GitHub Actions workflow is added
in this slice; hosted verification remains held for the final controlled batch.

Production obligations remain: durable atomic journal/ledger and concurrent
write/crash recovery; cryptographic token/pairing protection, rate limits and
trusted proxy configuration; actual snapshot content/digest and behavior
checks; extension load/preview/enable/failure isolation; cross-agent maintenance;
host/phone/Apple acceptance. The corpus cannot establish these by itself.
