# 86: Backend freeze manifest

**What to build:** Regenerate the contract documents, map every frontend capability to a tested backend contract,
and publish the Freeze Manifest for the user's explicit acceptance at Gate 3.

**Blocked by:**

- [85: Non-browser regression and credential hygiene](85-non-browser-regression-and-credential-hygiene.md)

**Governing sources:**

- [Phase 11 architecture: gate](../../docs/architecture/phase-11-backend-freeze.md)
- [Full-stack pivot ADR](../../docs/adr/0024-full-stack-pivot.md)
- [OpenAPI document](../../docs/api/openapi-v1.yaml)
- [WebSocket contract](../../docs/api/websocket-v1.md)
- [Build plan](../../docs/build/plan.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] The OpenAPI document and WebSocket contract are regenerated and committed-ready, with every status code and a
      response example for each route.
- [ ] The Freeze Manifest lists every frozen route, payload, cookie, close code, and the test that proves each.
- [ ] The capability matrix from Ticket 84 has a passing test for every row.
- [ ] Tickets 79 to 85 each have a complete verification record, and every finding from their reviews is
      remediated.
- [ ] The user has explicitly accepted the freeze; backend changes after acceptance reopen Phase 11.
- [ ] Production runtime truth is listed as an unverified gate pending the user's authorization.
