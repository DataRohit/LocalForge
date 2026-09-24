# 58: Account lifecycle SOLID audit

**What to build:** evidence-led SOLID review and focused repair of registration, activation, profile, password,
username, transactional email, timing, and account-maintenance workflows.

**Blocked by:**

- [57](57-account-authentication-solid-audit.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Ticket index and phase gates](../README.md)
- [SOLID audit plan](../../docs/architecture/solid-audit-plan.md)
- [API error contract](../../docs/adr/0018-api-error-contract.md)
- [First-party account endpoint decision](../../docs/adr/0017-first-party-account-endpoints.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Documentation standard](../../docs/platform/documentation-standard.md)

**Status:** ready-for-agent

- [ ] Each account workflow owns one coherent policy while shared token, delivery, timing, and transaction behaviour
      lives behind reusable interfaces only when multiple workflows truly share it.
- [ ] Adding or changing one supported account workflow does not require repeated edits across unrelated workflows
      or transport adapters.
- [ ] Activation, password, username, and profile adapters preserve shared interface invariants and do not
      strengthen preconditions or weaken postconditions.
- [ ] Views, tasks, email delivery, token stores, and timing protections depend only on the interfaces they use.
- [ ] REST status codes, error envelopes, response examples, enumeration resistance, statistical timing bounds,
      transaction boundaries, email content, and Celery behaviour remain exact.
- [ ] Confirmed findings receive regression tests through public workflow interfaces and real SMTP or worker seams
      where those seams are part of the behaviour.
- [ ] Both complete test modes pass every account lifecycle case without warning, skip, leaked state, or
      serialization failure.
- [ ] The findings ledger records every reviewed module, finding, fix, and verification result.
