# 84: Frontend contract gaps

**What to build:** Close the backend gaps the frontend capabilities need: backend-granted capabilities on the
profile, SPA-routed action links in account emails, and the documented notification scope.

**Blocked by:**

- [82: Cookie-authenticated REST](82-cookie-authenticated-rest.md)

**Governing sources:**

- [Phase 11 architecture: contract gaps](../../docs/architecture/phase-11-backend-freeze.md)
- [Phase 12 architecture: client route map](../../docs/architecture/phase-12-frontend.md)
- [Full-stack pivot ADR](../../docs/adr/0024-full-stack-pivot.md)
- [First-party account endpoints ADR](../../docs/adr/0017-first-party-account-endpoints.md)
- [OpenAPI document](../../docs/api/openapi-v1.yaml)
- [Ticket index](../README.md)

**Status:** done (Locally Verified)

- [x] `GET /api/v1/users/me/` gains a read-only `capabilities` array from the closed registry in the Phase 11
      architecture, held in full by an active authenticated account, documented with its JSON shape and an example.
- [x] Activation, password-reset, and username-reset emails link to `/activate`, `/reset-password`, and
      `/reset-username` on the configured site URL with `account` and `token` in the URL **fragment**, never the
      query string; the HTML and text templates and the link tests cover all three.
- [x] A test proves no emailed link places a credential in a path or query string.
- [x] The notification scope decision (live feed only, no history route) is restated in the contract documents.
- [x] A capability-to-endpoint matrix covers registration, activation, sign-in and sign-out, profile management,
      password and username flows, and notifications, with no capability left unmapped.
- [x] Tests cover the new fields and the rendered email links in `testing`.
- [x] Status recorded as Locally Verified.

**Verification (2026-10-09, `testing` only):** `capabilities` is a read-only closed registry on the profile
(unit and integration tests); the three emailed links target `/activate`, `/reset-password`, and `/reset-username` with
credentials in the fragment (`test_single_page_routes_carry_credentials_only_in_the_fragment` and the per-flow assertions
that the query string is empty); the notification live-feed scope is restated in the WebSocket contract; the capability
matrix is in `docs/handover/phase-11.md` Section 4 and `backend/tests/unit/test_freeze_manifest.py` proves every cited test
exists. ADR 0017 carries a dated amendment for the link change. Findings: none open. Disposition: complete.
