# Phase 11: Backend freeze for browser use

Authoritative for: the browser authentication contract, the frontend contract gaps, the security model, and the
freeze gate. Plan locked **2026-10-09**: a change to anything below needs an ADR update and the user's approval.

Decisions behind it: [0024](../adr/0024-full-stack-pivot.md), [0025](../adr/0025-browser-authentication.md),
[0027](../adr/0027-frontend-hosting-and-edge-routing.md). Tickets: 79–86 in
`.scratch/phase-11-backend-freeze/`. Everything here is verified in `testing` only; `development` is live.

## 1. Scope

- Remove every path by which routine work could read `development` credentials or address `localforge-dev`.
- Add the browser authentication group and accept its cookie on every authenticated route and on the WebSocket.
- Close the contract gaps the frontend needs: capabilities, emailed link shapes, notification scope.
- Prove non-browser clients are unchanged and no credential leaks, then freeze.

Not in scope: any route outside the browser group, a notification history route, a new environment, or any change
to the bearer and token contracts.

## 2. Cookies

Names come from environment variables with these defaults. Flags are not configurable; weakening one is a defect.

| Cookie | Default name | `HttpOnly` | `Secure` | `SameSite` | Path | Max-Age |
| --- | --- | --- | --- | --- | --- | --- |
| Access | `__Host-lf-access` | yes | yes | `Strict` | `/` | access token lifetime |
| Refresh | `__Secure-lf-refresh` | yes | yes | `Strict` | `/api/v1/browser/` | refresh token lifetime |
| CSRF | `__Host-lf-csrf` | no | yes | `Strict` | `/` | refresh token lifetime |

The refresh path covers both `refresh/` and `logout/`, the only routes that need it, and keeps the refresh
credential off every other request. Lifetimes reuse the existing JWT settings; rotation and blacklisting after
rotation are already enabled. `Secure` is always on. A browser that rejects a `Secure` cookie on plain-HTTP local
origins is documented as unsupported for that origin; the flag is never relaxed.

## 3. Routes

All under `/api/v1/browser/`. Each documents every status code with a response example, including CSRF failure,
request-size, throttling, content negotiation, and server error codes.

| Route | Method | Success | Behavior |
| --- | --- | --- | --- |
| `csrf/` | GET | `204` | Sets the CSRF cookie; `Cache-Control: no-store`; returns no body |
| `login/` | POST | `200` | Same credentials, throttling, and enumeration resistance as `/jwt/create/`; sets access and refresh cookies; body is the `users/me/` profile; requires a valid CSRF token |
| `refresh/` | POST | `204` | Rotates the pair from the refresh cookie; any failure clears all three cookies |
| `logout/` | POST | `204` | Blacklists the refresh token, clears all three cookies; safe to repeat |

No browser route returns a token in a body, header, or URL.

## 4. Authentication rules

- A new DRF authentication class accepts the access cookie on every existing authenticated route, listed after the
  bearer classes.
- **Exactly one credential source.** A request carrying both an `Authorization` header and the access cookie is
  rejected with the documented bad-request envelope. A WebSocket handshake carrying both a credential subprotocol
  and the cookie closes `4402`.
- **CSRF.** An unsafe request authenticated by cookie, and `login/`, requires a matching `X-CSRFToken` header and a
  trusted `Origin`. Bearer requests are exempt because they carry no ambient credential.
- Inactive and deleted accounts are rejected on the cookie path exactly as on the bearer path.
- The WebSocket handshake accepts the cookie only behind the existing exact Origin and Host validators.
- CORS stays exact-origin for non-browser use. The SPA is same-origin and needs none.

## 5. Contract gaps closed

| Gap | Fixed decision |
| --- | --- |
| Capabilities | `GET /api/v1/users/me/` gains a read-only `capabilities` array drawn from the closed registry `account:read`, `account:update`, `account:delete`, `credentials:change`, `notifications:receive`. An active authenticated account holds all five. The registry guards navigation only; every route still enforces authorization itself. |
| Emailed links | Links point at SPA routes with the credentials in the **fragment**, which browsers never send to a server: `/activate#account=<id>&token=<token>`, `/reset-password#...`, `/reset-username#...`. Today's links target API paths with a query string, which a single-page app cannot consume and proxies would log. |
| Notifications | Live feed only; no REST history route ([ADR 0024](../adr/0024-full-stack-pivot.md)). |

## 6. Environment variables

Three new non-secret variables, added to the inventory in [conventions.md](../platform/conventions.md): the three
cookie names. They enter `.env.example`, the secret generator, and the `testing` files in Ticket 80. The
`.env.development` and its encrypted counterpart change only as inert candidates until the user authorizes it.

## 7. Threat model

| Threat | Control |
| --- | --- |
| Script steals a credential | `HttpOnly` cookies; strict CSP on the SPA; no credential in JavaScript |
| Cross-site request forgery | `SameSite=Strict`, double-submit token, trusted `Origin` check |
| Login CSRF | `login/` requires the CSRF token |
| Cross-site WebSocket hijacking | Exact Origin and Host validation before the consumer starts |
| Credential in a URL, log, or referrer | Fragment-only emailed tokens, stripped on load, `Referrer-Policy: no-referrer` on SPA documents, redaction tests |
| Replay of a stolen refresh token | Rotation with blacklist; reuse of a rotated token fails |
| Cookie tossing from a sibling subdomain | `__Host-` and `__Secure-` prefixes |
| Clickjacking | `X-Frame-Options: DENY` and `frame-ancestors 'none'` |
| Account enumeration | Existing enumeration-resistance rules apply to every new route |

## 8. Gate

The Phase 11 gate passes when all of these hold:

1. The complete `testing` gate passes in container and host mode, in parallel.
2. No testing or tooling path reads `.env.development` or addresses `localforge-dev` (Ticket 79).
3. Every frontend capability maps to a tested backend contract (Ticket 84 matrix).
4. The bearer, token, and subprotocol suites pass unchanged, and the schema diff shows no change to their contracts.
5. The regenerated OpenAPI and WebSocket documents list every new route and close code with every status code.
6. The Freeze Manifest in `docs/handover/phase-11.md` lists every frozen route, payload, cookie, and close code.
7. The user explicitly accepts the freeze.

Status never exceeds **Locally Verified**; production runtime truth stays an unverified gate until the user
authorizes inspection. Backend code changes afterwards only by reopening Phase 11.
