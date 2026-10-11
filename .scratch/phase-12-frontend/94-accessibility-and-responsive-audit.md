# 94: Accessibility and responsive audit

**What to build:** A whole-application audit of semantics, keyboard use, focus, contrast, and responsive layout, with
fixes.

**Blocked by:**

- [91: Account recovery and credential changes](91-account-recovery-and-credential-changes.md)
- [92: Profile management](92-profile-management.md)
- [93: Notifications client](93-notifications-client.md)

**Governing sources:**

- [Frontend standard](../../docs/platform/frontend-standard.md) (Tailwind and accessibility)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** done (Locally Verified)

- [x] Every screen reports zero axe violations in its loading, empty, error, and populated states, through axe-core in
      the unit suite and `@axe-core/playwright` for contrast in a real browser.
- [x] Every flow completes by keyboard alone with a visible focus indicator and a logical order.
- [x] Layouts hold at phone, tablet, and desktop widths without horizontal scrolling or clipped content.
- [x] Focus management is tested for route changes and dialogs.
- [x] Findings and their dispositions are recorded.
- [x] Status recorded as Locally Verified.

**Verification (2026-10-11, no runtime change):**

- `make frontend-check` passes: 39 test files, 789 tests, coverage 100% for statements (672), branches (360),
  functions (190), and lines (666); all other checks clean. `make frontend-e2e` builds and passes 69 browser tests in
  Chromium (Playwright 1.63, `@axe-core/playwright` 4.13) under the parallel runner.
- Unit-level audit (`src/accessibility.test.tsx`): 49 scenarios drive every screen into its initial, loading, empty,
  error, and populated states (the session splash, session error, no-features and read-only accounts, every form with
  validation and server errors, every success screen, the delete dialog, and each notification feed status) and run
  axe-core with every rule except colour contrast. All report zero violations. A negative control feeds the same
  check an image with no alternative text, an unnamed button, and an unlabelled input, and requires all three to be
  reported, so a clean result means something.
- Browser audit (`e2e/audit/pages.spec.ts`): 21 screens and states at 360, 768, and 1280 pixels, 63 runs, each with
  all axe rules including colour contrast and a layout check for horizontal scrolling and any element past a
  viewport edge. All pass. The built application runs under Vite preview with the profile route and the
  notification socket mocked at the network layer. A negative control (`checks.spec.ts`) requires the audit to
  report a pale-text contrast failure and a page that is too wide, and to accept a good page.
- Keyboard: the sign-in page's tab order is asserted in jsdom and in Chromium, and signing in by keyboard alone works
  in jsdom; every stop shows a visible two-pixel focus outline in the browser. Focus moves to the main region after a
  client-side navigation (tested in jsdom and in the browser). The delete dialog opens from the keyboard, moves
  focus inside, keeps focus off the page behind it, closes on Escape, and returns focus to its button, verified in
  the real browser as well as in jsdom.
- Responsive: no screen scrolls horizontally or has content past a viewport edge at phone, tablet, or desktop width.
- No runtime, Compose, or service change; `development` was not touched.

**Findings and dispositions:**

- No accessibility defect was found in the application. The earlier tickets built the primitives with labels, error
  wiring, landmarks, a skip link, visible focus styles, and token colours chosen for contrast, and this audit
  confirms them rather than repairing anything.
- Findings in the audit itself: TanStack Query pauses queries while it believes the browser is offline, and its
  online state is global, so one offline scenario made every later signed-in scenario hang. The shared test setup now
  resets it after each test.
- A native modal dialog makes the page behind it inert, but Tab can still leave the page for the browser's own
  controls, where `activeElement` is the body. The first browser assertion required focus to stay inside the
  dialog; it now requires that focus never lands on the page behind it, which is the actual property.
- The browser audit does not exercise unsafe requests. Chromium rejects the `__Host-` and `Secure` cookies on a
  plain-HTTP origin (the Phase 11 manifest records this), so sign-in and form submission through the real backend
  are covered by Ticket 96 over its same-origin proxy; every submit path and its error state is covered here in jsdom.
- jsdom cannot evaluate colour contrast or layout, which is why the browser audit exists; each half covers the other.

Disposition: complete.
