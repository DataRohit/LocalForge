# 91: Account recovery and credential changes

**What to build:** Password reset request and confirm, username reset request and confirm, and signed-in password
and username change screens.

**Blocked by:**

- [90: Authentication workflows](90-authentication-workflows.md)

**Governing sources:**

- [Frontend standard](../../docs/platform/frontend-standard.md)
- [First-party account endpoints ADR](../../docs/adr/0017-first-party-account-endpoints.md)
- [OpenAPI document](../../docs/api/openapi-v1.yaml)
- [Ticket index](../README.md)

**Status:** pending

- [ ] All six endpoints are reachable from the UI and every documented status code has a handled outcome.
- [ ] `/reset-password` and `/reset-username` read `account` and `token` only from the URL fragment, remove them
      from the address bar and history before anything else renders, and send them only in the request body.
- [ ] A successful credential change reflects the new session state without a stale screen.
- [ ] Tokens never appear in logs, error reports, or telemetry.
- [ ] Status recorded as Locally Verified.
