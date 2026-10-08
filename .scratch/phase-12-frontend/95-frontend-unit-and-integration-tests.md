# 95: Frontend unit and integration tests

**What to build:** The unit and integration suite with enforced coverage thresholds and parallel execution.

**Blocked by:**

- [91: Account recovery and credential changes](91-account-recovery-and-credential-changes.md)
- [92: Profile management](92-profile-management.md)
- [93: Notifications client](93-notifications-client.md)

**Governing sources:**

- [Frontend standard: quality gate](../../docs/platform/frontend-standard.md)
- [Frontend toolchain ADR](../../docs/adr/0028-frontend-toolchain.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] Every feature has unit and integration tests against a mocked backend that mirrors the committed schema.
- [ ] Coverage is 100% for statements, branches, functions, and lines, enforced in the gate and never lowered to
      pass; `console.error` and `console.warn` fail a test.
- [ ] Every test carries a timeout, and the suite passes under the parallel runner.
- [ ] The import-direction and no-remote-URL tests run in the suite.
- [ ] Status recorded as Locally Verified.
