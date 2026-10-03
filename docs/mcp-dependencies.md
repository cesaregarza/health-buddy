# Optional MCP dependencies and retained notices

The backend's default dependencies are unchanged. The optional `mcp` extra pins
`mcp==2.2.0` and directly used `httpx2==2.13.1`. The verification queue resolved
this extra with the existing backend pins, installed 29 exact binary wheels on
CPython 3.12.13 Linux x86-64, repeated an offline hash-locked installation and
passed `pip check`. No source compilation or SDK optional extra was selected.
The SDK still brings transitive packages that this adapter does not directly
use; the inventory includes them rather than assuming they are absent.

[The public inventory](../provenance/mcp-dependencies.json) records each of the
29 wheel URLs, versions, filenames and SHA-256 values, upstream license
metadata, and the exact retained legal-text file paths, sizes and
hashes. This is an observed environment, not a cross-platform lock. The
separately augmented development environment, build tools, browsers, and
optional SleepIQ/PostgreSQL packages are outside this MCP runtime collection.
Dependency installation evidence does not establish adapter behavior; use the
[verification workflow](verification.md) for source, SDK and browser checks.

## Collection and artifact associations

The collection retains upstream bytes, including their original whitespace.
Health Buddy's MIT license does not replace upstream terms.

| Association | Retained scope and evidence |
| --- | --- |
| 29 Python wheels | All 33 declared notice files from the exact selected wheel members. |
| Granian 2.8.3 | The wheel hash matches the earlier HTTP selection; its exact wheel RECORD/native member and SBOM mapping are retained. The same all-target SBOM supplied 147 component associations and 287 legal texts. Its source lock declares 43 further components outside that SBOM collection. |
| rpds-py 2026.6.3 | The wheel, native member and SBOM hashes match the extension dependency inventory. Its 18 source-lock component associations supplied 36 legal texts; one source-lock component is absent from the all-target SBOM. |
| pydantic_core 2.46.5 and cryptography 50.0.1 | Exact wheel SBOM and same-version PyPI source archives bind the recorded associations. The conservative union of their source locks contains 134 registry components, with 245 retained legal texts. Five package license texts are retained from the same-version source archives; source workspace associations remain in the inventory without copying their Cargo manifests. |
| wit-bindgen-rt 0.39.0 | The checksum-verified crate's clean Cargo VCS metadata points to commit `f2393e6e98fa5f9236cac580db8a3fc9de6a4b70`, path `crates/guest-rust/rt`. All three license alternatives are retained from that exact revision; the inventory records the workspace/runtime declaration provenance without retaining the manifests. |
| CFFI 2.1.1 / libffi 3.4.6 | CFFI's immutable release recipe names libffi 3.4.6; the selected CFFI binary's static ffi symbol corroborates the association. The libffi license is retained from commit `3d0ce1e6fcf19f853894862abcbac0ae78a7be60`. Recipe association is weaker than full binary provenance. |
| cryptography / OpenSSL 4.0.2 | The wheel-carried OpenSSL SBOM names this source version. Five exact archive inputs retain named legal texts and build-support notices; version metadata and executable support code are excluded. |

There are 612 retained upstream files (3,146,016 bytes) under
[notices/mcp](notices/mcp/README.md). Only legal texts are retained; Cargo
manifests and locks, version metadata and executable support files are excluded. File counts are not counts of
unique licenses or proven linked components. Repeated component versions
can have separate associations with multiple wheels. Public provenance stores
original archive members or immutable upstream paths rather than private
verification directories or logs.

License expressions preserve upstream `OR` choices and `AND` requirements,
including LLVM and Unicode terms. For example, `r-efi` and `self_cell` contain
reciprocal terms as alternatives; recording those expressions neither selects
an alternative nor declares them mandatory. Read the applicable original
texts and exact artifact context before distributing a dependency bundle.

## Limits of this source collection

All-target SBOMs and source locks do not prove that every listed component is
linked into a particular wheel. Same-version source archives do not prove a
reproducible wheel build. Member hashes taken from a wheel RECORD are labeled
separately from hashes measured on collected member bytes. Granian/RPDS reuse
is bound to the exact wheel hashes, not just matching version labels.

The OpenSSL collection covers named legal texts and build-support notices.
It does not establish an independent audit of every inline copyright/license header.
The CFFI recipe and static symbol do not establish an exhaustive bill of
materials. These limits remain part of binary artifact qualification.

This Health Buddy source distribution carries the retained texts; the Health
Buddy wheel/sdist does not redistribute the external dependency binaries.
A future image, dependency bundle or different platform must qualify its own
actual bytes, base-image components and applicable notice obligations. This
inventory is not a claim of complete binary legal compliance, all-linked
component coverage, publisher attestation, or image/reproducibility closure.

## Maintained SDK and privacy boundaries

Primary sources inspected for this implementation:

- [MCP 2.2.0 metadata](https://pypi.org/pypi/mcp/2.2.0/json) and
  [tagged dependency metadata](https://github.com/modelcontextprotocol/python-sdk/blob/v2.2.0/pyproject.toml)
  declare MIT licensing, Python 3.10+ and a version-matched mcp-types dependency.
- [HTTPX2 2.13.1 metadata](https://pypi.org/pypi/httpx2/2.13.1/json) describes the
  directly selected HTTP client and its BSD-3-Clause project license.
- [The public low-level SDK Server](https://github.com/modelcontextprotocol/python-sdk/blob/v2.2.0/src/mcp/server/lowlevel/server.py)
  accepts typed message streams and supports both protocol eras. The adapter
  explicitly clears its default OpenTelemetry middleware. The SDK also obtains
  a tracer at import time: the dedicated entrypoint first clears inherited
  `OTEL_*` configuration, installs the public no-op tracer provider, and refuses
  a process where another provider was already installed. `opentelemetry-api`
  remains a transitive dependency. No exporter/instrumentation is enabled.
- [SDK stdio transport](https://github.com/modelcontextprotocol/python-sdk/blob/v2.2.0/src/mcp/server/stdio.py)
  does not supply this adapter's required strict byte/UTF-8 and partial-frame
  time bounds. The adapter supplies bounded streams with deliberate protocol
  stdout isolation through the public Server API.

No provider key, model call, exporter, or telemetry service is needed by the
adapter. The chosen AI host can receive the health context its user requests;
that separate egress boundary remains explicit in the
[MCP setup and daily workflow](mcp-adapter.md).
