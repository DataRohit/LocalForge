# 32: Account activation and resend

**What to build:** `/users/resend_activation/` and the activation confirmation it drives — a new account is
unusable until the owner proves they control the email address.

**Blocked by:**

- [31](31-user-registration-and-profile.md)
- [24](../phase-3-infrastructure-integration/24-email-backend-integration.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build plan](../../docs/build/plan.md)
- [First-party account endpoints ADR](../../docs/adr/0017-first-party-account-endpoints.md)
- [API error contract ADR](../../docs/adr/0018-api-error-contract.md)
- [Platform conventions](../../docs/platform/conventions.md)

**Status:** done

- [x] Registration triggers an activation email containing a signed, time-limited, single-use token bound to the
      account.
- [x] Confirming activation with a valid token activates the account, and the token cannot be reused afterwards.
- [x] An expired, malformed, already-used, or foreign token is rejected with a distinct documented error code for
      each case, without revealing whether the referenced account exists.
- [x] Resend accepts an email address and returns the same accepted response whether or not an account exists,
      whether or not it is already active, so the endpoint cannot be used to probe for accounts.
- [x] Resend is rate-limited per address and per account, so it cannot be used to flood someone's inbox.
- [x] Every valid resend charges a normalized-email bucket that survives account creation; a resolved account
      atomically charges its immutable account bucket too, and duplicate inactive registration uses those same
      activation-mail dimensions without changing its public `201` response on denial or admission-store loss.
- [x] An already-active account receiving a resend request is not sent a new activation link.
- [x] Activation links are built from the environment's site host, so they are correct per environment.
- [x] The email renders both a plain-text and an HTML part.
- [x] The email is sent by a background task, after the database transaction commits, so a rolled-back registration
      never sends one.
- [x] Queue expiry is the signed token lifetime remaining at publication, and delayed execution revalidates the same
      salt, maximum age, subject, active state, use state, and primary token record before SMTP.
- [x] Delivery is at most once under late acknowledgement: the worker commits a primary-backed delivery claim before
      SMTP, duplicate or redelivered tasks cannot send again, and a failed or interrupted attempt recovers through
      resend with a newly issued token.
- [x] Used-token classification survives account deletion through an immutable token subject and nullable account
      reference; [Ticket 43](../phase-6-async-services/43-periodic-task-scheduler.md) owns deletion after the token
      lifetime has elapsed.
- [x] Integration tests cover the full loop against the mail capture service, plus every rejection case using
      frozen time for expiry.

## Decisions

- Activation confirmation preserves Djoser's documented `POST` method and `uid` plus token shape, represented as
  `account` plus `token`, but uses the existing `/users/` route because LocalForge's fixed route table does not
  contain `/users/activation/`.
- The emailed `/users/?account=...&token=...` URL is the environment-hosted frontend link Djoser describes; that
  frontend submits the values to the versioned `POST /users/` contract.
- Resend returns `202` for unknown, inactive, and active accounts. Only an exact valid `{email}` body records the
  client address, stable normalized email, and optional immutable account identity, for at most three events. Exact
  authoritative limits are `30/hour` per client address and `3/hour` for each recipient identity. All advisory locks
  are sorted after any account row lock; undeclared, missing, non-object, invalid, and non-POST requests record no
  resend event.
- Activation, resend issuance, activation-task delivery, and duplicate-registration issuance lock the primary
  account row before token rows or sorted admission locks and re-evaluate active state under that lock.
- SMTP failure after a durable claim does not retry that bearer. This is deliberate at-most-once delivery; resend
  creates the replacement bearer and is the recovery interface.
- Registration contains activation-token persistence failure behind its normal `201` response for new,
  inactive-email, active-email, and username-only candidates. New account creation and partial token state roll
  back together; every failed or inapplicable issuance schedules equivalent dummy work and sends no mail.
- Those normal and token-store-fallback registration outcomes share the environment-configured monotonic
  `0.200`-second public response floor; the default retains approximately 52 percent margin over the measured
  `0.131551`-second slowest pre-floor median.
