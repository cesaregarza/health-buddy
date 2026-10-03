# License and asset inventory

The owner authorized MIT licensing for owned source. Included source blobs
have no vendored third-party library code or license-header removals. The
original package metadata described private use and supplied no LICENSE or
NOTICE file; it is not copied. `LICENSE` applies to Health Buddy-owned code,
not third-party packages or their trademarks.

No source photographs, screenshots, generated images, fonts, icon binaries or
record snapshots were copied. `health-runner/dashboard/assets/icon.svg` is a
new simple vector drawing authored for this extraction; its manifest is new.
The dashboard uses system fonts and inline owned JavaScript rather than a
bundled frontend framework. Test fixture values are fabricated.

External dependencies are installed by the package manager, not vendored into
source. Preserve their installed `.dist-info` license files and dependency
notices in any redistributed wheel/container or dependency bundle. The selected environment has exact installed versions and license metadata. Any
runtime image needs an audit of transitive dependencies and base-image notices.

| Direct package observed in queue | License determination | Purpose / inclusion |
| --- | --- | --- |
| Python standard library | Python distribution license | Core dashboard and HealthKit runtime; runtime distributor retains notices |
| Granian 2.8.3; Starlette 1.7.0 | BSD-3-Clause | Maintained production HTTP server and ASGI routing |
| AnyIO 4.15.1 | MIT | Bounded non-abandoning service thread handoff |
| Click 8.5.0; idna 3.20 | BSD-3-Clause | Resolved HTTP runtime transitive dependencies on Python 3.12 |
| typing_extensions 4.16.0 | PSF-2.0 | Resolved Python 3.12 typing dependency |
| asyncsleepiq 1.7.1 | MIT | Optional SleepIQ extra |
| SQLAlchemy 2.1.1; Alembic 1.20.0 | MIT | Optional SleepIQ store and migrations |
| tzdata 2026.4 | Apache-2.0; IANA data notice retained in distribution LICENSE | Optional timezone data |
| psycopg 3.3.6; psycopg-binary 3.3.6 | LGPL-3.0-only | Optional PostgreSQL extra; not required by core |
| jsonschema 4.23.0 | MIT | Runtime extension manifest/configuration validation and contract checks |
| attrs 26.1.0; jsonschema-specifications 2025.9.1; referencing 0.37.0; rpds-py 2026.6.3 | MIT project licenses; native rpds components have additional notices | Observed extension schema runtime dependencies |
| types-jsonschema 4.23.0.20241208 | Apache-2.0 | Development-only schema typing; exact wheel and installed metadata verified |
| pytest 9.1.1; Ruff 0.16.9; mypy 1.20.2; build 1.6.1; Hatchling 1.32.4 | MIT | Development and build tooling |
| pytest-asyncio 1.4.0 | Apache-2.0 | Development checks |
| Playwright 1.63.0 | Apache-2.0; bundled notices also retained | Optional browser validation tooling; not shipped in core source |

[provenance/dependency-licenses.json](provenance/dependency-licenses.json)
records the observed package metadata and relative notice-file SHA-256 digests.
The queue collected full notice texts from these installed distributions.
Legacy metadata fields/classifiers establish MIT for asyncsleepiq/jsonschema
and Apache-2.0 for tzdata; the others provide explicit license expressions.
These are observations of the validated environment, not a dependency lock.
The collected file index also includes metadata support files found under
license directories; it does not classify every such file as a legal notice.

Third-party code is not incorporated into Health Buddy's MIT license. In
particular the optional PostgreSQL extra remains LGPL licensed; an eventual
binary dependency or image distribution must retain its notices and meet the
applicable license obligations. Runtime transitive libraries, browser binaries
and base-image notices require review against the artifacts actually shipped.

No claim about an absent runtime image or future packaged dependency bundle is
made by this source inventory. Generated distributions must pass the archive
inspection command before publication. Dependency additions must update this
inventory and retain all applicable upstream notices.

The HTTP dependency-only queue inventory covered six Python wheels and every
one of the selected Granian wheel SBOM's 147 Rust components, preserving 287
actual Rust notice files plus Python notices. The tagged Cargo.lock declares
43 further components absent from that SBOM; those are a broader inventory,
not proven binary membership. Final binary redistribution must qualify the
exact platform/artifact and include all applicable notices. See
[HTTP dependency evidence](docs/http-dependencies.md) for scope and immutable
evidence digests. No dependency source or binary is vendored by this change.

The personal-extension dependency promotion and selected development-only
types-jsonschema typing package have a separate
[dependency evidence inventory](docs/extension-dependencies.md). That inventory
distinguishes installed Python metadata, the verified rpds wheel-shipped SBOM
and its broader Cargo.lock. The exact wheel matches the installed native binary
and SBOM; 18 checksum-verified crate archives supplied 36 actual Rust legal
texts, including LLVM and Unicode terms. Together with six Python notices and
the dev-only typing license, 43 files are retained under
[docs/notices/extensions](docs/notices/extensions/README.md). This is not proof
of static-link membership or future-image qualification.

The optional MCP adapter adds a separate [runtime dependency collection](docs/mcp-dependencies.md)
and [exact artifact/file map](provenance/mcp-dependencies.json). It covers the
29 observed CPython 3.12 Linux x86-64 runtime wheels, their declared notices,
and bounded native source associations, preserving original license choices
and bytes. Development tools and optional provider extras are excluded. The
Health Buddy source distribution retains these texts without redistributing
external dependency binaries. All-target SBOMs, source locks and recipe
associations do not establish all-linked coverage, reproducibility, complete
binary legal compliance or qualification of a future image.

The optional runtime image uses per-architecture binary selections
in `packaging/runtime-inputs.json`; `provenance/runtime-inputs.json` records their
metadata provenance. Source pins are not an assertion that an image has been
built or that final-image notice closure has passed. The controlled native
qualification retains actual installed Python metadata/SBOM and Debian copyright
texts/hashes from each produced image. Those per-architecture receipts, alongside
existing source notices, are required before accepting the final image inventory.
