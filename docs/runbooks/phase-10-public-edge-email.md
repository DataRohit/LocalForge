# Phase 10 public edge and email runbook

This runbook records the intended operator sequence for the public LocalForge deployment. It is a plan until tickets
64–70 complete.

## Prerequisites

- Cloudflare controls `datarohit.com`.
- Resend domain `localforge.datarohit.com` is verified.
- A domain-scoped Resend sending key exists and is stored through the project secret workflow.
- Cloudflared is installed from a pinned release and its Tunnel credential is stored through the project secret
  workflow.

## DNS

- Cloudflare remains authoritative for `datarohit.com`; confirm the delegated nameservers before changing records.
- Add the Resend verification records exactly as shown by the Resend Domains dashboard. Keep every verification
  `CNAME` and `TXT` record DNS-only. Do not copy a value from another domain or infer a selector.
- Add `_dmarc.datarohit.com` as one DNS-only TXT record with `v=DMARC1; p=none; rua=mailto:datarohit@outlook.com`.
  Review aggregate reports on **2026-10-25** before changing policy. Do not enforce `quarantine` or `reject` before
  that review records alignment evidence.
- Point `localforge.datarohit.com` at the exact Tunnel hostname returned by Cloudflare after Ticket 66. Proxy the
  application CNAME. The Tunnel hostname is provider output, not a repository-generated value.
- Create proxied originless records for `datarohit.com` and `www.datarohit.com` only when needed for Redirect Rules.
  Use Cloudflare's reserved placeholder address `192.0.2.0`; neither name may point at LocalForge's origin.
- Redirect apex and `www` to `https://localforge.datarohit.com${uri.path}` with the original query string and a permanent
  redirect. This is a Cloudflare Redirect Rule, not a Django route.
- Do not add public records for operator dashboards, Mailpit, databases, brokers, storage, metrics, or log viewers.

### Provider-record capture

The authenticated Resend dashboard verified `localforge.datarohit.com` on 2026-09-25. The following provider rows are
captured exactly as shown and are present in Cloudflare. Keep them DNS-only; do not copy a value from another domain or
infer a selector.

Required capture rows:

| Provider | Type | Name | Value | TTL | Proxy |
| --- | --- | --- | --- | --- | --- |
| Resend | TXT | `resend._domainkey.localforge` | `p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDrBc6iDTTByczhVc5Y6yqQhjgqDcSof21LziZvZoVt472x6E+kZ4MHUqzHstNaoYHsqFtyhNGOLBhi967UCzn0MczSo4WVhZYuSOgAdPbM4CdmJGhwUKRHJ/bRrY+rV9YP/EadJkCSMFbc08hifq2TAloHj0Np2dSLfh0ntLePrQIDAQAB` | Auto | DNS-only |
| Resend | CNAME | `rsend.localforge` | `rsend-apne1.forge.rmta.net` | Auto | DNS-only |
| Resend | CNAME | `send.localforge` | `send.forge.rmta.net` | Auto | DNS-only |

## Tunnel

- Run one named Tunnel from the Docker host.
- Route only `https://localforge.datarohit.com` to Traefik's internal web entrypoint.
- Reject unmatched hostnames and paths at the edge.
- Store Tunnel credentials outside the repository and rotate them on compromise or host replacement.

## Email

- Development uses Resend SMTP/API with `no-reply@localforge.datarohit.com`.
- Testing uses Mailpit and never requires Resend credentials.
- Send a controlled activation or password-reset message to a recipient owned by the operator.
- Confirm Resend delivery logs and application logs contain no key material.

## Rollback

- Disable the Tunnel route first.
- Remove or disable the apex and `www` Redirect Rules, then restore the previous DNS records from the Cloudflare audit
  log. Keep the Resend verification records until the sender is no longer used.
- Restore development email transport to Mailpit.
- Revoke the Resend key and Tunnel credential if exposure is suspected.
- Leave DNS records in place only when they do not route traffic to an unavailable host.

## Verification

- Query `NS`, `CNAME`, `A`, `AAAA`, `TXT`, and `_dmarc` records from two external resolvers.
- Confirm `localforge.datarohit.com` resolves only through Cloudflare and its certificate covers the exact hostname.
- Request apex and `www` with redirects disabled; require one permanent redirect to the canonical HTTPS hostname and no
  Django route for either alias.
- Enumerate the zone before and after the change. Any public operator hostname or origin address is a blocker.
