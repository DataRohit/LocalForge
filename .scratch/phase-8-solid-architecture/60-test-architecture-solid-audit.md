# 60: Test architecture SOLID audit

**What to build:** evidence-led SOLID review and focused repair of shared test fixtures, factories, communicators,
runtime probes, helpers, and tests as clients of production interfaces.

**Blocked by:**

- [54](54-operator-orchestration-solid-audit.md)
- [55](55-support-script-solid-audit.md)
- [56](56-django-runtime-solid-audit.md)
- [58](58-account-lifecycle-solid-audit.md)
- [59](59-notification-delivery-solid-audit.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Ticket index and phase gates](../README.md)
- [SOLID audit plan](../../docs/architecture/solid-audit-plan.md)
- [Documentation standard](../../docs/platform/documentation-standard.md)
- [Service inventory](../../docs/platform/service-inventory.md)

**Status:** ready-for-agent

- [ ] Shared fixtures and helpers expose cohesive interfaces; tests do not receive unrelated services, state, or
      configuration through broad fixture objects.
- [ ] Factories, communicators, runtime probes, fake adapters, and test hooks satisfy the same caller-visible
      contracts as their production counterparts.
- [ ] Tests cross production interfaces rather than private implementation seams, except for narrow unit evidence
      that cannot be expressed through the supported interface.
- [ ] Environment, clock, randomness, process, network, and storage dependencies are supplied through explicit
      seams where isolation or host/container substitution requires them.
- [ ] Global hook state, worker state, service markers, serial grouping, and resource ownership remain deterministic
      under collection, subprocess tests, and parallel execution.
- [ ] One-off test setup remains local when extracting it would create a shallow helper with no reuse or policy.
- [ ] Unit, focused integration, complete host, and complete container suites retain exact counts, 100% branch
      coverage, zero warnings, zero skips, timeouts, and cleanup.
- [ ] The findings ledger records every reviewed test-support module, finding, fix, and verification result.
*** End of File
