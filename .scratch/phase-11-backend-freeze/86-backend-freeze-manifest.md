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

**Status:** corrected freeze accepted under delegated authority 2026-10-11; Locally Verified

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
`docs/handover/phase-11.md` Sections 8 and 9. Phase 12 was not started by that acceptance.

**Reopening (2026-10-11):** Ticket 96 exposed an omitted CSRF `403` contract on authenticated unsafe account
operations. The correction, regression evidence, current verification gates, and acceptance status are recorded in
`docs/handover/phase-11.md` Section 10. The original acceptance and checkboxes above describe the earlier baseline,
not acceptance of this correction. The corrected acceptance below closes the reopened gate.

**Correction verification (2026-10-11):** `make check` passed in host mode; `make testing-build` rebuilt the testing
image and `make testing-test-container` passed on that image. Each mode collected and passed 2,297 core and 23 timing
tests, with 100% branch coverage on the core suite. Five host registration stability runs passed. The frontend gate
passed 1,087 tests at 100% coverage, and 108 real-browser cases passed. Testing health, ownership, convention, browser
security, bounded logs, and residue checks passed. Exact windows, review findings, and their dispositions are in
Section 10 of the handover. No development deployment, commit, or push occurred. The original acceptance does not
accept the corrected manifest.

**Corrected acceptance (2026-10-11):** the user renewed full autopilot authority and explicitly asked to
complete Phase 12. The main thread reviewed Section 10 against the freeze criteria and accepts the corrected
manifest under the user's previously delegated acceptance decision. All host, container, contract, and browser
gates passed; the correction only documents existing responses. Production remains unverified until the
separately authorized deployment and runtime checks. This acceptance does not waive any Phase 12 gate.
