# 61: Cross-module dependency audit

**What to build:** the final package-level SOLID review that proves dependency direction, composition roots, shared
interfaces, and adapters remain coherent after every area audit.

**Blocked by:**

- [60](60-test-architecture-solid-audit.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Ticket index and phase gates](../README.md)
- [SOLID audit plan](../../docs/architecture/solid-audit-plan.md)
- [Build plan](../../docs/build/plan.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Documentation standard](../../docs/platform/documentation-standard.md)

**Status:** ready-for-agent

- [ ] The findings ledger contains a current dependency map for application packages, operator modules, test
      support, framework adapters, and composition roots.
- [ ] High-level account, notification, health, security, and orchestration policies do not depend directly on
      unrelated concrete transports, stores, processes, or framework setup.
- [ ] No import cycle, service locator, broad shared context, duplicate dispatch policy, or cross-package private
      import bypasses an intended interface.
- [ ] Shared interfaces remain deep and cohesive; duplicate abstractions, pass-through wrappers, and speculative
      extension points are removed.
- [ ] Objective, stable dependency rules receive machine enforcement when repository tooling can express them
      without heuristic SOLID scores or source-size thresholds.
- [ ] Any new architecture check has failure-focused unit tests, actionable diagnostics, and a documented command
      in the existing quality gate.
- [ ] The complete module inventory has no missing, duplicate, or unresolved required finding.
- [ ] Targeted and full quality gates pass after every cross-module correction.
*** End of File
