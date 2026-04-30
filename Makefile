# Runner Dashboard — developer Makefile
#
# Targets here are thin wrappers around the existing dev-surface scripts.
# All heavy lifting (venv creation, pip install) is delegated to
# start-dashboard.sh, which never touches system site-packages.

.PHONY: dev test seed help

help:
	@echo "Runner Dashboard dev targets:"
	@echo "  make dev   - start the dashboard with hot reload (uvicorn --reload)"
	@echo "  make test  - run the pytest suite"
	@echo "  make seed  - seed local fixtures (TODO: no seed script yet)"

dev:
	./start-dashboard.sh --reload

test:
	pytest tests/ -q --tb=short

seed:
	@echo "TODO(#416): no seed script exists yet."
	@echo "When fixtures land (see start-dashboard.sh --mock), wire them here."
