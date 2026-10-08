# 94: Accessibility and responsive audit

**What to build:** A whole-application audit of semantics, keyboard use, focus, contrast, and responsive layout,
with fixes.

**Blocked by:**

- [91: Account recovery and credential changes](91-account-recovery-and-credential-changes.md)
- [92: Profile management](92-profile-management.md)
- [93: Notifications client](93-notifications-client.md)

**Governing sources:**

- [Frontend standard: Tailwind and accessibility](../../docs/platform/frontend-standard.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] Every screen reports zero axe violations in its loading, empty, error, and populated states.
- [ ] Every flow completes by keyboard alone with a visible focus indicator and a logical order.
- [ ] Layouts hold at phone, tablet, and desktop widths without horizontal scrolling or clipped content.
- [ ] Focus management is tested for route changes and dialogs.
- [ ] Findings and their dispositions are recorded.
- [ ] Status recorded as Locally Verified.
