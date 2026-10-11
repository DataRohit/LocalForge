# 95: Frontend unit and integration tests

**What to build:** The unit and integration suite with enforced coverage thresholds and parallel execution.

**Blocked by:**

- [91: Account recovery and credential changes](91-account-recovery-and-credential-changes.md)
- [92: Profile management](92-profile-management.md)
- [93: Notifications client](93-notifications-client.md)

**Governing sources:**

- [Frontend standard](../../docs/platform/frontend-standard.md) (quality gate)
- [Frontend toolchain ADR](../../docs/adr/0028-frontend-toolchain.md)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** done (Locally Verified)

- [x] Every feature has unit and integration tests against a mocked backend whose handlers are typed from the committed
      schema.
- [x] Coverage is 100% for statements, branches, functions, and lines over `src/`, enforced in the gate and never lowered
      to pass; `console.error` and `console.warn` fail a test.
- [x] Every test carries a timeout, and the suite passes under the parallel runner.
- [x] The import-direction, single-network-module, and no-remote-URL tests run in the suite, as do the tests of the
      tooling in `scripts/`.
- [x] Status recorded as Locally Verified.

**Verification (2026-10-11, no runtime change):**

- `make frontend-check` passes: 41 test files, 982 tests, coverage 100% for statements (672), branches (360),
  functions (190), and lines (666); all other checks clean. The whole suite passed three consecutive runs under the
  parallel runner. The initial bundle is 136,695 of 200,000 gzip bytes of JavaScript.
- Every feature is tested against the mock backend: authentication (sign-in, registration, activation, resend),
  recovery (both request screens and both reset screens), credentials (password and username change), profile
  management (view, edit, delete), the notification feed, the shell, the session, and the API client, each with its
  loading, empty, error, and offline states where the screen has them.
- The mock backend mirrors the schema in three ways. Its handlers take method and path from the generated operation
  table and refuse a status the document does not list; success and error bodies are typed from the generated
  schema; and `src/api/contract.test.ts` replays all 183 response examples that the OpenAPI document gives for the
  operations the interface calls, through the real client. Each error example must parse into an `ApiError` with
  the example's code, details, and request identifier, each success must come back unchanged, and the profile
  examples must pass the profile parser on all three routes that return one. The examples are generated into
  `src/api/generated/examples.ts` and the drift check fails if they differ from the document. A second test requires
  an example for every documented status that has a body, so the replay is total.
- Policy, asserted from the real configuration (`scripts/test-policy.test.ts`): every test and hook has a timeout of
  ten seconds; test files run in parallel; coverage measures only `src/` at 100% on all four measures; and the only
  exclusions are the bootstrap, the generated API files, tests, and declaration files.
- Console policy, observed in a real run: the same test spawns Vitest on three fixtures with the shared setup. A test
  that calls `console.error` fails with that message, a test that calls `console.warn` fails with that message, and
  a test that logs both and then takes the calls passes.
- The import-direction, single-network-module, build-variable, and absolute-URL tests run in the suite
  (`scripts/check-architecture.test.ts`), as does the bundle check's remote-URL logic and the supply-chain audit
  against the real project files.
- No runtime, Compose, or service change; `development` was not touched.

**Findings and dispositions:**

- The first replay mixed up its own handlers: it sent a body on `GET` routes, which `fetch` forbids, and a handler
  for the refresh route shadowed the example for that same route. The operation table now records whether an
  operation takes a body, and the handlers are registered in the right order.
- The generated artifacts grew by an examples module (54 kB, test-only, never imported by the application) and a
  `hasBody` flag per operation; both are covered by the generator's tests and the drift check.
- A fixture run writes its report to a file in Vitest 5, not to standard output; the test now reads a temporary file
  and removes it, and nothing is left in the working tree.
- Existing suites already met several criteria (timeouts, parallel execution, console failure); this ticket adds the
  evidence for them instead of assuming them.

Disposition: complete.
