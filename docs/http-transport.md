# Production HTTP boundary

The canonical service owns policy, identity, validation, revisions and durable
receipts. `transport.create_app(operations, authenticate=...)` maps a finite
route table to that service. Authentication is an injected trusted resolver;
the default denies every protected route. The resolver receives bounded raw
headers and the actual peer/server/scheme, never proxy-rewritten identity.
Core rechecks current authorization after the early preflight and before a
durable decision. Constructing a Principal cannot itself confer grants.

`production_server.serve(factory, port=8791, development=False)` uses standard
Granian with one serving child plus its supervisor. The importable/picklable
factory creates service/policy/store handles inside the child. Explicit local
development selects the development principal only for a real loopback peer;
the service still has to permit that principal. A deny-policy service stays
denied. Production bearer/session/pairing/proxy configuration is a later input.

Every dashboard, asset, API and legacy alias shares admission. There are only
two asset basenames, no directory static mount, schema/docs endpoint, debug
mode, WebSockets, automatic code update, reload, metrics or access logging.
`GET /livez` is the sole unprotected resource and returns only `status: ok`.
Loopback Host must match the listening address and port. If Origin is present,
it must match; development writes require it. Forwarding headers do not change
the observed connection. Public hostname, trusted proxy and deployment remain
unconfigured.

## Fixed bounds

| Boundary | Limit |
| --- | --- |
| Server | HTTP/1; one runtime thread and one runtime blocking thread; 16 connections; backlog 128; keep-alive off |
| Admission | 8 active requests, 2 service jobs, 2-second slot wait |
| Headers | 5-second initial read deadline; 16 KiB server buffer; 64 headers, 16 KiB aggregate, 4 KiB per value |
| Target | 4 KiB path/query; no encoded paths, traversal aliases or slash redirects |
| Body | 64 KiB ordinary writes; 256 KiB plans; 4 MiB HealthKit, both compressed and expanded |
| Receive | 10 seconds total from parsed headers, no reset for each chunk |
| JSON | UTF-8 object; depth 32; 20,000 nodes ordinarily, 40,000 for HealthKit; duplicate keys, nonfinite values and lone surrogates rejected |
| Response | 4 MiB before emission; context responses 128 KiB; no truncation or response compression |
| Execution | 20-second service deadline; 10-second total send deadline; 25-second drain before supervisor's 30-second shutdown bound |

Maintained server code owns HTTP parsing. The adapter additionally rejects
duplicate security/framing singleton headers and transfer encoding, requires a
positive bounded Content-Length for writes, and accepts JSON only. HealthKit
alone accepts exactly one complete gzip member with no trailing data. Query
keys are explicitly listed per route; fields/source/time bounds then go to core.
Dashboard `export=1` and Fast `date`/`revision` reads are preserved.
HealthKit's separate node budget accommodates all 500 records and 500 deletions
permitted by retained protocol v1, including all source/device/workout fields,
while preserving the same depth and compressed/expanded byte limits.

Core Response bytes/status/receipt headers pass unchanged. The adapter adds
no-store/nosniff/no-referrer/frame-ancestors and bounded connection framing.
HealthKit success additionally requires all three receiver identity headers
to match the submitted tuple, while retaining the schema-v1 body. The phone
must still verify its complete identity/batch/count acknowledgement contract.
Transport-local errors use `{error, meta:{}}`; unknown paths and wrong methods
use that same safe JSON boundary. Parser-level failures before ASGI may close
the connection or return the maintained server's own 400/408/431 response.

## Write timeout and shutdown

A service job keeps its slot until the synchronous callable actually finishes,
even when the HTTP waiter times out or disconnects. It never runs on the ASGI
event loop. The deadline is an admission signal checked by core before its
durable decision. Once decided, a timeout is an ambiguous outcome, not proof
that nothing was saved. A retry must reuse the exact original identity,
revision, idempotency key and normalized intent. Temporary errors advertise
bounded Retry-After; they cannot allocate an unbounded replacement thread pool.

Shutdown refuses new work and drains the finite job registry. A process killed
after that bound still relies on core's journal recovery before later reads or
writes. HTTP unit/probe tests do not establish that journal guarantee; the
integrated crash/retry tests must exercise real storage.

## Validation scope

`tests/test_transport.py` covers fixed-route denial, policy preflight, malformed
envelopes, field propagation, exact receipt bytes, gzip, bounds and timed-out
job ownership. `tests/test_transport_wire.py` launches the actual Granian
supervisor/child with fabricated in-memory probes to cover wire parsing,
initial-header/body deadlines, process-factory isolation and disconnects.
Those probes do not implement health mutations or durable retry semantics.

`tests/test_portable_http.py` and the portable browser script use the same
maintained launcher with a synthetic canonical workspace. They require the
core implementation and verify real save/alias replay/restart and UI flows.
Recorded results must cite the exact integrated commit. Source tests alone do
not qualify device acceptance, remote authentication, ARM execution, deployment,
migration or release.
