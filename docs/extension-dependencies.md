# Extension validation dependencies

`jsonschema==4.23.0` is a pinned runtime dependency for installed manifest and
owner-config schema checks. Validation uses an empty local reference registry
and rejects remote schema references. No format extras, network schema resolver,
Rust build fallback or new agent provider is enabled.

The selected Python 3.12 dependency inventory contains: jsonschema 4.23.0,
attrs 26.1.0, jsonschema-specifications 2025.9.1, referencing 0.37.0 and rpds-py
2026.6.3 (MIT project licenses), plus typing_extensions 4.16.0 (PSF-2.0).
These are observations of that environment, not a cross-platform dependency
lock. Exact versions, source URLs, relative legal-text paths and SHA-256 digests
are in [the extension inventory](../provenance/extension-dependencies.json).

`types-jsonschema==4.23.0.20241208` is a development-only Apache-2.0 typing
package. The queue verified its exact 15,021-byte wheel, installed it with no
dependency resolver, retained its actual license and passed `pip check`. It is
not a production runtime dependency.

The exact Linux CPython 3.12 x86-64 wheel
`rpds_py-2026.6.3-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`
is 366,189 bytes with SHA-256
`ecabd69db66de867690f9797f2f8fa27ba501bbc24540cbdbdc649cd15888ba6`.
The queue verified that its native extension bytes match the installed binary,
and that its 20,594-byte CycloneDX SBOM matches the installed SBOM exactly.
The SBOM has 17 Rust components and
`cdx:rustc:sbom:target:all_targets=true`; that is an artifact-shipped build
inventory, not proof of each component's static-link retention in the binary.

The release's Cargo.lock contains 18 third-party crates. Every retrieved crate
archive matched its lock checksum. The additional portable-atomic crate is
absent from the wheel SBOM and is recorded as lock-only. The queue collected
36 actual Rust license/notice files, including the target-lexicon LLVM exception
and unicode-ident Unicode notice. No dependency was built from source.

[Retained legal texts](notices/extensions/README.md) include those 36 files,
six Python distribution notices and the development stub license: 43 files,
226,398 bytes. Only legal texts are copied; no package code, binary, private
queue receipt or host path is included. The inventory binds the evidence to
source revision `ee27684fdcca47beb10f4178d02f376a181f7ea2`; raw receipts remain
with the coordinator. Later candidates recheck unchanged declarations and text
hashes rather than attributing old checks to a new source revision.

These notices accompany the source bundle. Any runtime artifact must retain
applicable notices for its base image and selected architecture. This collection does not qualify an unbuilt image or another
platform and does not relabel all native components as MIT.
