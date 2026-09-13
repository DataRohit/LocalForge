---
status: accepted
date: 2026-09-13
---

# Mailpit for local SMTP capture

Mailpit v1.31.1 (2026-09-05) captures outbound mail. It ships an SMTP listener, a web UI, a REST API usable for
test assertions, and message storage in one MIT-licensed container. Two to four releases a month, repository pushed
2026-09-06, two open issues.

The REST API matters as much as the UI: it is what lets an integration test assert that Django actually sent a
message, rather than asserting that Django *thinks* it did.

## Considered options

**MailHog v1.0.1** — the reflexive choice, and abandoned in everything but name: no release since **2020-08-11**, no
commit since 2022-08-02, a six-year-old image carrying unpatched base-image CVEs, 256 open issues.

**MailDev v2.2.1** — an active repository with a stale stable line. Its last non-RC release was 2024-12-12 and its
`latest` tag currently resolves to `3.0.0-rc.3`. A release candidate behind a floating tag is not a reproducible
pin.

## Consequences

The `testing` environment defaults to Django's `locmem` email backend and excludes Mailpit entirely, so the suite
does not depend on a second container to assert on mail. The real SMTP round-trip runs under a Compose `smtp`
profile. See [../platform/service-inventory.md](../platform/service-inventory.md).
