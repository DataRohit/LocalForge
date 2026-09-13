# 28: Framework and middleware status-code contract

**What to build:** proof that the status codes no view ever raises still behave correctly and still return the
envelope. These are the codes integrators hit and nobody tests.

**Blocked by:** 27.

**Status:** ready-for-agent

- [ ] Every status code in the table in `docs/adr/0018-api-error-contract.md` is reachable by a test against a real
      route, and each returns the envelope.
- [ ] An unacceptable `Accept` header returns the not-acceptable code, produced by content negotiation before any
      view runs.
- [ ] An unsupported `Content-Type` on a write returns the unsupported-media-type code.
- [ ] A valid route called with a method it does not implement returns method-not-allowed, with the permitted
      methods in the response header.
- [ ] An unknown path returns not-found in the envelope, not an HTML page, even though it never reaches the API
      layer.
- [ ] A CSRF failure on a session-authenticated write returns forbidden in the envelope, and the test proves it
      comes from middleware rather than a permission class.
- [ ] An unhandled server error returns the envelope with no traceback, no settings detail, and no stack frames,
      with debug disabled.
- [ ] Request body size is bounded, and an oversized body is rejected with a defined code rather than consuming
      memory.
- [ ] Malformed JSON returns a bad-request envelope rather than a server error.
- [ ] Every one of these is added to the schema in ticket 36, so the documented contract and the tested behaviour
      are the same set.
