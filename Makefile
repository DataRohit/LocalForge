.DEFAULT_GOAL := help

REPOSITORY_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))/
BACKEND_PROJECT := $(REPOSITORY_ROOT)backend
UV_RUN := uv run --project $(BACKEND_PROJECT) --directory $(BACKEND_PROJECT)
POE := $(UV_RUN) poe

POE_TASKS := \
  dev setup environments-setup docker-audit docker-clean-check \
  convention-audit convention-audit-development convention-audit-testing \
  security-audit security-audit-static security-audit-runtime \
  secrets-generate secrets-decrypt developer-access-export \
  development-build development-up development-down development-rebuild development-reset \
  development-status development-health development-logs \
  testing-build testing-up testing-down testing-rebuild testing-reset testing-status \
  testing-health testing-logs testing-test-container testing-test-host testing-test-both \
  testing-registration-timing-stability testing-integration-audit testing-verify \
  migrate migrations superuser shell django-check openapi-generate openapi-check docs-standard \
  lint format format-check types-mypy types-ty typecheck architecture-audit \
  test test-stages test-serial test-serial-stages test-fresh test-fresh-stages test-unit \
  test-integration test-integration-stages test-parallel test-core test-core-fresh \
  test-security-timing check

.PHONY: help sync preflight pre-commit-install pre-commit $(POE_TASKS)

help:
	@$(POE) help

sync:
	uv sync --project $(BACKEND_PROJECT) --all-groups --frozen

preflight:
	$(UV_RUN) python scripts/preflight.py

pre-commit-install:
	$(UV_RUN) pre-commit install

pre-commit:
	$(UV_RUN) pre-commit run --all-files

$(POE_TASKS):
	$(POE) $@ $(ARGS)
