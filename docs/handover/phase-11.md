# Phase 11 handover and Freeze Manifest

Status: **Locally Verified**. The backend freeze is **awaiting the user's explicit acceptance** (Gate 3). Backend
code changes after acceptance reopen Phase 11. Everything here was verified in `testing` only; `development` was never
read, written, or addressed. Production runtime truth is an unverified gate.

Authoritative scope: [Phase 11 architecture](../architecture/phase-11-backend-freeze.md). Decisions:
[ADR 0024](../adr/0024-full-stack-pivot.md), [ADR 0025](../adr/0025-browser-authentication.md),
[ADR 0027](../adr/0027-frontend-hosting-and-edge-routing.md). Contract documents: [OpenAPI](../api/openapi-v1.yaml),
[WebSocket](../api/websocket-v1.md).

## 1. Gate status

| Gate item | State | Evidence |
| --- | --- | --- |
| 1. Complete `testing` gate, container and host, parallel | Passed | Section 6 verification records |
| 2. No testing or tooling path reads `.env.development` or addresses `localforge-dev` | Passed | Ticket 79 record |
| 3. Every frontend capability maps to a tested backend contract | Passed | Section 4 |
| 4. Bearer, token, and subprotocol suites unchanged; schema diff shows no contract change | Passed | Ticket 85 record |
| 5. Regenerated OpenAPI and WebSocket documents list every route and close code | Passed | Section 2 |
| 6. Freeze Manifest lists every frozen route, payload, cookie, and close code | Passed | Section 2 |
| 7. The user explicitly accepts the freeze | **Pending** | Not yet given |

## 2. Freeze Manifest

Every item below is frozen. Changing any of it reopens Phase 11.

### 2.1 Browser routes

All under `/api/v1/browser/`. Statuses are exactly the documented set in the OpenAPI document; each is observed by the
named test, which compares the observed statuses with the schema.

| Route | Success | Documented statuses | Proving test |
| --- | --- | --- | --- |
| `GET csrf/` | `204`, `Cache-Control: no-store`, no body | 204, 400, 405, 406, 413, 429, 500 | `backend/tests/integration/accounts/test_browser_sessions.py::test_browser_operation_statuses_match_their_documented_contracts[csrf]` |
| `HEAD csrf/` | `204` | 204, 400, 406, 413, 429, 500 | `backend/tests/integration/accounts/test_browser_sessions.py::test_browser_operation_statuses_match_their_documented_contracts[csrf-head]` |
| `POST login/` | `200`, body is the `users/me/` profile, no token | 200, 400, 401, 403, 405, 406, 413, 415, 429, 500, 503 | `backend/tests/integration/accounts/test_browser_sessions.py::test_browser_operation_statuses_match_their_documented_contracts[login]` |
| `POST refresh/` | `204`, no body | 204, 400, 401, 403, 405, 406, 413, 429, 500, 503 | `backend/tests/integration/accounts/test_browser_sessions.py::test_browser_operation_statuses_match_their_documented_contracts[refresh]` |
| `POST logout/` | `204`, repeatable | 204, 400, 403, 405, 406, 413, 429, 500, 503 | `backend/tests/integration/accounts/test_browser_sessions.py::test_browser_operation_statuses_match_their_documented_contracts[logout]` |

Behavior frozen with the routes:

- `login/` and every unsafe cookie-authenticated request require a matching `X-CSRFToken` header and an `Origin` header
  trusted by `DJANGO_CSRF_TRUSTED_ORIGINS`. A rejection is the enveloped `403` `permission_denied`.
- `refresh/` and `logout/` require CSRF only while a credential cookie is present, so a repeated logout with no cookies
  is a harmless `204`. A refresh without a cookie is `401`. A CSRF rejection never clears cookies.
- Any `401` from `refresh/` clears all three cookies. Replaying a rotated refresh token is a `401`.
- `logout/` blacklists the refresh token, clears all three cookies, and publishes a session revocation that closes the
  account's cookie-authenticated sockets. A previously issued access token stays valid until it expires.
- No route returns a token in a body, header readable by script, or URL.

### 2.2 Existing routes

Every route that authenticates through the default classes accepts the access cookie after the bearer classes:
`users/me/` (GET, HEAD, PATCH, DELETE), `users/set_password/`, and `users/set_username/`. Their documented statuses and
payloads are unchanged; the OpenAPI security alternatives gain `browserCookieAuth`. Unsafe requests authenticated by
the cookie are CSRF-checked; bearer and token requests are exempt. A request carrying both an `Authorization` header and
the access cookie is rejected with the `400` `bad_request` envelope.

The profile payload gains one field:

| Field | Type | Meaning |
| --- | --- | --- |
| `capabilities` | array of `account:read`, `account:update`, `account:delete`, `credentials:change`, `notifications:receive` | Read-only; an active authenticated account holds all five. Guards client navigation only |

### 2.3 Cookies

| Cookie | Default name | `HttpOnly` | `Secure` | `SameSite` | Path | Max-Age | Proving test |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Access | `__Host-lf-access` | yes | yes | `Strict` | `/` | access token lifetime | `backend/tests/integration/accounts/test_browser_sessions.py::test_login_sets_httponly_cookies_and_returns_only_the_profile` |
| Refresh | `__Secure-lf-refresh` | yes | yes | `Strict` | `/api/v1/browser/` | refresh token lifetime | same |
| CSRF | `__Host-lf-csrf` | no | yes | `Strict` | `/` | refresh token lifetime | `backend/tests/integration/accounts/test_browser_sessions.py::test_csrf_route_sets_only_the_readable_cookie_and_forbids_caching` |

Only the three names come from the environment (`DJANGO_BROWSER_ACCESS_COOKIE_NAME`,
`DJANGO_BROWSER_REFRESH_COOKIE_NAME`, `DJANGO_BROWSER_CSRF_COOKIE_NAME`); settings reject a name without its required
prefix and duplicate names (`backend/tests/unit/config/test_settings.py::test_browser_cookie_names_reject_unsafe_values`).

### 2.4 WebSocket

`/ws/notifications/` accepts the access cookie when no subprotocol is offered, behind the exact Host and Origin
validators. Close codes added to behavior, none added to the table:

| Situation | Close code | Proving test |
| --- | --- | --- |
| Cookie and credential subprotocol together | 4402 | `backend/tests/integration/notifications/test_cookie_authentication.py::test_a_cookie_beside_a_subprotocol_is_rejected_never_merged` |
| Malformed or password-revoked cookie | 4402 | `backend/tests/integration/notifications/test_cookie_authentication.py::test_an_unusable_cookie_closes_with_its_documented_code` |
| Expired cookie, access token expiry, logout, or revocation | 4403 | same test, plus `test_a_cookie_socket_closes_when_its_access_token_expires` and `test_session_revocation_closes_cookie_sockets_but_not_bearer_sockets` |
| Inactive account, including at the next frame | 4404 | `test_the_next_frame_after_deactivation_closes_the_cookie_socket` |
| Disallowed or absent Origin | 4406 | `test_a_cookie_handshake_from_a_disallowed_origin_never_reaches_the_consumer` |

### 2.5 Emailed links

| Email | Link |
| --- | --- |
| Activation | `<site>/activate#account=<id>&token=<token>` |
| Password reset | `<site>/reset-password#account=<id>&token=<token>` |
| Username reset | `<site>/reset-username#account=<id>&token=<token>` |

Proving test: `backend/tests/unit/config/test_email.py::test_single_page_routes_carry_credentials_only_in_the_fragment`
and the per-flow link assertions in the integration account suites.

## 3. Cookie and origin policy

Trusted origins are exact: `DJANGO_CSRF_TRUSTED_ORIGINS` and `DJANGO_CORS_ALLOWED_ORIGINS` are environment lists, and
startup fails if either contains a wildcard
(`backend/tests/unit/config/test_settings.py::test_csrf_trusted_origins_reject_wildcards`). The single-page application
is same-origin and needs no CORS. `X-Frame-Options: DENY` and `frame-ancestors 'none'` are on every API response
(`backend/tests/integration/accounts/test_browser_cookie_authentication.py::test_api_responses_forbid_framing`).
`security.W016` no longer appears in the deployment check because the CSRF cookie is always `Secure`.

### Browser evidence for `Secure` cookies on plain-HTTP local origins

Recorded 2026-10-09 from public sources only. **No browser was driven in this session**, so this is secondary evidence,
not a hands-on result; Phase 12 Playwright runs re-verify it.

| Browser | `Secure` on `http://localhost` | `__Host-` and `__Secure-` prefixes on `http://localhost` |
| --- | --- | --- |
| Chrome (since 89) | Accepted | Reported as rejected |
| Firefox (since 75) | Accepted | Reported as accepted |
| Safari | Rejected (WebKit bugs 232088, 231035, 281149) | Rejected |

Sources: [WebKit bug 232088](https://bugs.webkit.org/show_bug.cgi?id=232088),
[WebKit bug 231035](https://bugs.webkit.org/show_bug.cgi?id=231035),
[WebKit bug 281149](https://bugs.webkit.org/show_bug.cgi?id=281149),
[httpwg/http-extensions issue 2605](https://github.com/httpwg/http-extensions/issues/2605).

Consequence: the prefixed cookies are dependable only over HTTPS. Browser use belongs on the HTTPS public origin
(`https://localforge.datarohit.com`). A plain-HTTP local origin is documented as unsupported for browsers that reject
the cookies, and the flags are not relaxed for any browser.

## 4. Capability matrix

Every frontend capability maps to backend contracts and at least one passing test.

| Capability | Client route | Backend contract | Proving tests |
| --- | --- | --- | --- |
| Registration | `/register` | `POST users/` | `backend/tests/integration/accounts/test_user_profiles.py::test_registration_creates_an_inactive_account_without_sensitive_output` |
| Activation | `/activate` | `POST users/` (activation branch); fragment link | `backend/tests/integration/accounts/test_account_activation.py::test_registration_email_activates_once_through_the_existing_user_post`; `backend/tests/unit/config/test_email.py::test_single_page_routes_carry_credentials_only_in_the_fragment` |
| Resend activation | `/resend-activation` | `POST users/resend_activation/` | `backend/tests/integration/accounts/test_account_activation.py::test_resend_is_identical_for_unknown_inactive_and_active_accounts` |
| Sign-in | `/sign-in` | `GET browser/csrf/`, `POST browser/login/` | `backend/tests/integration/accounts/test_browser_sessions.py::test_login_sets_httponly_cookies_and_returns_only_the_profile`; `backend/tests/integration/accounts/test_browser_sessions.py::test_login_rejections_are_indistinguishable_and_set_no_cookie` |
| Session upkeep | all signed-in routes | `POST browser/refresh/` | `backend/tests/integration/accounts/test_browser_sessions.py::test_refresh_rotates_the_pair_and_a_replayed_refresh_clears_every_cookie` |
| Sign-out | account menu | `POST browser/logout/` | `backend/tests/integration/accounts/test_browser_sessions.py::test_logout_blacklists_the_refresh_token_and_is_safe_to_repeat` |
| Capability-guarded navigation | all signed-in routes | `capabilities` on `users/me/` | `backend/tests/integration/accounts/test_user_profiles.py::test_profile_returns_only_the_authenticated_caller_and_ignores_query_identifiers` |
| Profile read | `/account` | `GET users/me/` with the cookie | `backend/tests/integration/accounts/test_browser_cookie_authentication.py::test_a_safe_cookie_request_needs_no_csrf_proof` |
| Profile update | `/account` | `PATCH users/me/` with cookie and CSRF | `backend/tests/integration/accounts/test_browser_cookie_authentication.py::test_an_unsafe_cookie_request_needs_a_valid_token_and_origin` |
| Account deletion | `/account` | `DELETE users/me/` | `backend/tests/integration/accounts/test_user_profiles.py::test_profile_deletion_requires_the_current_password` |
| Password change | `/account/password` | `POST users/set_password/` | `backend/tests/integration/accounts/test_password_management.py::test_authenticated_password_change_revokes_every_existing_credential_and_session` |
| Password recovery | `/forgot-password`, `/reset-password` | `POST users/reset_password/`, `POST users/reset_password_confirm/`; fragment link | `backend/tests/integration/accounts/test_password_management.py::test_password_reset_request_is_indistinguishable_and_delivers_account_bound_link`; `backend/tests/integration/accounts/test_password_management.py::test_password_reset_confirm_changes_password_notifies_revokes_and_rejects_replay` |
| Username change | `/account/username` | `POST users/set_username/` | `backend/tests/integration/accounts/test_username_management.py::test_authenticated_username_change_preserves_existing_credentials` |
| Username recovery | `/forgot-username`, `/reset-username` | `POST users/reset_username/`, `POST users/reset_username_confirm/`; fragment link | `backend/tests/integration/accounts/test_username_management.py::test_username_reset_full_loop_is_single_use_and_preserves_credentials` |
| Live notifications | `/notifications` | `/ws/notifications/` with the cookie; live feed only, no history route | `backend/tests/integration/notifications/test_cookie_authentication.py::test_a_valid_cookie_connects_and_receives_user_events` |

## 5. Inert development candidates

`development` is live and was not touched. The backend now **requires** three non-secret variables at startup, so the
next rebuild of the development stack fails with a message naming the first missing one until the user adds these exact
lines to `.env.development` (they already exist in `.env.example`):

```text
DJANGO_BROWSER_ACCESS_COOKIE_NAME=__Host-lf-access
DJANGO_BROWSER_REFRESH_COOKIE_NAME=__Secure-lf-refresh
DJANGO_BROWSER_CSRF_COOKIE_NAME=__Host-lf-csrf
```

The committed ciphertext `.env.development.sops` carries the same three names (added with `sops set`, which decrypts
only inside the tool; no value was read or printed). That file is an inert artifact: nothing loads it until the user
decrypts it into `.env.development`.

Nothing else was changed for `development`. The Compose development overlay, its containers, volumes, and networks
were neither read nor addressed.

## 6. Verification records

Quality gate = `make check` (Django checks under `config.settings.testing`, OpenAPI contract, Ruff format and lint,
docstring checker, mypy, ty, architecture audit, then the complete host suite in parallel at 100% branch coverage, the
security timing stage, and a bounded log audit) and `make testing-test-container` or `make testing-verify` for
container mode. Everything ran on 2026-10-09 against `localforge-test` only.

| Evidence | Result |
| --- | --- |
| Host static gates | Django checks, OpenAPI contract (9), Ruff, docstrings, mypy (213 files), ty, architecture audit: all pass |
| Host core suite | 2,297 tests pass, 100.00% branch coverage (final `make check`) |
| Host security timing stage | 23 pass in a clean rerun (990 s). Earlier runs on this loaded machine failed one of the two concurrent wrong-versus-unknown credential timing cases each time, with the slower side alternating (`batch=4 wrong=4.7 s unknown=2.8 s` and the reverse); the same two cases pass in isolation (60 s) and the stage passed in a prior full `make check`. Classified as load noise from a machine also running a game launcher and Docker Desktop, not a regression: no credential code path changed. Not weakened, not skipped |
| Container mode | `make testing-test-container`: 2,297 core tests pass at 100.00% coverage, 23 timing tests pass (304 s), ownership audit PASS, container-set audit PASS, log window `04:46:27Z`–`04:53:41Z` PASS, `RESULT mode=container status=0` |
| Host log and ownership audit | `PASS` for ownership, container set, and the bounded log window `04:25:19Z`–`04:45:45Z` |
| Contract documents | OpenAPI regenerated with `spectacular --validate --fail-on-warn`; diff against the previous document is additive only |
| Browser contract audit | `python -m scripts.audit_security --scope browser` passes |

| Ticket | Disposition |
| --- | --- |
| 79 Test-isolation hardening | Done. Mount removed, entry points default to `testing`, guarded Make targets, suite-wide guard against opening the live development file, baseline failures repaired |
| 80 Browser session endpoints | Done |
| 81 CSRF, cookie attributes, origin policy | Done; browser evidence is secondary, see Section 3 |
| 82 Cookie-authenticated REST | Done |
| 83 WebSocket cookie authentication | Done |
| 84 Frontend contract gaps | Done |
| 85 Non-browser regression and credential hygiene | Done |
| 86 Backend freeze manifest | Done except acceptance (Gate 3 item 7) |

Each ticket file under `.scratch/phase-11-backend-freeze/` carries its own verification record with findings and
dispositions.

## 7. Unverified gates

- **Production runtime truth** for `development` is unverified and stays so until the user authorizes inspection:
  nothing was started, rebuilt, or exercised there, and the running development backend does not yet carry the browser
  routes or the three new variables. Reported status does not exceed Locally Verified.
- **Hands-on browser evidence** for `Secure` and prefixed cookies on plain-HTTP origins is secondary, from public bug
  trackers.
- **The freeze acceptance** is the user's decision. Until it is given, ticket 86 stays pending and Phase 12 must not
  start.
