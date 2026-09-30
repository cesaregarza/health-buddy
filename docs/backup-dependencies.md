# Backup crypto input provenance

CES-1070 reuses the existing hosted-SDK versions and license notices:
`cryptography==50.0.1`, `cffi==2.1.1`, `pycparser==3.0`. Product dependencies and
both runtime platform wheel inventories now name those exact versions.
The existing amd64/common URL, size and SHA256 records come unchanged from
`packaging/sdk-host-inputs.json` and `provenance/mcp-dependencies.json`.
License texts remain under `docs/notices/mcp/python/`; see the existing MCP
provenance license-member mappings. The SDK profile itself is unchanged.

ARM64 descriptors come from official version metadata queried by the serialized
queue without installation or wheel import:

| Package | Primary metadata | Wheel SHA256 |
| --- | --- | --- |
| cryptography 50.0.1 | [PyPI version metadata](https://pypi.org/pypi/cryptography/50.0.1/json) | `e2ca8fd1b6b4b82a1c4cb02841d0837e3c12336c2e24b520ab8ab3b969733d8f` |
| cffi 2.1.1 | [PyPI version metadata](https://pypi.org/pypi/cffi/2.1.1/json) | `68e62fe11f30d5ca8289242866f0a5291402d8529ca2178ab8afc5c9694ae890` |

The crypto wheel is CPython 3.11 ABI3/manylinux 2.34 aarch64; cffi is CPython
3.12/manylinux2014 aarch64. The common pycparser wheel is pure Python. Exact
URLs, byte lengths and hashes are in `packaging/runtime-inputs.json`; runtime
acquisition verifies them before offline image installation. Metadata approval
and source tests do not qualify a newly built runtime image on either native
architecture. No source-built crypto/Rust/OpenSSL path is introduced.
