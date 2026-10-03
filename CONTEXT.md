# CONTEXT

Domain glossary for the LocalForge local backend platform. One authoritative definition per term. When a document
or a commit message uses one of these words, it means this and nothing else.

Scope note: this platform is infrastructure around a Django project. It has **no application domain yet** — no
models, no business entities, no API. Every term below is about the platform itself. When application work starts,
its vocabulary belongs in this file too, in a separate section.

## Environments

**development** — the full stack, every dashboard running, one Compose project named `localforge-dev`.

**testing** — the headless subset the test suite needs, Compose project `localforge-test`. Drops every dashboard and
UI service. Not a staging environment and not a mirror of development.

**host mode** / **container mode** — the two ways the `testing` suite must run and pass. Container mode runs pytest
inside `django-test-dt5qx` against container hostnames. Host mode runs pytest on the developer's machine against
published ports on `127.0.0.1`. Both are required; one passing is not a pass.

There is no separate production Compose environment. A **public deployment** may expose the development Compose stack
through the controlled Phase 9 edge; it remains the `development` environment and must not expose operator services.

## Naming

**registry** — the frozen table in [docs/platform/conventions.md](./docs/platform/conventions.md) assigning a name
to every container, volume, and network. Assigned once on 2026-09-13. Copied verbatim, never regenerated.

**ID** — the five-character lowercase code that makes a service name unique, drawn from `a–z` and `2–9`. Digits `0`
and `1` are excluded so `0`/`o` and `1`/`l` cannot be confused. `uv5n2` is an ID; `postgres-pg3ka` is a name.

**service type** — the role prefix in a name, describing what the thing does rather than what it is built from:
`postgres`, `postgres-replica`, `celery-worker`. Never a vendor version and never generic (`db`, `web`, `cache`).

## Platform structure

**tier** — a rung in the startup dependency order. Tier 1 depends on nothing; each later tier waits on the previous
one being healthy. Enforced by `depends_on` with `condition: service_healthy`.

**gate** — the pass/fail check that ends a build phase. A phase may not begin until the previous gate passes.
Weakening a gate to make it pass is the one move that is never available.

**zone** — a network's purpose, and the first segment of its name: `edge`, `app`, `data`, `obsv`. Every zone except
`edge` is `internal: true`, which is how the offline constraint is enforced rather than merely intended.

**companion** — a dashboard service added because the tool it fronts ships no UI of its own. pgAdmin is a companion
for PostgreSQL; Flower is a companion for Celery. A tool with a native UI never gets one.

**headless** — running with no UI service at all. The property that defines the `testing` environment.

**public deployment** — the development Docker stack reached through Cloudflare Tunnel at
`localforge.datarohit.com`. It is not a third Compose environment.

**public edge** — Cloudflare Tunnel plus Traefik routing that accepts the public hostname and rejects unmatched hosts
and operator paths.

## Decisions and evidence

**ADR** — a numbered decision record under [docs/adr/](./docs/adr/). One decision per file.

**release lag** — a dependency whose default branch is CI-green on our target platform while its most recent
published artifact predates that work. Three dependencies are in this state and the response is uniform; see
[docs/adr/0016-accept-release-lag.md](./docs/adr/0016-accept-release-lag.md). The word exists so the situation is
recognised once instead of rediscovered three times.

**primary source** — the artifact that owns a fact: a CI workflow file, packaging metadata, a source file, official
documentation. A PyPI trove classifier is **not** one — it is a hand-edited string that drifts in both directions,
and treating it as evidence produced three wrong conclusions in the first draft of this platform's ADRs.

## Data and storage

**stanza** — pgBackRest's name for one backup configuration covering one PostgreSQL cluster. This platform has one,
called `localforge`.

**standby** — the read-only PostgreSQL replica. Enters standby mode because a `standby.signal` file exists in its
data directory. Called `replica` when referring to the Django `DATABASES` alias pointing at it.

**channel layer** — the Valkey instance backing Django Channels group messaging, deliberately separate from the
cache instance so that cache eviction can never drop a WebSocket message.

## Application surface

**surface** — the fixed set of routes this project exposes, listed in [AGENTS.md](./AGENTS.md). It is a closed set:
adding to it is a documentation change before it is a code change.

**envelope** — the single JSON error shape every non-2xx REST response returns, and every WebSocket error frame
reuses: a stable machine-readable code, a human message, per-field detail, and the request identifier. Defined in
[docs/adr/0018-api-error-contract.md](./docs/adr/0018-api-error-contract.md).

**enumeration resistance** — the property that registration, login, password reset, and username reset reveal
nothing about whether an account exists, through status, body, or timing. Where it conflicts with returning the
most semantically precise status code, resistance wins, and the schema documents the result.

**ticket** — a tracer-bullet vertical slice under [.scratch/](./.scratch/README.md), sized for one context window,
declaring the tickets that block it.

**frontier** — the set of tickets whose blockers are all done, and therefore the set that can be picked up now.

## External identities

**transactional sender** — `no-reply@localforge.datarohit.com`, the Resend-verified address used for development
account mail. It has no mailbox or inbound forwarding rule.

**support address** — `support@datarohit.com`, the monitored human contact address routed by Cloudflare Email
Routing. It is separate from the transactional sender and receives replies.
