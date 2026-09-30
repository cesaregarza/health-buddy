# Extension validation dependencies

`jsonschema==4.23.0` is a pinned runtime dependency for the installed manifest
and owner-config schema checks. Validation uses an empty local reference
registry and rejects remote schema references. No format extras, network schema
resolver, Rust build fallback or new agent provider is enabled.

The initial CES-1085 dependency receipt observed the following existing Python
3.12 distributions: jsonschema 4.23.0, attrs 26.1.0,
jsonschema-specifications 2025.9.1, referencing 0.37.0, rpds-py 2026.6.3
(all project licenses MIT), and typing_extensions 4.16.0 (PSF-2.0).
These are observations of that environment, not a cross-platform dependency
lock. The relative notice digests are recorded in
[the extension inventory](../provenance/extension-dependencies.json).

`types-jsonschema==4.23.0.20241208` is a development-only Apache-2.0 typing
package. Its exact 15,021-byte wheel is selected for strict typing of the same
runtime API; it is not imported by production. The queued installation must
verify the wheel and installed metadata, use no dependency resolver and retain
its actual license text.

The installed rpds-py distribution includes a CycloneDX 1.5 SBOM with 17 Rust
components. Its `cdx:rustc:sbom:target:all_targets=true` property describes a
build inventory; it does not establish which code was retained in the native
Linux binary. The release's Cargo.lock has 18 third-party crates; the additional
portable-atomic crate is absent from that SBOM. License expressions alone do
not replace actual upstream license and notice texts. In particular,
target-lexicon uses `Apache-2.0 WITH LLVM-exception`, and unicode-ident uses
`(MIT OR Apache-2.0) AND Unicode-3.0`.

The exact Linux CPython 3.12 rpds wheel, byte-identical SBOM comparison, and
checksum-verified component notice collection are pending the candidate's
queue artifact job. The source inventory records that boundary explicitly.
Do not infer completed wheel or container redistribution qualification from
installed metadata. CES-1068 must retain applicable notices in each actual
runtime artifact, including its base image and selected architecture.
