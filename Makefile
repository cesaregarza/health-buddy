PYTHON ?= python3
LEGACY_TYPED = src scripts/next_workout.py scripts/publish_training_day.py scripts/prescription_progression.py
REFERENCE_TESTS = src/health_buddy/reference_extensions/local.weekly-mass/tests src/health_buddy/reference_extensions/local.water-import/tests
EXTENSION_TESTS = tests/test_extension_*.py
RUNTIME_LINT = scripts/package_runtime.py scripts/runtime_entrypoint.py packaging/*.py tests/test_runtime_*.py tests/test_packaged_runtime.py
EXTENSION_LINT = $(EXTENSION_TESTS) tests/extension_fixtures.py health-runner/dashboard/scripts/check_extensions.py
MCP_LINT = tests/test_mcp_*.py tests/test_workspace_discovery.py tests/mcp_wire_fixtures.py tests/mcp_process_runner.py
CANONICAL_READ_LINT = tests/test_canonical_reads.py
AGENT_GUIDE_LINT = tests/test_agent_guide.py

.PHONY: contracts test extension-test dashboard-test lint typecheck package preview
contracts:
	$(PYTHON) scripts/validate_contracts.py
test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m pytest -p no:cacheprovider tests $(REFERENCE_TESTS)
extension-test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m pytest -p no:cacheprovider $(EXTENSION_TESTS) $(REFERENCE_TESTS)
dashboard-test:
	cd health-runner/dashboard && HEALTH_TIMEZONE=America/Chicago $(PYTHON) -m unittest discover -s tests -v
lint:
	RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 $(PYTHON) -m ruff check $(LEGACY_TYPED) $(EXTENSION_LINT) $(RUNTIME_LINT) $(MCP_LINT) $(CANONICAL_READ_LINT) $(AGENT_GUIDE_LINT)
typecheck:
	$(PYTHON) -m mypy
package:
	$(PYTHON) -m build
preview:
	$(PYTHON) health-runner/dashboard/scripts/preview.py --output health-runner/dashboard/design/preview/index.html

# Exact packaging tools; image execution remains an explicit controlled job.
.PHONY: runtime-test runtime-lint
runtime-test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m pytest -p no:cacheprovider tests/test_runtime_*.py tests/test_packaged_runtime.py
runtime-lint:
	RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 $(PYTHON) -m ruff check src/health_buddy/runtime_*.py src/health_buddy/packaged_runtime.py scripts/package_runtime.py scripts/runtime_entrypoint.py packaging/*.py tests/test_runtime_*.py tests/test_packaged_runtime.py
