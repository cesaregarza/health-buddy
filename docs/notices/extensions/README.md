# Personal-extension dependency notices

These are unchanged upstream legal texts retained from hash-verified artifacts.
They are not covered by Health Buddy's MIT license merely because they are
included here. No library source or binary is vendored in this directory.

- `python/`: six observed runtime distribution notices and the development-only
  types-jsonschema license, preserving upstream filenames and text.
- `rust/`: 36 legal files from 18 exact Cargo.lock checksum-verified crates. The
  selected rpds wheel SBOM lists 17; portable-atomic is the separate lock-only
  component. Nested pyo3-runtime notices are retained at their original relative
  location. All-targets SBOM membership is not static-link membership proof.

[The provenance inventory](../../../provenance/extension-dependencies.json)
records each relative path, SHA-256, component version, declared license,
source archive and inventory scope. [Dependency evidence](../../extension-dependencies.md)
describes the selected wheel, matching installed binary/SBOM and remaining
runtime-image/platform qualification. Redistribution must retain applicable
notices for the actual artifacts shipped; this source collection is not an
assertion that all dependencies use the same license.
