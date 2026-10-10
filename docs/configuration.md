# Portable local first run and canonical operations

Prepare a Health Buddy source checkout or unpacked source bundle under the
[source-development prerequisites](platforms.md#source-development).
Native core operations need no model key, global Git identity, signing setup,
GitHub connection or host service. Install the declared project dependencies
for the maintained Granian/Starlette HTTP entrypoint, for example with
`python -m pip install .` in the selected virtual environment.

```sh
export PYTHONPATH="$PWD/src"
python -m health_buddy.cli --workspace "$HOME/.local/share/health-buddy" init
python -m health_buddy.cli --workspace "$HOME/.local/share/health-buddy" --development serve
```

Open `http://127.0.0.1:8791`. This is a **local development
entrypoint**, without production authentication. It binds only loopback, checks
the exact Host and same-origin mutation requests, and denies cross-origin
preflight. Do not expose it through a proxy, tunnel or public interface. The
production runtime and installer are separate deployment paths. [Authorization setup](authorization.md)
provides explicit native credentials and protected browser sessions; browser
login also needs deliberately configured HTTPS ingress.
Development mode refuses a workspace an installation has prepared
(`development_mode_refused_on_installed_workspace`); there, run commands with
the owner's `--credential-file` instead of `--development`.
The wheel still targets library/ingest use: this dashboard entrypoint requires
the source bundle's templates, calculations and scripts. An editable source
install also provides the `health-buddy` command.

The empty dashboard explains missing records. Training accepts completed
workouts with no plan. Settings → context pack lets you choose sections and
copy a local summary for Codex, Claude Code or another assistant, without Jev.
No fabricated demo rows are inserted during initialization.

Keep the selected workspace outside the replaceable release tree and follow
[platform storage admission](platforms.md#transport-and-admission-limits).
Root and owner directories use mode 0700; config and created owner files use
mode 0600.
Existing permissive files are reported, never silently chmodded. Configured
child symlinks and overlapping storage/config/secret paths are rejected.

## Owner configuration

`init` creates the complete `config.json` schemaVersion 1. Edit that file and
restart the local server to load changes. Unknown, missing, duplicate or invalid
fields are errors. Strings must not have leading/trailing whitespace. Invalid
IANA zones, nonfinite numbers and ambiguous equipment aliases are rejected.

| Field | Meaning / neutral default |
| --- | --- |
| `identity.displayName` | Display label, initially `Health Buddy`; not authentication |
| `timezone` | IANA display/logging zone, initially `UTC` |
| `goals` | Empty; explicit `{id,label,metric:"weight",target,unit:"lb" or "kg",direction:"above" or "below"}` entries |
| `equipment` | Empty; explicit `{id,label,exercise,loadBasis,aliases}` entries |
| `storage.manual` | `stores/manual.git`, private CSV/JSON backend behind canonical operations |
| `storage.healthkit` | `stores/healthkit.db`, one selected optional read-only/receiver source |
| `storage.cache` | `cache`, derived render/ranking state |
| `integrations.healthkit` | `{enabled:false,mode:"read-only"}`; receiver mode is explicit opt-in |
| `integrations.sleepiq` | `{enabled:false,exportFile:"stores/sleepiq.csv"}` |
| `integrations.jev` | `enabled:false`; explicit `apiKeyFile`, HTTPS `endpoint`, `model` |

Equipment IDs/exercise IDs use lowercase letters, numbers, `_`, `-`, `.` and
start with a letter. Load basis is one of `per_hand`, `total_stack`,
`machine_stack`, `total`, `bodyweight`. An equipment selection fills its exercise
and load basis in the workout form; the server checks the same pair. Explicit
aliases identify earlier records from that same physical equipment. Unknown
generic machines remain unverified for progression; loads/bases never merge
because an agent guesses they are equivalent. No equipment or gym is presumed.

Goals are raw owner inputs, shown in dashboard/context with their chosen units
and direction. They are not clinical recommendations, inferred medication
targets, or a training plan. The existing measured-weight trend stays separate
from those owner goals; it does not infer a treatment start.

## Logging and persistence

Fabricated examples (replace with the owner's intended input when using the app):

```sh
python -m health_buddy.cli --workspace "$HOME/.local/share/health-buddy" --development log measurement \
  --measured-at-local 2030-01-01T08:00:00 --weight-lb 150 --source manual_entry
python -m health_buddy.cli --workspace "$HOME/.local/share/health-buddy" --development log intake \
  --event-at-local 2030-01-01T12:00:00 --status consumed --category meal \
  --item-name 'Example meal' --calories-kcal 400 --source manual_entry
python -m health_buddy.cli --workspace "$HOME/.local/share/health-buddy" --development context --scopes all
python -m health_buddy.cli --workspace "$HOME/.local/share/health-buddy" --development status
```

Workout JSON uses the existing validated dashboard payload and can be passed on
stdin to `log workout`. Measurement/intake use the retained validators. The
record zone defaults to config; explicit `--timezone` is validated and preserved.
The selected dashboard zone converts timestamped measurements/intake consistently
for daily grouping. Date-only workout records retain their recorded civil date.
`--data-file` is rejected because all writes belong to the configured adapter.

An optional owner-authored program can be explicitly installed with
`plan --file /path/to/program.json`; it uses the existing program validator and
progression logic. No program is generated by first run. The recorded program,
manual sessions and sets create the live prescription; malformed programs show
a review state rather than an inferred replacement. Optional Fast mode ranks
that exact prescription and preserves its loads, reps, sets and safety rules.

Repeated `init` preserves config, records and `personal/` files. New stores are
staged before placement. Interrupted or malformed existing stores are reported
for recovery, never reset. The adapter uses local Git object plumbing with its
own fixed identity, no remote, no checkout/filter/hook execution, no signing,
and no inherited global/system/repository configuration. Arbitrary pre-existing
Git repositories are not accepted as initialized stores. Preserve a rejected
store, review/restore it from backup, or select a new empty storage path.

`personal/` is for owner source, assets, configuration additions, tests, notes
and state. Initialization adds only a create-if-absent README. `secrets/`,
`operations/` and `security/` are separate private directories. Back up the
entire workspace. Canonical identity, durable receipts and integer dataRevision
now come from the coordinator. Git hashes are internal data-object provenance,
not the API revision or code release. Qualified archive/restore and production
cutover remain separate work. Use the native owner backup context described in
[api-implementation.md](api-implementation.md) to hold health mutation admission
while copying the complete workspace.

## Optional sources and Jev

Disabled read adapters do not touch source files. In read-only mode, HealthKit
opens the configured private mode-0600 SQLite database using `mode=ro`; a missing
file is never created. Existing enabled-only config remains read-only. Explicit
receiver mode may initialize an absent/empty database and registers devices via
the coordinator; it refuses nonempty legacy databases pending operator adoption.
After adoption, changing modes or losing/replacing the bound receiver requires
reconciliation. The two modes never add overlapping datasets. An empty source means `no_data_or_denied_read`, without guessing read
authorization. SleepIQ's local export contract is exactly `date,sleep_hours`,
one unique reported local wake date and duration per row, bounded to 4 MiB and
10,000 rows; export collection is a
separate explicit optional-adapter workflow, never an automatic login.

Source status distinguishes disabled, empty, available, missing and bad-present
data. Freshness uses a 24-hour age for a known last successful receipt/commit
(SleepIQ export mtime), not a claim that every metric is current. Future or older
timestamps are stale; unknown timestamps remain unknown. Metric dates remain
visible in the dashboard. Optional-source errors leave manual records/views
available and do not overwrite those records. Malformed manual CSV is an error,
not an empty health history.

Jev is disabled by default. Setting `enabled:false` prevents secret lookup,
network access and CLI credential fallback even when ambient credentials exist.
To opt in, place the provider key in the configured private mode-0600 file under
`secrets/` and set `enabled:true`. Provider calls happen only after an explicit
request or UI action, use the configured endpoint/model, have a bounded timeout/response,
and reject redirects. No ambient proxy or agent CLI is consulted. Failure leaves
manual section selection and original workout available; incomplete provider
answers never become invented confidence percentages.

The source-only legacy `context_service.py`, old pipeline and host-oriented
command wrappers are not this entrypoint. Do not start those to bypass config.
All supported health writes now use canonical operations. The durable security
authority supplies current owner/agent/device admission. Without explicit
development selection or a currently authenticated credential/session, native
and HTTP operations deny protected access.
See [canonical-clients.md](canonical-clients.md) for pending retries and the
full current command syntax.
