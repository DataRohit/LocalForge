---
status: accepted
date: 2026-10-08
---

# Browser authentication with HttpOnly cookies

The browser authenticates with the existing JSON web token pair delivered as `HttpOnly`, `Secure`, `SameSite=Strict`
cookies, protected by double-submit CSRF tokens, over a single origin. Credentials never reach JavaScript, a URL,
or a log. It amends [0019](./0019-websocket-authentication.md), which rejected cookie authentication when no browser
client existed, and leaves the bearer routes untouched.

## The shape

- **Cookies.** An access cookie (`__Host-` prefix, path `/`), a refresh cookie (`__Secure-` prefix, path
  `/api/v1/browser/` so only `refresh/` and `logout/` receive it), and a readable CSRF cookie (`__Host-` prefix).
  All are `Secure` and `SameSite=Strict`; the first two are `HttpOnly`. The flags are fixed, never configurable.
  A browser that rejects `Secure` cookies on a plain-HTTP local origin is unsupported there; the flag is not
  relaxed. The exact names, lifetimes, and table are in
  [the Phase 11 architecture](../architecture/phase-11-backend-freeze.md).
- **CSRF.** A readable CSRF cookie plus an `X-CSRFToken` header, enforced by Django's CSRF machinery and exact
  trusted origins. Enforcement applies to every unsafe request authenticated by a cookie and to browser sign-in.
- **One credential source.** A request or handshake carrying both a bearer credential and the cookie is rejected,
  never merged.
- **Routes.** A new group on the closed surface: `/api/v1/browser/csrf/` (GET, seeds the CSRF cookie),
  `/api/v1/browser/login/`, `/api/v1/browser/refresh/`, `/api/v1/browser/logout/` (POST). Every existing
  authenticated route also accepts the cookie through one additional DRF authentication class.
- **Bearer routes.** `/jwt/*` and `/token/*` keep their contract, set no cookie, and stay the path for
  non-browser clients.
- **Logout.** Blacklists the refresh token and clears both cookies.
- **WebSocket.** The handshake accepts the access cookie when no credential subprotocol is present, behind the
  existing exact Origin and Host validators, which are the defence against cross-site WebSocket hijacking. A
  handshake carrying both credential sources is rejected, never merged. The subprotocol path remains for
  non-browser clients.
- **CORS.** The SPA is same-origin ([0027](./0027-frontend-hosting-and-edge-routing.md)), so the browser needs no
  CORS. Local Vite development proxies the API to stay same-origin. The existing exact-origin CORS stays for
  non-browser use.
- **Status codes.** Every new route documents every status code with a response example, per
  [0018](./0018-api-error-contract.md), and keeps the enumeration-resistance rules.

## Considered options

**Access token in JavaScript memory, refresh token in a cookie.** Rejected: the access credential is readable by any
script that runs in the page, which the mandate forbids.

**Django server-side sessions.** Rejected: sessions live on the evictable cache Valkey, so memory pressure would sign
users out; moving them to the database adds a third authentication model beside token and JWT.

**`localStorage` or `sessionStorage` tokens.** Rejected: readable by any script.

**Cookie mode on the existing `/jwt/*` routes.** Rejected: it changes a frozen non-browser contract; a separate group
keeps both contracts independently testable.

## Consequences

- Cookie-authenticated requests are CSRF-exposed in a way bearer requests are not; Ticket 81 owns the tests.
- Plain-HTTP local development works only in browsers that accept `Secure` cookies there; Ticket 81 records dated
  browser evidence of which, without changing the flags.
- Emailed links carry credentials in the URL fragment so servers and proxies never see them
  ([Phase 11 architecture](../architecture/phase-11-backend-freeze.md) Section 5).
- 0019's rejection of cookie authentication is superseded for the browser path only.
