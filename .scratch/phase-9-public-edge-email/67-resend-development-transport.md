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

**Status:** done

- [x] Add a domain-scoped Resend sending credential through the secret generator and environment files; never log or
      commit the key.
- [x] Configure development email transport for Resend SMTP/API with sender
      `no-reply@localforge.datarohit.com` and Reply-To support routing.
- [x] Keep testing on Mailpit and prove test collection does not require Resend access or credentials.
- [x] Preserve asynchronous delivery, retry, at-most-once bearer delivery, enumeration resistance, and existing email
      templates.
- [x] Send one controlled activation or recovery message and verify Resend delivery status plus application logs.

Evidence: the development environment was rebuilt and passed `development-health` and `docker-audit`; the Celery
activation task returned `True`, and the Resend dashboard showed the activation message as `Delivered`. Testing
passed `testing-health` plus the managed Mailpit integration audit (`5 passed` in both container and host phases),
with no Resend credential in the testing environment. Focused implementation tests passed (`368 passed`), and the
key was never printed, logged, or committed.
