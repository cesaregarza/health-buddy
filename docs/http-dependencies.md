# Production HTTP dependency selection

The HTTP adapter selects Granian 2.8.3, Starlette 1.7.0 and AnyIO 4.15.1 without
optional extras. The queue completed dependency-only resolution and selected
wheel notice collection. Runtime verification remains separate; this source
note is not an implementation test receipt or binary redistribution approval.

## Primary sources

- [Granian release](https://github.com/emmett-framework/granian/releases/tag/v2.8.3),
  [tagged options](https://github.com/emmett-framework/granian/blob/v2.8.3/README.md),
  [HTTP settings](https://github.com/emmett-framework/granian/blob/v2.8.3/granian/http.py),
  [Python metadata](https://github.com/emmett-framework/granian/blob/v2.8.3/pyproject.toml),
  [Rust metadata](https://github.com/emmett-framework/granian/blob/v2.8.3/Cargo.toml),
  [Rust lock](https://github.com/emmett-framework/granian/blob/v2.8.3/Cargo.lock).
- [Starlette metadata](https://github.com/Kludex/starlette/blob/1.7.0/pyproject.toml).
- [AnyIO metadata](https://github.com/agronholm/anyio/blob/4.15.1/pyproject.toml).

Granian and Starlette declare BSD-3-Clause; AnyIO declares MIT. Granian has a
compiled Rust runtime, so its Python metadata does not enumerate every embedded
dependency. Preserve the actual distributions' copyright/license notices and
inventory the selected binary's Rust dependencies before redistribution. The
project's own MIT license does not replace upstream notices.

Granian's Python dependency is Click. On Python 3.12, Starlette needs AnyIO and
typing_extensions; AnyIO needs idna and typing_extensions. The queue records
exact resolved transitive versions and notices. Do not infer a complete lock
from the three direct pins.

## Linux wheel and process boundary

The [2.8.3 release files](https://pypi.org/project/granian/2.8.3/#files), inspected
2026-09-29, publish these CPython 3.12 Linux wheels:

| Architecture | Wheel tag | Minimum glibc |
| --- | --- | --- |
| x86-64 | cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64 | 2.17 |
| ARM64 | cp312-cp312-manylinux_2_28_aarch64 | 2.28 |

Use matching prebuilt wheels. Missing compatibility must fail visibly rather
than silently compiling Rust. File availability is not ARM64 execution proof;
multiarchitecture packaging still requires its own verification.

Use the standard supervisor with one serving worker and explicit resource
limits. Do not use the experimental embedded server. The operations factory
constructs its service inside the serving child; live stores, policy locks and
database connections must not be inherited from a supervisor-owned instance.

## Selected environment evidence

The dependency-only queue captured clean source
`c1ec9f35aa7552d669a9ec10877a2ae01bec2539`, then installed only binary wheels on
CPython 3.12.13 Linux x86-64. `pip check` passed. Exact resolved versions were
Granian 2.8.3, Starlette 1.7.0, AnyIO 4.15.1, Click 8.5.0, idna 3.20 and
typing_extensions 4.16.0. The adjacent
[wheel observations](http-linux-x86_64-cp312-wheels.txt) record the downloaded
hashes for this environment, not a multiarchitecture installation lock.

The immutable queue receipt SHA-256 is
`98d8b447b76ce2eb43a154053e8b481bec5fd1502312674fc87c3b3734b6bb55`.
The coordinator's preserved evidence manifest SHA-256 is
`ae040e3630cbdf378f2469f2dbe169fbf5c7f502fd294999bec38a0ffa0dd120`.
It indexes the wheel metadata/SBOM, tagged Rust inputs, Python notice digests
and all 147 SBOM components' actual notices (287 Rust files). No selected SBOM
component had a retrieval gap or unknown license metadata; upstream expressions
contained no reciprocal/copyleft identifiers. These are upstream metadata
observations, not a choice of terms or a legal conclusion.

The SBOM declares all-target inventory. Cargo.lock contains 43 additional
components absent from that SBOM; their notices were outside this bounded
selected-wheel collection. SBOM membership does not prove linkage. The future
runtime image must qualify its exact binary/platform, preserve applicable
upstream notices, and resolve that broader inventory if its build includes any
additional components. No Rust compiler, source-build fallback or ARM runtime
execution was used for the dependency receipt.
