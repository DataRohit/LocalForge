# 18: Custom user model

**What to build:** the project's own user model, in place before any migration depends on the default one, with the
fields the account endpoints need: a unique username, a unique email, and an active flag that gates login until the
account is activated.

**Blocked by:** 14.

**Status:** ready-for-agent

- [ ] A dedicated accounts application holds the user model, and the settings name it as the authentication user
      model before the first migration is generated.
- [ ] The model uses a non-sequential primary key, so identifiers in URLs do not leak how many accounts exist.
- [ ] Username and email are both unique, with case-insensitive uniqueness so two accounts cannot differ only by
      letter case.
- [ ] Email is validated and normalised on save.
- [ ] A newly created account is inactive until activated, and an inactive account cannot obtain a token.
- [ ] Creation timestamps and last-modified timestamps are recorded.
- [ ] The manager exposes ordinary and superuser creation, both requiring an email.
- [ ] The model is registered in the admin with the sensitive fields read-only or excluded.
- [ ] The initial migration is generated and applies cleanly against an empty database.
- [ ] The migration check reports no missing migrations.
- [ ] Model, manager, and admin all follow the documentation standard from ticket 15.
- [ ] Unit tests cover creation, superuser creation, the case-insensitive uniqueness constraints, email
      normalisation, and the inactive-by-default rule.
