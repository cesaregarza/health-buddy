# Portable local first run (CES-1065)

Use Python 3.12+ and Git from a Health Buddy source checkout or unpacked source
bundle. The core command has no third-party Python dependency, account, model
key, global Git identity, signing setup, GitHub connection or host service.

```sh
export PYTHONPATH="$PWD/src"
python -m health_buddy.cli --workspace "$HOME/.local/share/health-buddy" init
python -m health_buddy.cli --workspace "$HOME/.local/share/health-buddy" serve
```

Open the printed `http://127.0.0.1:8791` address. This is a **local development
entrypoint**, without production authentication. It binds only loopback, checks
the exact Host and same-origin mutation requests, and denies cross-origin
preflight. Do not expose it through a proxy, tunnel or public interface. The
production runtime/installer is CES-1068; production authorization is CES-1067.
The wheel still targets library/ingest use: this dashboard entrypoint requires
the source bundle's templates, calculations and scripts. An editable source
install also provides the `health-buddy` command.

The empty dashboard explains missing records. Training accepts completed
workouts with no plan. Settings → context pack lets you choose sections and
copy a local summary for Codex, Claude Code or another assistant, without Jev.
No fabricated demo rows are inserted during initialization.

The selected workspace must be outside the replaceable release tree. There is
no product restriction against a particular mount prefix. Its filesystem must
support private POSIX permissions, local advisory locks and atomic rename.
The coding-agent environment separately requires native Linux paths. Root and
owner directories use mode 0700; config and created owner files use mode 0600.
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
| `storage.manual` | `stores/manual.git`, transitional private CSV backend |
| `storage.healthkit` | `stores/healthkit.db`, optional read-only source |
| `storage.cache` | `cache`, derived render/ranking state |
| `integrations.healthkit.enabled` | `false` |
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
python -m health_buddy.cli --workspace "$HOME/.local/share/health-buddy" log measurement \
  --measured-at-local 2030-01-01T08:00:00 --weight-lb 150 --source manual_entry
python -m health_buddy.cli --workspace "$HOME/.local/share/health-buddy" log intake \
  --event-at-local 2030-01-01T12:00:00 --status consumed --category meal \
  --item-name 'Example meal' --calories-kcal 400 --source manual_entry
python -m health_buddy.cli --workspace "$HOME/.local/share/health-buddy" context --scopes all
python -m health_buddy.cli --workspace "$HOME/.local/share/health-buddy" status
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
entire workspace. This slice does not claim qualified backup/restore, canonical
v1 operations, dataset identities or journal semantics: those remain later
tickets. Git hashes shown by this transitional runtime are **data** revisions,
not code revisions or canonical v1 `dataRevision` values.

## Optional sources and Jev

Disabled adapters do not touch source files. HealthKit opens the configured
SQLite database using `mode=ro` and query-only mode; a missing file is never
created. An empty source means `no_data_or_denied_read`, without guessing read
authorization. SleepIQ's local export contract is exactly `date,sleep_hours`,
one reported local wake date and duration per row; export collection is a
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
UI action, use the configured endpoint/model, have a bounded timeout/response,
and reject redirects. No ambient proxy or agent CLI is consulted. Failure leaves
manual section selection and original workout available; incomplete provider
answers never become invented confidence percentages.

The source-only legacy `context_service.py`, old pipeline and host-oriented
command wrappers are not this entrypoint. Do not start those to bypass config.
CES-1066 will move this adapter behind canonical operations, and CES-1067 will
replace this development HTTP boundary with the contracted identity policy.
