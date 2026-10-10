# 92: Profile management

**What to build:** View, edit, and delete the signed-in account's profile.

**Blocked by:**

- [90: Authentication workflows](90-authentication-workflows.md)

**Governing sources:**

- [Frontend standard](../../docs/platform/frontend-standard.md)
- [OpenAPI document](../../docs/api/openapi-v1.yaml)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] View and edit use the profile endpoint and show backend validation errors on their fields.
- [ ] Delete requires the current password and an explicit, accessible confirmation, signs the user out, and clears all
      cached data.
- [ ] Optimistic updates are not used for destructive or credential-bearing changes.
- [ ] Loading, empty, error, and offline states are implemented and tested.
- [ ] Status recorded as Locally Verified.
