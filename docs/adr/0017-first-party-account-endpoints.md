---
status: accepted
date: 2026-09-13
---

# First-party account endpoints instead of Djoser

The required API surface is exactly Djoser's fourteen endpoints. We implement them ourselves on DRF,
`djangorestframework-simplejwt` 5.5.1, and DRF's bundled `authtoken`, keeping the URLs identical. We do **not**
install Djoser.

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
are removed while detached JWT revocation metadata, operational logs, and backups remain subject to their existing
cleanup and retention policies.

SimpleJWT carries one unreleased **breaking** change on `master` — a 404 becomes a 401. If the VCS escape from
[0016](./0016-accept-release-lag.md) is ever taken for this package, that status change must be reflected in the
documented contract.

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
