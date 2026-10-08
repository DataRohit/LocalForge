# 79: Test-isolation hardening

**What to build:** Make it impossible for `testing` runs, host tooling, or the quality gate to read or reach
`development` credentials, containers, or volumes, so the live environment stays untouched by routine work.

**Blocked by:** None. Phase 10 is done.

**Governing sources:**

- [Full-stack pivot ADR](../../docs/adr/0024-full-stack-pivot.md)
- [Conventions: secrets and env files](../../docs/platform/conventions.md)
- [Service inventory: testing stack](../../docs/platform/service-inventory.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] `django-test-dt5qx` no longer bind-mounts `.env.development`; the credential-distinctness assertions that
      needed it read only `.env.testing`, and the development-file checks move to unit tests over fixture data.
- [ ] No host command selects `config.settings.development` by default: Django entry points fail closed or default
      to `testing` unless the settings module is set explicitly.
- [ ] `make check` and every Make target that is not named `development-*` or an explicit operator task runs without
      reading `.env.development` or addressing `localforge-dev`.
- [ ] The Make targets `dev`, `migrate`, `migrations`, `superuser`, and `shell` require an explicit environment
      selection and refuse to default to development.
- [ ] A unit test proves each Compose command built for `testing` carries the `localforge-test` project and
      `.env.testing`, and that no testing code path opens `.env.development`.
- [ ] The convention audit mount allowlist, service inventory, and conventions document match the new mounts.
- [ ] `testing` gate is green in container and host mode, and the new behavior is exercised against `testing` only.
- [ ] Status recorded as Locally Verified; `development` runtime stays an unverified gate.
