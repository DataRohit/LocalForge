# 98: Frontend security and supply-chain audit

**What to build:** An audit of the content security policy, bundle contents, dependency licences, advisories, release
age, and install scripts, wired into the security audit.

**Blocked by:**

- [96: Browser end-to-end against the testing backend](96-browser-e2e-against-testing-backend.md)
- [97: Frontend container and edge candidate](97-frontend-container-and-edge-candidate.md)

**Governing sources:**

- [Frontend standard](../../docs/platform/frontend-standard.md) (security)
- [Restricted licences ADR](../../docs/adr/0015-reject-restricted-licenses.md)
- [Security audit](../../docs/security/security-audit.md)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] The served policy is `default-src 'self'` with no inline script and no `unsafe-eval`, and a browser test
      proves it blocks an injected script.
- [ ] The production bundle contains no absolute URL to another host and no telemetry endpoint.
- [ ] `scripts/audit-supply-chain.mjs` fails closed on any licence outside the accepted list, any advisory at high or
      critical severity, any lockfile entry that requires a build script, any `minimumReleaseAgeExclude` entry, and any
      direct pin younger than seven days, with exceptions recorded in an ADR.
- [ ] The security audit script covers the frontend checks and fails closed.
- [ ] Findings and dispositions are recorded.
- [ ] Status recorded as Locally Verified.
