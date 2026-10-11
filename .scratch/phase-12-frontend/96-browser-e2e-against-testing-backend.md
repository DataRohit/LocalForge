# 96: Browser end-to-end against the testing backend

**What to build:** Playwright suites that drive the built SPA in real browsers against the `testing` backend over a
single origin.

**Blocked by:**

- [94: Accessibility and responsive audit](94-accessibility-and-responsive-audit.md)
- [95: Frontend unit and integration tests](95-frontend-unit-and-integration-tests.md)

**Governing sources:**

- [Frontend hosting and edge routing ADR](../../docs/adr/0027-frontend-hosting-and-edge-routing.md)
- [Full-stack pivot ADR](../../docs/adr/0024-full-stack-pivot.md)
- [Service inventory: testing stack](../../docs/platform/service-inventory.md)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** Locally Verified 2026-10-11

- [x] The suite runs host processes against `localforge-test` through a same-origin proxy; no frontend container
      joins `testing` and the headless set is unchanged.
- [x] Before any run, an isolation check proves the targets are the `testing` ports and credentials.
- [x] Journeys cover registration with a captured email, activation, sign-in, profile edit, password and username
      recovery, notifications over the socket, and sign-out.
- [x] CSRF, expired-session, throttled, and offline behavior are exercised in a real browser.
- [x] The suite runs on Chromium, Firefox, and WebKit as the browser support table specifies, with WebKit limited to
      the origins it supports; the browsers are fetched once by the pinned Playwright.
- [x] Runs are parallel with timeouts, and leave no residue in the test services.
- [x] A bounded log window for every affected `testing` container is inspected and clean.
- [x] Status recorded as Locally Verified; `development` stays unverified.

## Verification record, 2026-10-11

The implementation was exercised while verifying the reopened Phase 11 contract correction. The renewed
autopilot authorization and delegated corrected-freeze acceptance are recorded in the Phase 11 handover;
the corrected gate is closed before further Phase 12 implementation.

- `make frontend-check`: 1,087 tests pass, with 100% statements, branches, functions, and lines; formatting, lint,
  types, generated-contract drift, documentation, build, and bundle checks pass.
- `make frontend-e2e`: 108 cases pass with four workers, zero skips and retries, covering the audit project and
  Chromium, Firefox, and WebKit journeys against the isolated testing backend. The final window is
  `2026-10-11T16:32:30.9775932Z` to `2026-10-11T16:34:01.2803651Z`.
- Isolation passed before preparation. The runner checked logs from all six headless testing containers, temporary
  Mailpit, and the host backend, with no unexplained records. A separate preview-output review found only the exact
  WebKit client-reset records documented in the service inventory, with no other warning or error.
- Cleanup proved that the throwaway database and cache keys were gone, Mailpit was removed, and channel-layer keys
  returned to baseline. Testing health and convention/ownership audits passed after the run.
- The backend host and rebuilt-container gates both passed 2,297 core and 23 timing tests, at 100% branch coverage;
  the browser security audit passed. The full correction evidence is in `docs/handover/phase-11.md` Section 10.

Main-thread findings: the schema omitted existing CSRF `403` responses; status assertions omitted those responses;
the log gate ignored unreadable logs and omitted three containers; Windows shell spawning and conflicting color
flags emitted warnings; multi-tab cleanup raced session revocation. All are remediated with focused regression or
real-browser proof. Preview reset records are retained and narrowly explained, not hidden. No development resource
was inspected or changed during this verification, and no push was made.

Closing inline review covered the backend response declarations and observed-status tests, generated contract
artifacts, UI regressions, browser journeys, isolation and resource cleanup, TLS, mail capture, command spawning,
and log classification. One remaining host-server log read swallowed filesystem failures; the entry point now
propagates them to the orchestration's failure gate. No warning is suppressed. The user explicitly requested
one commit per ticket and no push; this ticket's implementation and its necessary backend contract correction
are committed together before Ticket 97 starts.

The fresh closing run exposed one WebKit offline-recovery timeout (107 passed, one failed). A ten-repeat
WebKit selection passed all 20 cases but the strict log gate rejected one interrupted bootstrap refresh
(`request stream failed`, `500`). The offline journey now waits for initial network quiescence before
cutting connectivity and for the browser's online signal before resubmitting. The same ten-repeat
selection then passed all 20 cases with clean bounded backend logs and no residue. No backend warning
allowlist was expanded. `make frontend-e2e ARGS="--project=journeys-webkit --grep=losing --repeat-each=10"`
is the reproduction and regression command; the root target now forwards these supported runner arguments.

Final closing rerun: `make frontend-check` passed 1,087 tests with 100% coverage and every static/build gate.
`make frontend-e2e` passed all 108 cases, zero unexpected, skipped, or flaky cases, in 71.58 seconds;
the browser window began `2026-10-11T16:57:30.248Z`. Backend log and container windows were clean except
documented negative-path records; cleanup returned every resource to baseline. Testing health and naming
audits passed, as did all three freeze-manifest tests. Main-thread standards and specification review has
no unresolved findings. Development runtime truth remains unverified at this ticket boundary.
