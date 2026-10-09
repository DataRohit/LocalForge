# 79: Test-isolation hardening

**What to build:** Make it impossible for `testing` runs, host tooling, or the quality gate to read or reach
`development` credentials, containers, or volumes, so the live environment stays untouched by routine work.

**Blocked by:** None. Phase 10 is done.

**Governing sources:**

- [Full-stack pivot ADR](../../docs/adr/0024-full-stack-pivot.md)
- [Conventions: secrets and env files](../../docs/platform/conventions.md)
- [Service inventory: testing stack](../../docs/platform/service-inventory.md)
- [Ticket index](../README.md)

**Status:** done (Locally Verified)

- [x] `django-test-dt5qx` no longer bind-mounts `.env.development`; the credential-distinctness assertions that
      needed it read only `.env.testing`, and the development-file checks move to unit tests over fixture data.
- [x] No host command selects `config.settings.development` by default: Django entry points fail closed or default
      to `testing` unless the settings module is set explicitly.
- [x] `make check` and every Make target that is not named `development-*` or an explicit operator task runs without
      reading `.env.development` or addressing `localforge-dev`.
- [x] The Make targets `dev`, `migrate`, `migrations`, `superuser`, and `shell` require an explicit environment
      selection and refuse to default to development.
- [x] A unit test proves each Compose command built for `testing` carries the `localforge-test` project and
      `.env.testing`, and that no testing code path opens `.env.development`.
- [x] The convention audit mount allowlist, service inventory, and conventions document match the new mounts.
- [x] `testing` gate is green in container and host mode, and the new behavior is exercised against `testing` only.
- [x] Status recorded as Locally Verified; `development` runtime stays an unverified gate.

**Verification (2026-10-09, `testing` only):** `make check` on the host — Django checks under `config.settings.testing`,
OpenAPI contract, Ruff, docstring checker, mypy, ty, architecture audit, then the complete suite in parallel with
100.00% branch coverage and a clean bounded log audit. Container mode through `make testing-verify`: status 0, 100.00%
coverage, ownership and container-set audits PASS. The suite-wide guard in `backend/tests/conftest.py` fails any test that
opens the live `.env.development`; `backend/tests/unit/scripts/test_environment_isolation.py` proves every testing
command carries `localforge-test` and `.env.testing`. Findings: three baseline `make check` failures that predate this
phase were repaired in the same change (documented browser variables missing from `.env.example`, Node and npm rows 15
and 16 missing from the preflight, the `django-check` task selecting development settings); one coverage divergence
between container and host (`seed_storage` guard test read the real development file) was fixed by driving it from the
process environment. Disposition: all remediated. `development` runtime stays an unverified gate. The Make targets
that may address development are the named operator tasks in `test_only_declared_operator_targets_may_address_development`.
