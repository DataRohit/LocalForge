# 16: Test layout and parallel execution

**What to build:** a test suite structure that mirrors the application, runs in parallel by default, and cannot
hang. A developer can run the whole suite, one layer of it, or one module, and get a result quickly.

**Blocked by:** 13.

**Status:** done

- [x] Tests are organised into a unit tree and an integration tree, each mirroring the application package layout
      so a module's tests are findable from its path.
- [x] Unit tests touch no external service; integration tests declare the services they need.
- [x] The existing markers separate the two layers, and selecting one layer runs only that layer.
- [x] The default test command runs in parallel across available cores, and the suite is verified to pass in
      parallel, not only serially.
- [x] Shared fixtures live in the conventional place per layer, and every fixture that allocates an external
      resource namespaces it per worker so parallel workers cannot collide.
- [x] A global timeout fails any test that hangs, so a stuck network call surfaces as a failure rather than an
      apparently frozen run. The timeout is tuned so a normal integration test does not trip it.
- [x] A test requiring serial execution is marked as such and its docstring justifies why.
- [x] Database setup reuses the test database between runs where safe, and the reuse can be disabled with a flag.
- [x] Coverage remains enforced at the existing threshold with branch coverage, and the parallel run reports the
      same coverage as a serial run.
- [x] Test modules follow the documentation standard from ticket 15.
- [x] A deliberately failing test is added, observed to fail, and removed, proving the harness reports correctly.
