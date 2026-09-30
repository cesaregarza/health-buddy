PYTHON ?= python3
LEGACY_TYPED = src scripts/next_workout.py scripts/publish_training_day.py scripts/prescription_progression.py

.PHONY: contracts test dashboard-test lint typecheck package preview
contracts:
	$(PYTHON) scripts/validate_contracts.py
test:
	$(PYTHON) -m pytest tests
dashboard-test:
	cd health-runner/dashboard && HEALTH_TIMEZONE=America/Chicago $(PYTHON) -m unittest discover -s tests -v
lint:
	RAYON_NUM_THREADS=1 RUFF_NUM_THREADS=1 $(PYTHON) -m ruff check $(LEGACY_TYPED)
typecheck:
	$(PYTHON) -m mypy
package:
	$(PYTHON) -m build
preview:
	$(PYTHON) health-runner/dashboard/scripts/preview.py --output health-runner/dashboard/design/preview/index.html
