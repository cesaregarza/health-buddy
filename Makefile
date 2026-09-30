PYTHON ?= python3
LEGACY_TYPED = src scripts/next_workout.py scripts/publish_training_day.py scripts/prescription_progression.py
REFERENCE_TESTS = src/health_buddy/reference_extensions/local.weekly-mass/tests src/health_buddy/reference_extensions/local.water-import/tests
EXTENSION_TESTS = tests/test_extension_*.py
EXTENSION_LINT = $(EXTENSION_TESTS) tests/extension_fixtures.py health-runner/dashboard/scripts/check_extensions.py
MCP_LINT = tests/test_mcp_*.py tests/test_workspace_discovery.py tests/mcp_wire_fixtures.py tests/mcp_process_runner.py

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
	RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 $(PYTHON) -m ruff check $(LEGACY_TYPED) $(EXTENSION_LINT) $(MCP_LINT)
typecheck:
	$(PYTHON) -m mypy
package:
	$(PYTHON) -m build
preview:
	$(PYTHON) health-runner/dashboard/scripts/preview.py --output health-runner/dashboard/design/preview/index.html
