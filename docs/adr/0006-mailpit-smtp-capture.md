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

**What actually stops mail leaving, and what does not.** Mailpit publishes its SMTP and web ports, so by
[0021](./0021-access-zone-for-published-ports.md) it joins a non-internal access zone and cannot be asserted
offline by network placement. Criterion 6 of its ticket is therefore met in substance rather than literally, and
the residue below is accepted knowingly rather than left undiscovered.

Closed by configuration, verified 2026-09-14:

| Route out | Closed by |
|---|---|
| Relay: `--smtp-relay-config`, `--smtp-relay-all`, `--smtp-relay-matching` | unset, and asserted unset by test |
| Forwarding: `--smtp-forward-config` | unset, and asserted unset by test |
| Webhooks: `--webhook-url` | unset, and asserted unset by test |
| The same routes via `MP_SMTP_RELAY_*`, `MP_SMTP_FORWARD_*`, `MP_WEBHOOK_URL` | absent from every env file, and asserted absent by test |
| Update check against `api.github.com` | `--disable-version-check`. It was **on by default** and measured connecting out, exactly as SeaweedFS did |
| Reverse DNS on every SMTP connection | `--smtp-disable-rdns` |
| Remote CSS and fonts in message previews | `--block-remote-css-and-fonts` |
| The API answering any `Host` header, so any machine that can route to the published port | `--allowed-hosts localhost,127.0.0.1` |

**Not closable: the link checker.** `GET /api/v1/message/{id}/link-check` makes Mailpit fetch the URLs found in a
captured message. v1.31.1 offers no flag to disable it — `--allow-internal-http-requests` and
`--disable-link-check-rate-limit` only widen it. It is request-triggered rather than spontaneous, and
`--allowed-hosts` now keeps that request from arriving off-box, so the residual is an operator viewing their own
captured mail. Recorded as accepted.

**Considered: publishing through the proxy instead.** Keeping Mailpit on internal zones only and routing 8025 and
1025 through `traefik-tk2jp`, which already holds the access zone, would make it assertable offline by
[../platform/service-inventory.md](../platform/service-inventory.md) Section 6.5 and close the residual outright.
Rejected for now: it contradicts the inventory's "native, none locally" dashboard row, needs a TCP entry point for
SMTP, and does nothing for `testing`, which runs no proxy at all. Revisit if the residual ever matters.

Storage is also explicit. Without `--database` Mailpit writes a temporary SQLite file it deletes on exit, so the
captured mail the ticket requires to survive a restart is pointed at the service's named volume. The write-ahead
log and shared-memory sidecars sit beside it inside that volume.
