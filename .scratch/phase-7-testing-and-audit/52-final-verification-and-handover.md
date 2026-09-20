# 52: Final verification and handover

**What to build:** the closing pass that proves the whole platform works from nothing, and leaves the documentation
matching what was actually built.

**Blocked by:**

- [50](50-convention-audit.md)
- [51](51-security-and-reliability-audit.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Ticket index and phase gates](../README.md)
- [Build plan](../../docs/build/plan.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Documentation standard](../../docs/platform/documentation-standard.md)

**Status:** ready-for-agent

- [ ] A clean-clone rehearsal is performed: from a fresh checkout, following only the written instructions,
      the development stack comes up healthy and the test suite passes in both modes. Every step that required
      undocumented knowledge is written down.
- [ ] Every service is healthy, correctly named, and reachable on its documented port.
- [ ] Every documented route returns every documented status code, and the published schema matches observed
      behaviour rather than intent.
- [ ] The WebSocket contract matches observed behaviour, including every close code.
- [ ] Both test modes pass at the enforced coverage threshold, in parallel, within the recorded time budget.
- [ ] The full quality gate is green.
- [ ] Any pinned version that moved during the build is updated in the documentation with its new release date, or
      deliberately held with a reason.
- [ ] Every release-lag gate is recorded as resolved in its decision record, naming which fallback was taken.
- [ ] Documentation is reconciled with reality: any place where the built system differs from what
      `docs/` describes is corrected in `docs/`, not left as a known discrepancy.
- [ ] The handover report lists what was built, what was deferred, every accepted risk, and the review dates that
      fall due, including the message broker's community support window.
- [ ] Work stops here. No application feature beyond the documented surface is started.
