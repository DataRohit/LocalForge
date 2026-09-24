# 55: Support script SOLID audit

**What to build:** evidence-led SOLID review and focused repair of project-owned audit, prerequisite, secret,
storage, broker, readiness, worker-health, and documentation scripts.

**Blocked by:**

- [53](53-solid-audit-baseline-and-module-inventory.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Ticket index and phase gates](../README.md)
- [SOLID audit plan](../../docs/architecture/solid-audit-plan.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Security audit](../../docs/security/security-audit.md)
- [Documentation standard](../../docs/platform/documentation-standard.md)

**Status:** ready-for-agent

- [ ] Each script has one coherent policy or lifecycle responsibility, with parsing, environment access, external
      commands, and reporting separated only where evidence shows independent change.
- [ ] Shared policy is consolidated behind a deep interface when duplication causes changes to spread across
      scripts; unrelated one-off logic remains local.
- [ ] Security, naming, and prerequisite checks depend on injectable or substitutable data sources where tests and
      runtime already use different adapters.
- [ ] Exit codes, redaction, machine-readable output, operator diagnostics, offline behaviour, and exact audit
      coverage remain unchanged unless their governing source changes first.
- [ ] Framework hooks and adapter implementations preserve their accepted input domain, errors, and return
      contracts under substitution.
- [ ] Confirmed findings receive regression tests through each script's supported command or callable interface.
- [ ] The complete script test layer passes under parallel execution with zero warnings and zero skips.
- [ ] The findings ledger records every reviewed script, finding, fix, and verification result.
*** End of File
