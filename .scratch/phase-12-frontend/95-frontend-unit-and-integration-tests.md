# 95: Frontend unit and integration tests

**What to build:** The unit and integration suite with enforced coverage thresholds and parallel execution.

**Blocked by:**

- [91: Account recovery and credential changes](91-account-recovery-and-credential-changes.md)
- [92: Profile management](92-profile-management.md)
- [93: Notifications client](93-notifications-client.md)

**Governing sources:**

- [Frontend standard](../../docs/platform/frontend-standard.md) (quality gate)
- [Frontend toolchain ADR](../../docs/adr/0028-frontend-toolchain.md)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] Every feature has unit and integration tests against a mocked backend whose handlers are typed from the committed
      schema.
- [ ] Coverage is 100% for statements, branches, functions, and lines over `src/`, enforced in the gate and never lowered
      to pass; `console.error` and `console.warn` fail a test.
- [ ] Every test carries a timeout, and the suite passes under the parallel runner.
- [ ] The import-direction, single-network-module, and no-remote-URL tests run in the suite, as do the tests of the
      tooling in `scripts/`.
- [ ] Status recorded as Locally Verified.
