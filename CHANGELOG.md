# Changelog

All notable changes to this project will be documented in this file.

The format follows Keep a Changelog principles, and released versions will use Semantic Versioning once a public API or
release lifecycle exists.

## Unreleased

### Added

- Repository governance and quality baseline: contributing, security, support, governance, and conduct policies, a
  Conventional Commits convention with a validating hook, and pre-commit gates.
- A containerized platform on Docker Compose with exactly two environments: `development` (24 services, including the
  frontend) and `testing` (a headless subset). It covers PostgreSQL 18 with a streaming replica and pgBackRest backups,
  two Valkey instances, RabbitMQ, SeaweedFS object storage, Mailpit, Traefik, Prometheus, Grafana, Loki, Alloy,
  cAdvisor, exporters, Flower, and pgAdmin, with registry-assigned names and project-scoped Docker, convention, and
  security audits.
- A Django 6 backend on Python 3.14 with a fixed, fully documented REST surface (health, schema, accounts, token and
  JWT authentication), an authenticated notification WebSocket over Channels, Celery workers and scheduler, and
  structured JSON logging.
- Browser cookie authentication with `HttpOnly` JWT cookies, double-submit CSRF, and a cookie WebSocket handshake
  (ADR 0025), and a frozen backend contract (Phase 11).
- A React 19 and Tailwind CSS 4 single-page application with sign-in, registration, activation, password and username
  recovery, account management, and live notifications, tested to 100% coverage with Playwright browser journeys and
  axe-core accessibility checks.
- A hardened static nginx image for the frontend, served on the same origin as the API through Traefik, with Django
  owning `/api/`, `/ws/`, `/health/`, `/admin/`, and `/static/` at higher priority (ADR 0027).
- A Cloudflare Tunnel public edge and Resend email for `development`, with a runbook for publishing on a custom
  domain.
- Strict quality gates: Ruff with all rules, mypy strict and ty, 100% branch coverage, no-comments docstring standard,
  supply-chain and image-vulnerability audits for Python and pnpm dependencies, and SOPS and age encrypted env files.
- A deploy and rollback runbook for the frontend, and a complete project README.

### Changed

- The frontend is adopted into the committed development definition; its Compose service, image build target, and
  registry entries replace the earlier inert candidate files.
- Integration tests that name a per-minute throttle rate now wait out the end of a clock minute before starting, so the
  fixed-window throttle cannot split a request pair; no limit was changed.

### Fixed

- A minute-boundary race that made the username-contract throttle test fail intermittently.
