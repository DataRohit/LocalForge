# 28: Framework and middleware status-code contract

**What to build:** proof that the status codes no view ever raises still behave correctly and still return the
envelope. These are the codes integrators hit and nobody tests.

**Blocked by:** 27.

**Status:** done

- [x] Every status code in the table in `docs/adr/0018-api-error-contract.md` is reachable by a test against a real
      route, and each returns the envelope.
- [x] An unacceptable `Accept` header returns the not-acceptable code, produced by content negotiation before any
      view runs.
- [x] An unsupported `Content-Type` on a write returns the unsupported-media-type code.
- [x] A valid route called with a method it does not implement returns method-not-allowed, with the permitted
      methods in the response header.
- [x] An unknown path returns not-found in the envelope, not an HTML page, even though it never reaches the API
      layer.
- [x] A CSRF failure on a session-authenticated write returns forbidden in the envelope, and the test proves it
      comes from middleware rather than a permission class.
- [x] An unhandled server error returns the envelope with no traceback, no settings detail, and no stack frames,
      with debug disabled.
- [x] Request body size is bounded, and an oversized body is rejected with a defined code rather than consuming
      memory.
- [x] Malformed JSON returns a bad-request envelope rather than a server error.
- [x] The durable, complete status-code registry/matrix required by ticket 36 is established, with every framework
      and middleware status above mapped to the real route and trigger that reaches it.
