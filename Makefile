.DEFAULT_GOAL := help

REPOSITORY_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))/
BACKEND_PROJECT := $(REPOSITORY_ROOT)backend
UV_RUN := uv run --project $(BACKEND_PROJECT) --directory $(BACKEND_PROJECT)
POE := $(UV_RUN) poe
FRONTEND_PROJECT := $(REPOSITORY_ROOT)frontend
PNPM := pnpm --dir $(FRONTEND_PROJECT)

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

ENVIRONMENT_CHOICES := development testing
ENVIRONMENT_TARGETS := dev migrate migrations superuser shell

FRONTEND_TARGETS := \
  frontend-install frontend-format frontend-lint frontend-types frontend-docs-standard \
  frontend-test frontend-build frontend-check frontend-api-generate frontend-e2e \
  frontend-image frontend-image-check frontend-audit frontend-audit-refresh frontend-advisory-evidence

.PHONY: help sync preflight pre-commit-install pre-commit require-environment $(POE_TASKS) $(FRONTEND_TARGETS)

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

$(ENVIRONMENT_TARGETS): require-environment
$(ENVIRONMENT_TARGETS): export DJANGO_SETTINGS_MODULE = config.settings.$(ENVIRONMENT)

require-environment:
ifneq ($(origin ENVIRONMENT),command line)
	$(error ENVIRONMENT=development or ENVIRONMENT=testing is required on the command line)
endif
ifeq ($(filter $(ENVIRONMENT),$(ENVIRONMENT_CHOICES)),)
	$(error ENVIRONMENT must be one of: $(ENVIRONMENT_CHOICES))
endif

$(POE_TASKS):
	$(POE) $@ $(ARGS)

frontend-install:
	$(PNPM) install --frozen-lockfile

frontend-format:
	$(PNPM) run format

frontend-lint:
	$(PNPM) run lint

frontend-types:
	$(PNPM) run types

frontend-docs-standard:
	$(PNPM) run docs

frontend-test:
	$(PNPM) run test

frontend-build:
	$(PNPM) run build

frontend-check:
	$(MAKE) frontend-format
	$(MAKE) frontend-lint
	$(MAKE) frontend-types
	$(MAKE) frontend-docs-standard
	$(MAKE) frontend-test
	$(MAKE) frontend-build

frontend-api-generate:
	$(PNPM) run api:generate

frontend-e2e:
	$(PNPM) run e2e $(ARGS)

frontend-image:
	docker build --file docker/frontend/Dockerfile --tag localforge/frontend:0.1.0 $(REPOSITORY_ROOT)

frontend-image-check:
	$(PNPM) exec node scripts/check-image.ts

frontend-audit:
	$(PNPM) run audit:supply

frontend-audit-refresh:
	$(PNPM) run audit:supply --refresh

frontend-advisory-evidence:
	@$(PNPM) audit --json
