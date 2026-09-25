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

- Keep Resend verification records DNS-only.
- Add apex DMARC with `p=none` and an owned report mailbox before enforcement.
- Point `localforge.datarohit.com` at the Tunnel hostname through Cloudflare-managed routing.
- Do not add public records for operator dashboards.

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
- Restore development email transport to Mailpit.
- Revoke the Resend key and Tunnel credential if exposure is suspected.
- Leave DNS records in place only when they do not route traffic to an unavailable host.
