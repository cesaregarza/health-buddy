# Optional MCP dependency checkpoint

The backend's default dependencies are unchanged. The optional `mcp` extra pins
`mcp==2.2.0` and directly used `httpx2==2.13.1`; these are resolution proposals
until the queue verifies actual wheels, the complete dependency graph and
license notices. No SDK, HTTP/2, CLI, rich or provider extra is selected.

Primary source review on 2026-09-29:

- [MCP 2.2.0 metadata](https://pypi.org/pypi/mcp/2.2.0/json) and
  [tagged dependency metadata](https://github.com/modelcontextprotocol/python-sdk/blob/v2.2.0/pyproject.toml)
  declare MIT licensing, Python 3.10+ and a version-matched mcp-types dependency.
- [HTTPX2 2.13.1 metadata](https://pypi.org/pypi/httpx2/2.13.1/json) is the
  directly selected HTTP client; its advertised license is BSD-3-Clause.
- [The public low-level SDK Server](https://github.com/modelcontextprotocol/python-sdk/blob/v2.2.0/src/mcp/server/lowlevel/server.py)
  accepts typed message streams and supports both protocol eras. Its default
  OpenTelemetry middleware is explicitly removed by the adapter. The SDK also
  obtains a tracer at import time: the dedicated entrypoint first clears
  inherited `OTEL_*` configuration, installs the public no-op tracer provider,
  and refuses a process where another provider was already installed.
  `opentelemetry-api` remains a transitive dependency; this is not a claim of
  no telemetry-related packages. No exporter/instrumentation is enabled.
- [SDK stdio transport](https://github.com/modelcontextprotocol/python-sdk/blob/v2.2.0/src/mcp/server/stdio.py)
  does not supply the adapter's required strict byte/UTF-8 bound. Explicit
  streams also require deliberate protocol stdout isolation.

The queue must resolve this optional extra alongside exact existing backend
pins, with binary-only downloads and no source compilation fallback. Preserve
actual artifact SHA-256 values, installed versions, dependency metadata,
license expressions and complete distributed license/notice files. No package
version metadata alone proves installed bytes or runtime behavior. Declared
wheel license files do not establish complete Rust/OpenSSL/libffi component
coverage; exact bundled-component inventory and notices remain a separate gate.
No product
tests are attributable to a dependency-only checkpoint.
