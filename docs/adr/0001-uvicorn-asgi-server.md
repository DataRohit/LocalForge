---
status: accepted
date: 2026-09-13
---

# Uvicorn as the ASGI server

The platform serves Django over ASGI so that Channels, async views, and WebSockets share one process model. Uvicorn
0.52.4 (2026-08-19) is the server: 24 releases in the last twelve months, a repository pushed 2026-09-13, and a
Python 3.14 classifier on the published artifact. It serves Channels over the standard ASGI interface, so choosing
it does not drag in Daphne.

The project moved from `encode/uvicorn` to **`Kludex/uvicorn`** — GitHub serves a 301. Treat the new path as
canonical.

## Considered options

**Daphne 4.2.3** — not deprecated, and the instinct to pair it with Channels is understandable since both live under
the `django` organization. It lost on cadence: two releases in twelve months for a network-facing component. Its CI
on `main` does test Python 3.14 and `pyproject.toml` there declares `requires-python = ">=3.10"`, so it remains a
viable fallback.

Two CVEs were fixed in Daphne 4.2.2 (2026-06-03), both affecting `< 4.2.2`. Any pin below that is vulnerable:

| CVE | Advisory | Severity | Issue |
| --- | --- | --- | --- |
| CVE-2026-44545 | GHSA-rrc9-mx66-ffcm | Medium, CVSS 5.3, CWE-770 | Unbounded WebSocket message/frame memory, denial of service |
| CVE-2026-44546 | GHSA-xh68-hfp5-5x5m | Low, CVSS 3.7, CWE-444 | WebSocket handshake header smuggling via autobahn's `splitlines()` |

**Granian 2.8.2** — healthy and fast, 19 releases in twelve months. Rejected because it adds a Rust build dependency
to an otherwise pure-Python image and has a shorter Django-specific track record. It is the fallback if Uvicorn ever
fails a Python gate.

**Hypercorn 0.18.0** — one release in twelve months, no commits since that release day, 149 open issues. Its
changelog does add Python 3.13/3.14 support, but the activity signal alone disqualifies it.

## Consequences

The image must install the `websockets` extra; without it Uvicorn accepts no WebSocket upgrade and Channels fails at
runtime rather than at startup.

Ticket 41 sets Uvicorn's environment-driven assembled-message ceiling to 131,072 bytes through `--ws-max-size`.
The application contract is separately fixed at 65,536 bytes, so the first range reaches ASGI and receives the
private `frame_too_large` close while anything above the transport ceiling is rejected by Uvicorn with standard
close code `1009` before application dispatch.

Uvicorn also owns observable standard outcomes outside the application enumeration: malformed protocol framing
closes `1002`, invalid UTF-8 text closes `1007`, keepalive timeout closes `1011`, and graceful shutdown or restart
closes `1012`. Invalid handshake syntax returns HTTP `400`. None can carry a LocalForge private close code or
request identifier because the transport produces it before or outside ASGI handling.

**Daphne is not a development dependency.** Reversed 2026-09-23 after pytest warnings became errors:
`channels.testing` imports Daphne's live-server package and changes the Windows event-loop policy through APIs
deprecated for removal in Python 3.16. The integration suite now uses the narrow project-owned
`tests.websocket.WebsocketCommunicator`, built directly on ASGI queues, so consumer tests retain their in-process
protocol seam without importing or installing an alternative server. Uvicorn remains the only server.
