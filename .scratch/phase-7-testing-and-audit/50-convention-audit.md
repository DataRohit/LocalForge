# 50: Convention audit

**What to build:** an automated check that the running stack matches the naming, volume, and network rules, so a
drifted name is caught mechanically rather than by reading a Compose file.

**Blocked by:**

- [49](49-dual-mode-test-execution.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Ticket index and phase gates](../README.md)
- [Build plan](../../docs/build/plan.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Documentation standard](../../docs/platform/documentation-standard.md)

**Status:** done

- [x] The audit compares live Docker objects against the registry and reports any container that is missing,
      unexpected, or misnamed.
- [x] Every audit command filters by the Compose project label. This machine is shared and carries unrelated
      containers, volumes, and networks; an unfiltered check fails for the wrong reason.
- [x] Every container name matches the documented pattern.
- [x] No anonymous volume belongs to either project, and every named volume traces to a registry entry.
- [x] No default network exists for either project, and every network carries the zone naming scheme.
- [x] Every internal network is proven internal: a container on one cannot resolve an external name.
- [x] Every published host port matches the inventory, and no service publishes a port the inventory does not
      list.
- [x] Bind mounts are read-only except where documented, and the only writable exceptions are justified.
- [x] The audit runs for both environments and exits non-zero on any violation, printing the object and the rule.
- [x] Violations found are fixed and the audit re-run in full, not just the failing check.
- [x] The audit also proves every required service is running or intentionally stopped, health status matches the
      inventory, and a bounded idle and exercise log window has no unexplained warning-or-higher record.
