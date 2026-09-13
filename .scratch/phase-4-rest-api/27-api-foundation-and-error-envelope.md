# 27: API foundation and error envelope

**What to build:** the shared API layer every route sits on — routing, content negotiation, pagination, and a
single error shape that every failure returns, so a client parses one envelope instead of three.

**Blocked by:** 26.

**Status:** ready-for-agent

- [ ] The API is mounted under a versioned prefix, and the version is part of the URL.
- [ ] Default authentication classes, permission classes, pagination, and renderers are configured centrally; a
      route opts out explicitly rather than each one re-declaring the defaults.
- [ ] The default permission denies unauthenticated access, so a new route is closed unless it opens itself.
- [ ] The browsable API renderer is enabled in development only, and disabled in the testing environment.
- [ ] A custom exception handler returns the envelope defined in `docs/adr/0018-api-error-contract.md` for every
      framework exception: a stable machine-readable code, a human-readable message, per-field detail for
      validation failures, and the request identifier.
- [ ] The envelope also covers the responses the REST framework never sees, so an unhandled exception or an unknown
      route returns JSON rather than an HTML error page.
- [ ] Error codes are defined in one enumeration, so a code cannot be invented at a call site.
- [ ] The request identifier in the body matches the one in the response header and in the log records for that
      request.
- [ ] Unit tests cover the handler for each exception class it maps, and an integration test asserts an unhandled
      exception returns the envelope and not a traceback.
