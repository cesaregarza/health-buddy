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
notices in any redistributed wheel/container or dependency bundle. The queue
records exact installed versions and license metadata; CES-1068 audits the
actual runtime image, including transitive dependencies and base-image notices.

| Direct package observed in queue | License determination | Purpose / inclusion |
| --- | --- | --- |
| Python standard library | Python distribution license | Core dashboard and HealthKit runtime; runtime distributor retains notices |
| asyncsleepiq 1.7.1 | MIT | Optional SleepIQ extra |
| SQLAlchemy 2.1.1; Alembic 1.20.0 | MIT | Optional SleepIQ store and migrations |
| tzdata 2026.4 | Apache-2.0; IANA data notice retained in distribution LICENSE | Optional timezone data |
| psycopg 3.3.6; psycopg-binary 3.3.6 | LGPL-3.0-only | Optional PostgreSQL extra; not required by core |
| jsonschema 4.23.0 | MIT | Contract development checks |
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
