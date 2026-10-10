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

**Status:** done, Locally Verified (freeze accepted 2026-10-10 under the user's explicit delegation)

- [x] The OpenAPI document and WebSocket contract are regenerated and committed-ready, with every status code and a
      response example for each route.
- [x] The Freeze Manifest lists every frozen route, payload, cookie, close code, and the test that proves each.
- [x] The capability matrix from Ticket 84 has a passing test for every row.
- [x] Tickets 79 to 85 each have a complete verification record, and every finding from their reviews is
      remediated.
- [x] The user has explicitly accepted the freeze; backend changes after acceptance reopen Phase 11. The user
      delegated the decision on 2026-10-10 ("read the file and make that decision for me"); the record is
      Section 8 of the handover.
- [x] Production runtime truth is listed as an unverified gate pending the user's authorization.

**Verification (2026-10-09, `testing` only):** the OpenAPI and WebSocket documents are regenerated; the Freeze
Manifest and capability matrix are published in `docs/handover/phase-11.md` and checked by
`backend/tests/unit/test_freeze_manifest.py`. Production runtime truth is listed as an unverified gate.

**Acceptance (2026-10-10):** the user delegated the decision to the main thread and asked for the clean-room command
blocks to be produced instead of Phase 12 starting. The main thread read the manifest against gate items 1 to 6 and
accepted the freeze; the basis, the residual risks, and the two rehearsal command blocks are in
`docs/handover/phase-11.md` Sections 8 and 9. Phase 12 is not started.
