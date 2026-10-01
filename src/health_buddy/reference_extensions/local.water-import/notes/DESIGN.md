# Maintained synthetic example

All test values are fabricated. Install create-only into personal/extensions,
review source/configuration and enable explicitly. Core source remains unchanged.
No optional provider key, network request or scheduler is used.

The host filters canonical data for the current caller, binds source provenance,
validates outputs and owns durable retry. This code receives finite JSON only.
Local code is trusted and is not sandboxed; inspect it before enabling.

Keep these notes with source, tests and state. Change a display setting or pure
normalization deliberately, run the tests, then enable the new reviewed digest.
Earlier pending events retain their original payload. For standalone native
developer tests, disable bytecode/cache writes:
`PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider tests` from this
extension directory.
