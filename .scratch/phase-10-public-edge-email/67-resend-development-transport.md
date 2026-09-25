# 67: Resend development transport

**What to build:** Send development account mail through Resend from `no-reply@localforge.datarohit.com`, while
testing continues to use Mailpit with no Resend credentials.

**Blocked by:**

- [64: Public deployment scope and configuration contract](64-public-deployment-scope.md)
- [65: Cloudflare DNS and email identity](65-cloudflare-dns-and-email-identity.md)

**Governing sources:**

- [Asynchronous transactional email](../phase-6-async-services/45-asynchronous-transactional-email.md)
- [Email backend integration](../phase-3-infrastructure-integration/24-email-backend-integration.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Resend Django integration](https://resend.com/django)

**Status:** ready-for-agent

- [ ] Add a domain-scoped Resend sending credential through the secret generator and environment files; never log or
      commit the key.
- [ ] Configure development email transport for Resend SMTP/API with sender
      `no-reply@localforge.datarohit.com` and Reply-To support routing.
- [ ] Keep testing on Mailpit and prove test collection does not require Resend access or credentials.
- [ ] Preserve asynchronous delivery, retry, at-most-once bearer delivery, enumeration resistance, and existing email
      templates.
- [ ] Send one controlled activation or recovery message and verify Resend delivery status plus application logs.
