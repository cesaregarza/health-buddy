# Production HTTP dependency selection

The HTTP adapter selects Granian 2.8.3, Starlette 1.7.0 and AnyIO 4.15.1 without
optional extras. Runtime validation and complete resolved notices inventory are
required before accepting the integrated implementation. This source note is
not a test receipt or proof of binary/license inventory completeness.

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
