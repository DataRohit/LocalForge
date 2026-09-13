# 47: Unit test suite

**What to build:** fast, isolated coverage of every unit of project logic — models, serializers, permissions,
routers, validators, tasks, helpers — running with no external service and finishing in seconds.

**Blocked by:** 16, and the application work in phases 4 through 6.

**Status:** ready-for-agent

- [ ] The unit tree mirrors the application package layout, one test module per source module.
- [ ] No unit test opens a socket, touches the broker, the cache, object storage, or the mail service; external
      collaborators are substituted.
- [ ] Model tests cover constraints, defaults, normalisation, string representations, and every custom manager
      method.
- [ ] Serializer tests cover valid input, every validation failure, and the exact error shape returned.
- [ ] Permission and throttle classes are tested directly for allow and deny.
- [ ] The database router is tested for read, write, relation, and migration decisions.
- [ ] Task functions are tested directly, separately from their execution by a worker.
- [ ] Every branch of custom validation and error handling is exercised, since the coverage gate is branch-based.
- [ ] Tests use factories rather than fixtures copied between modules, and factories produce valid objects by
      default.
- [ ] Frozen time is used wherever behaviour depends on the clock, so expiry tests are deterministic.
- [ ] The whole unit layer passes under the parallel runner and completes well inside the configured timeout.
- [ ] Every test module and test function follows the documentation standard from ticket 15.
