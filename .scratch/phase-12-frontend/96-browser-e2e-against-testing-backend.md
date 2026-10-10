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

**Status:** pending

- [ ] The suite runs host processes against `localforge-test` through a same-origin proxy; no frontend container
      joins `testing` and the headless set is unchanged.
- [ ] Before any run, an isolation check proves the targets are the `testing` ports and credentials.
- [ ] Journeys cover registration with a captured email, activation, sign-in, profile edit, password and username
      recovery, notifications over the socket, and sign-out.
- [ ] CSRF, expired-session, throttled, and offline behavior are exercised in a real browser.
- [ ] The suite runs on Chromium, Firefox, and WebKit as the browser support table specifies, with WebKit limited to
      the origins it supports; the browsers are fetched once by the pinned Playwright.
- [ ] Runs are parallel with timeouts, and leave no residue in the test services.
- [ ] A bounded log window for every affected `testing` container is inspected and clean.
- [ ] Status recorded as Locally Verified; `development` stays unverified.
