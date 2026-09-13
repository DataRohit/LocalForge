# 36: Exhaustive OpenAPI documentation

**What to build:** a schema that documents every route, every status code each one can return, and at least one
response example per status code — including the codes produced by middleware and content negotiation. The schema
is verified against real behaviour, not written from intent.

**Blocked by:** 28, 31, 32, 33, 34, 35.

**Status:** ready-for-agent

- [ ] Every route in the API surface appears in the schema with a summary, a description, its request body, and its
      responses.
- [ ] Every status code a route can return is documented, including those from authentication, permissions,
      throttling, content negotiation, CSRF, routing, and unhandled errors.
- [ ] Every documented status code carries at least one response example bound to that code, so a reader sees the
      actual body rather than only a schema reference.
- [ ] Validation failures document the per-field error shape, not just a generic error object.
- [ ] Both authentication schemes are declared as security schemes, and each route states which it accepts.
- [ ] The enumeration-resistant responses are documented as such, so an integrator understands that an accepted
      response does not mean the account existed.
- [ ] Operation identifiers are stable and human-readable, so a generated client has usable method names.
- [ ] The schema generates with no warnings. Any suppressed warning is justified in writing.
- [ ] A test generates the schema and fails the build on a warning or on a route missing from it.
- [ ] A contract test asserts that each documented status code is actually reachable, and that no route returns an
      undocumented status code — the schema and the test suite check each other.
- [ ] The schema is committed as an artifact so a change to the public contract is visible in review.
- [ ] The WebSocket message and close-code contract is documented alongside, so an integrator reads one page.
