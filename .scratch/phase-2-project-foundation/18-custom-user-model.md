# 18: Custom user model

**What to build:** the project's own user model, in place before any migration depends on the default one, with the
fields the account endpoints need: a unique username, a unique email, and an active flag that gates login until the
account is activated.

**Blocked by:**

- [14](14-settings-package-split.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build plan](../../docs/build/plan.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Documentation standard](../../docs/platform/documentation-standard.md)
- [Architecture decision index](../../docs/adr/README.md)

**Status:** done

- [x] A dedicated accounts application holds the user model, and the settings name it as the authentication user
      model before the first migration is generated.
- [x] The model uses a non-sequential primary key, so identifiers in URLs do not leak how many accounts exist.
- [x] Username and email are both unique, with case-insensitive uniqueness so two accounts cannot differ only by
      letter case.
- [x] Email is validated and normalised on save.
- [x] A newly created account is inactive until activated, and an inactive account cannot obtain a token.
- [x] Creation timestamps and last-modified timestamps are recorded.
- [x] The manager exposes ordinary and superuser creation, both requiring an email.
- [x] The model is registered in the admin with the sensitive fields read-only or excluded.
- [x] The initial migration is generated and applies cleanly against an empty database.
- [x] The migration check reports no missing migrations.
- [x] Model, manager, and admin all follow the documentation standard from [ticket 15](15-documentation-standards-enforcement.md).
- [x] Unit tests cover creation, superuser creation, the case-insensitive uniqueness constraints, email
      normalisation, and the inactive-by-default rule.
