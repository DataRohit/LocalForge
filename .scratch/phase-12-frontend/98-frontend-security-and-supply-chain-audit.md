# 98: Frontend security and supply-chain audit

**What to build:** An audit of the content security policy, bundle contents, dependency licences, advisories, and
install scripts, wired into the security audit.

**Blocked by:**

- [96: Browser end-to-end against the testing backend](96-browser-e2e-against-testing-backend.md)
- [97: Frontend container and edge candidate](97-frontend-container-and-edge-candidate.md)

**Governing sources:**

- [Frontend standard: security](../../docs/platform/frontend-standard.md)
- [Restricted licences ADR](../../docs/adr/0015-reject-restricted-licenses.md)
- [Security audit](../../docs/security/security-audit.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] The served policy is `default-src 'self'` with no inline script and no `unsafe-eval`, and a browser test
      proves it blocks an injected script.
- [ ] The production bundle contains no absolute URL to another host and no telemetry endpoint.
- [ ] Every dependency passes the licence, advisory, and install-script audit, with exceptions recorded in an ADR.
- [ ] The security audit script covers the frontend checks and fails closed.
- [ ] Findings and dispositions are recorded.
- [ ] Status recorded as Locally Verified.
