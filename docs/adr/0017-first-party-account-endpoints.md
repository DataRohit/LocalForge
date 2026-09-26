---
status: accepted
date: 2026-09-13
---

# First-party account endpoints instead of Djoser

The required API surface follows Djoser's account contracts on DRF, `djangorestframework-simplejwt` 5.5.1, and
DRF's bundled `authtoken`, with the activation confirmation folded into the fixed `/users/` route as recorded
below. We do **not** install Djoser.

This reverses the obvious choice, so the evidence is recorded in full. All checked 2026-09-13.

## Why not Djoser

Djoser 2.3.4 was released 2026-08-01 and **empirically works** — a probe on Python 3.14.6 with Django 6.0.8, DRF
3.18.0, and djoser 2.3.4 exercised all fourteen endpoints successfully. Working is not the same as supported:

| Signal | Finding |
| --- | --- |
| CI matrix on `master` | Three jobs — Django 3, 4, 5. **No Django 6.0 row. No Python 3.14 row.** |
| DRF in CI | Every row pins **DRF 3.14**. Our DRF 3.18.0 is entirely untested upstream |
| Release cadence | ~1–2 per year, with a 13-month gap then a 12-month gap |
| Open issues | 156, with a 3.0 rewrite in flight |
| Dependency surface | Drags in `social-auth-core` and caps `social-auth-app-django<6.0.0`, pinning a 5.x line whose Django 6 support is also not CI-proven |

This is **not** release lag as defined in [0016](./0016-accept-release-lag.md). There, the default branch is green
and only the artifact is behind. Here nothing upstream has ever tested this combination, so adopting Djoser makes
us the QA for it.

The decisive argument is different, though, and it would hold even if Djoser's CI were green.

## The schema requirement forces the work anyway

Every route must document every status code with response examples. Verified 2026-09-13, drf-spectacular 0.30.0
cannot do that for Djoser unaided:

- There is no djoser contrib module, no blueprint, and no known-issues entry.
- It emits an illegal-empty-component-name warning on Djoser's token-destroy view.
- It **mis-documents every 204 write endpoint as a 200 echoing the request serializer** — wrong status, wrong body.

Fixing that means an `@extend_schema` override on every Djoser view, written against serializers we do not control
and cannot change. At that point we are writing the schema by hand while inheriting someone else's view logic,
error shapes, and dependency tree. Writing the views too is less work and yields full control of the error envelope
required by [0018](./0018-api-error-contract.md) and of the enumeration-resistance the security audit checks.

Note also that drf-spectacular's own CI tops out at **DRF 3.17**, so DRF 3.18.0 is untested there as well. That risk
is unavoidable — it applies whichever schema generator path we take.

## What we use instead

| Concern | Choice | Evidence, 2026-09-13 |
| --- | --- | --- |
| JWT create / refresh / verify | `djangorestframework-simplejwt` 5.5.1 | **Release lag.** `master` is green on Django 6.0 × Python 3.14 (PR #959, merged 2026-02-09); release 5.5.1 predates it by ~7 months. Works in the probe. Handled under [0016](./0016-accept-release-lag.md) |
| Token login / logout | `rest_framework.authtoken`, bundled with DRF 3.18.0 | Ships in DRF. See the caveats below |
| Account endpoints | First-party views, serializers, and URLs | This decision |

## Ticket 31 public account representations

Recorded 2026-09-16. `POST /users/` returns `201` with only the submitted username and normalized email. It omits
the database identifier and account state so a PostgreSQL `LOWER` uniqueness conflict can return exactly the same
public status and body as creation; enumeration resistance takes precedence over the semantic precision of a
different duplicate status.

`/users/me/` represents the caller as `id`, `username`, and `email`. Only `email` is mutable there: `id` is the
immutable account identifier, while username changes remain owned by the dedicated username route already fixed in
the application surface. Profile deletion returns `204`; its schema records that the account and secondary token
are removed while digest-only activation classification tombstones, detached JWT revocation metadata, operational
logs, and backups remain subject to their existing cleanup and retention policies.

SimpleJWT carries one unreleased **breaking** change on `master` — a 404 becomes a 401. If the VCS escape from
[0016](./0016-accept-release-lag.md) is ever taken for this package, that status change must be reflected in the
documented contract.

## Ticket 32 activation route and token contract

Recorded 2026-09-17. [Djoser's published activation contract](https://djoser.readthedocs.io/en/latest/base_endpoints.html#user-activate)
uses `POST` with `uid` and `token`, and explicitly says the emailed URL belongs to a frontend that submits that POST
rather than to the activation endpoint itself. Its default endpoint path is `/users/activation/`, but LocalForge's
closed route table contains `/users/` and `/users/resend_activation/` and no activation route. The route table is the
more specific project contract.

LocalForge therefore preserves the POST method and request shape while folding confirmation into `POST /users/`.
Registration bodies still contain `username`, `email`, `password`, and `password_confirm`; activation bodies contain
only `account` and `token`. The email carries a credential handoff URL on `DJANGO_SITE_URL`; it is not a browser
endpoint and does not claim that `GET` completes activation. A LocalForge client extracts the query values and
submits them to the versioned `/users/` POST. No `/users/activation/` route exists.

Ticket 36 names the combined OpenAPI operation `user_registration_or_activation`. Its summary and description state
both exact body branches and distinguish the enumeration-resistant registration `201` representation from the
bodyless activation `204`, so generated clients do not present the operation as registration-only.

Activation tokens are timestamp-signed with Django's signing API, bind the immutable account key and a random nonce,
and are stored only as SHA-256 digests. A successful confirmation locks the account before its token record, activates
the account, and consumes every outstanding link for that account in one primary-database transaction. Expired,
foreign, malformed, and used tokens return distinct stable `400` codes without exposing account fields.

`POST /users/resend_activation/` returns the same `202` body for unknown, inactive, and active addresses. It publishes
the same activation-shaped background task in every case, but only an inactive account has an authoritative digest
record the task can resolve into a recipient. The resend boundary uses primary-backed atomic rolling windows of
`30/hour` per client address and `3/hour` per normalized email, adding the same `3/hour` immutable account identity
when one exists. The email bucket never changes at account creation, so earlier unknown-address admissions constrain
the later account and carries no existence-dependent discriminator. Only a complete request body with exact shape
`{email}` and a valid normalized address records the address, stable email, and optional account dimensions, for at
most three events. Undeclared fields, missing fields, non-object bodies, invalid email values, and other methods
record nothing. Valid requests lock a resolved primary account row before sorted advisory admission locks.
PostgreSQL loss fails closed with the shared correlated `503`.

Duplicate inactive registration uses the same normalized-email and immutable-account activation-mail admission,
independent of caller address. Denial or admission-store loss keeps the exact public `201` response and performs the
same dummy signing and task-publication work without creating a real token or sending mail. Registration therefore
does not become an inbox-flooding bypass and does not disclose the duplicate.

Resend issuance and confirmation serialize on the primary account row before token rows, and both re-read active
state after acquiring it. Queue publication carries Celery expiry equal to the signed lifetime remaining at commit.
The task repeats the same signature, age, subject, active, use, and digest checks, then commits a durable claim before
its sole SMTP attempt. Automatic retry is disabled only for this task; late acknowledgement remains enabled and a
duplicate or redelivered invocation observes the claim and sends nothing. SMTP failure recovers through resend.

Activation records retain an immutable subject identifier and use a nullable account reference. An already-used
bearer therefore remains `activation_token_used` after account deletion, while an unissued or mismatched bearer stays
`activation_token_foreign`. Token material is still digest-only. Ticket 43 owns bounded primary cleanup after the
signed maximum age, using the issue-time retention index; Ticket 32 does not implement Phase 6 scheduling.

The `crypto` extra exists and pulls only `cryptography>=3.3.1`. It is needed solely for RS\*/ES\* signing; with the
default HS256 it is dead weight and is **not** installed.

## DRF token authentication caveats, recorded deliberately

DRF's own documentation is explicit, and these are accepted because the token scheme is the secondary one:

- Tokens are stored **in plaintext**, as the table's primary key.
- There is exactly **one non-expiring token per user**.
- DRF itself points at `django-rest-knox` for expiry and tighter security.

Consequences we accept and must implement: rotate the token on logout by deleting it, never log or return it
outside the login response, and treat JWT as the primary scheme for anything long-lived. If token expiry becomes a
requirement, `django-rest-knox` is the documented upgrade path and this ADR is superseded.

## What the user model does and does not give these endpoints

Measured 2026-09-14, when the model was built. The identifiers are unique **case-insensitively**, enforced by two
functional constraints, and the manager's natural-key lookup matches case-insensitively to suit. Three consequences
land on the endpoints rather than on the model:

- **Serializer uniqueness must be written case-insensitively.** A `ModelSerializer` generates a `UniqueValidator`
  from the plain `unique=True` on each column, and that validator is case-sensitive. A username differing only in
  case therefore passes validation and fails at the database, turning ordinary input into a 500. Tickets 31 and 34
  must validate against the lowercased value themselves.
- **The default duplicate message breaks enumeration resistance.** It states that an account with that identifier
  already exists. Registration and username change must return their documented indistinguishable response instead,
  per [0018](./0018-api-error-contract.md). The constraints carry neutral messages so a violation that does escape
  says nothing useful, but the endpoints are what make the guarantee.
- **The inactive flag is enforced by `authenticate`, not by token creation.** Both the DRF token endpoint and
  SimpleJWT's obtain serializer call `authenticate`, so an unactivated account is refused. `RefreshToken.for_user`
  does **not** check it, so ticket 30 and the WebSocket middleware in ticket 39 must not mint tokens through that
  path without checking `is_active` themselves.

## Ticket 33 password replacement and recovery contract

Recorded 2026-09-17. `POST /users/set_password/` accepts exactly `current_password`, `new_password`, and
`new_password_confirm`. It locks the authenticated primary account, refuses stored password profiles outside the
bounded login verification policy, requires the current password, requires a different replacement, and runs every
configured Django password validator with the account attributes available.

`POST /users/reset_password/` accepts exactly `{email}` and always returns `202` with the same body for active,
inactive, and unknown addresses. Every accepted outcome observes the configured monotonic response floor. A real
reset token is issued only for an active account; other outcomes perform the same Django token-generation and
task-publication shape with no authoritative digest record or recipient. Failure limited to inserting the reset
record takes that same accepted dummy-publication path inside a savepoint, rolling back any partial active-account
record. Account lookup and admission loss remain dependency failures because no common outcome can be established.

The emailed bearer combines Django's `PasswordResetTokenGenerator` value with a random per-issuance nonce and is
stored only as a SHA-256 digest. The generator binds account id, password hash, last login, email, timestamp, and
the project signing secret; the digest record makes each complete bearer independently single-use and permits
stable malformed, foreign, expired, and used classifications. `POST /users/reset_password_confirm/` accepts exactly
`account`, `token`, `new_password`, and `new_password_confirm`.

Both reset routes validate their exact bodies before charging quota. Request admission atomically charges client
address, stable PostgreSQL-normalized email, and optional immutable account dimensions. Confirmation admission
charges client address and submitted immutable account dimensions. Both use the primary-backed rolling-window store
and fail closed with `503`. Confirmation applies exact-shape and account-independent validators before admission.
Only after it locks and authenticates the account and token does it run the complete validator set against real
account attributes. Consequently account-independent invalid bodies consume no quota, while account-sensitive
policy failures occur after and consume one admitted attempt.

Issuance, delivery, confirmation, account deletion, email changes, and authenticated password changes use
account-before-token lock order. Delivery commits one durable claim before its sole SMTP attempt, carries broker
expiry equal to the confirmation lifetime remaining, and revalidates account activity, email, password, expiry,
digest, and use state while holding both rows through SMTP. SMTP failure recovers through another reset request.

A password replacement deletes the account's DRF token, blacklists every outstanding refresh token, and changes the
password hash embedded in every newly issued JWT. JWT authentication, refresh, and verification compare that claim
to the current primary password hash, so access and refresh credentials issued before the change fail immediately.
Django's session authentication hash provides the same behavior for cached sessions. The caller receives no
exception: the credential or session used for the change is invalid after the `204` response and the client must
authenticate again.

DRF token login and JWT create complete bounded password verification without an account row lock, then begin
issuance with an immutable account ID and exact password-security snapshot. The issuance transaction locks the
account, rejects changed or inactive state generically, and only then persists credentials. Password replacement and
recovery retain account-first locking, so an old password cannot mint a credential that survives either ordering.
JWT refresh uses the same account-before-outstanding-token order,
then rechecks blacklist state and atomically blacklists and rotates the credential.

Successful recovery consumes every outstanding reset record in the password transaction and sends a credential-free
password-change notification after commit. A consumed record retains its immutable subject after account deletion,
so replay remains `password_reset_token_used`; an unused orphan or a token invalidated by email change is foreign.
Confirmation checks used state first after locking, then rechecks expiry before account binding. A bearer that ages
out while waiting for locks therefore returns `password_reset_token_expired`, while a concurrently consumed bearer
retains used precedence. Ticket 43 owns bounded primary cleanup after the configured maximum age.

## Ticket 34 username replacement and recovery contract

Recorded 2026-09-17. `POST /users/set_username/` accepts exactly `current_password` and `new_username`. It locks the
authenticated primary account, applies the bounded current-password verification policy, validates Django's standard
username character contract, and checks availability with PostgreSQL `LOWER` expressions matching the functional
constraint. A conflict, including a concurrent constraint winner, returns only
`{"new_username": ["That username is not available."]}` inside the standard validation envelope.

`POST /users/reset_username/` accepts exactly `{email}` and returns the same timed `202` response for active,
inactive, and unknown addresses. Request admission atomically charges route-specific client address, stable
PostgreSQL-normalized email, and optional immutable account dimensions. A real account-bound digest record is issued
only for an active account; every other outcome performs equivalent token generation and task publication. Failure
limited to token insertion rolls back partial state and takes the same accepted dummy path.

The emailed bearer uses a dedicated-salt subclass of Django's `PasswordResetTokenGenerator` plus a random
per-issuance nonce. It binds immutable account id, password hash, last login, email, timestamp, and the project
signing secret while persistence retains only its SHA-256 digest. Its HMAC, account-state, and age validation use
the independent username-reset lifetime, not Django's password-reset timeout. Confirmation accepts exactly
`{account, token, new_username}` and charges route-specific client address plus submitted immutable account only
after exact-shape and account-independent username-format validation. Availability runs after admission and bearer
authentication against the locked live account.

Issuance, delivery, confirmation, authenticated change, and account deletion use account-before-token lock order.
Delivery commits a durable claim before its sole SMTP attempt, carries broker expiry equal to the signed lifetime
remaining, and revalidates account activity, email, password, expiry, digest, and use state while holding both rows
through SMTP. Failure recovers through another request. A successful change consumes every outstanding
username-reset record and sends a notification containing no old username, new username, account identifier, or
credential.

Malformed, foreign, expired, and used bearers have distinct stable codes. Used state takes precedence after locking
and survives account deletion through the immutable subject tombstone; unused deletion, email or password change,
record loss, and mismatched account state are foreign. Ticket 43 owns bounded primary cleanup after the configured
maximum age.

Unlike password replacement, username replacement changes neither the password hash nor any credential record.
Existing DRF tokens, JSON web access and refresh tokens, and Django sessions therefore remain valid and resolve the
same immutable account after the change. Old username-and-password login stops matching; new username-and-password
login begins matching.

## Considered options

**Adopt Djoser and pin it exactly.** The lowest-code path. Rejected on the combination above: untested on our
stack, a mandatory per-view schema override anyway, an unwanted social-auth dependency tree, and a rewrite in
flight that will move the ground later.

**Adopt Djoser only for the account endpoints and write the JWT views.** Splits the surface across two error
contracts and two schema styles, which the "one page a client integrator reads" requirement in
[0018](./0018-api-error-contract.md) rules out.

**`django-allauth` with its headless API.** Actively maintained and broader, but it does not expose the required
URL surface, so every endpoint would need a compatibility shim. Wrong shape for a fixed API contract.

## Consequences

- We own the account logic, so a full contract test suite over all fourteen endpoints is mandatory, not optional.
  That obligation exists in the Djoser path too — this decision just makes it honest.
- Password reset, activation, and username reset tokens use Django's own signing and token generators, which are
  maintained by the framework and need no third party.
- If Djoser 3.0 ships with a green Django 6 CI matrix and a stable schema story, revisiting this is a contained
  change: the URLs already match.
