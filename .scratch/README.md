# LocalForge build tickets

52 tracer-bullet tickets across 7 phases, numbered globally in dependency order. Each ticket is sized to fit one
fresh context window and is verifiable on its own.

Format follows `.agents/skills/to-tickets/SKILL.md`. Ticket files carry no file paths or code snippets — those go
stale. The authoritative detail lives in [docs/](../docs/), and every ticket points at the document that owns its
facts.

## How to work these

1. Work the **frontier**: any ticket whose blockers are all done.
2. Read the documents the ticket names before starting. [AGENTS.md](../AGENTS.md) maps them.
3. `/clear` context between tickets. Each is self-contained by construction.
4. A ticket is done when every acceptance criterion is checked **and** `uv run poe check` is green.

## Phase order

Infrastructure first, then the project is brought up to meet it, then Django is wired to each running service, then
the application surface is built on top.

| Phase | Folder | Tickets | Delivers |
|---|---|---|---|
| 1 | [phase-1-infrastructure](./phase-1-infrastructure) | 01–12 | Every development service running and healthy in Docker |
| 2 | [phase-2-project-foundation](./phase-2-project-foundation) | 13–18 | Dependencies, settings split, standards, test layout, user model |
| 3 | [phase-3-infrastructure-integration](./phase-3-infrastructure-integration) | 19–26 | Django talking to every service, verified end to end, `/health` live |
| 4 | [phase-4-rest-api](./phase-4-rest-api) | 27–37 | The full REST surface with every status code documented |
| 5 | [phase-5-websockets](./phase-5-websockets) | 38–41 | Authenticated WebSockets over the Channels layer |
| 6 | [phase-6-async-services](./phase-6-async-services) | 42–46 | Celery worker, scheduler, Flower, async email, event fan-out |
| 7 | [phase-7-testing-and-audit](./phase-7-testing-and-audit) | 47–52 | Full suites in both modes, convention and security audits |

## Dependency graph

Arrows point from blocker to blocked. Tickets on the same line can run in parallel.

```text
01 ──> 02 ──> 03 ──┬──> 04 ──> 05
                   │     └──> 12
                   ├──> 06
                   ├──> 07
                   ├──> 08
                   ├──> 09
                   └──> 10
04,06,07 ────────────> 11

01 ──> 13 ──┬──> 14 ──┬──> 17
            ├──> 15   └──> 18
            └──> 16

04,17,18 ──> 19 ─┐
06,17 ─────> 20 ─┤
06,17 ─────> 21 ─┤
07,17 ─────> 22 ─┼──> 26 ──> 27 ──> 28
08,17 ─────> 23 ─┤              └──> 29,30 ──> 31 ──┬──> 32
09,17 ─────> 24 ─┤                                  ├──> 33
11,17 ─────> 25 ─┘                                  ├──> 34
10 ────────────────────────────────────────────────>└──> 35

28,31,32,33,34,35 ──> 36 ──> 37

21,27 ──> 38 ──> 39 ──> 40 ──> 41
              (30)

22 ──> 42 ──┬──> 43
            ├──> 44
            ├──> 45  (also 32,33,34)
            └──> 46  (also 40)

16 + phases 4-6 ──> 47 ──> 48 ──> 49 ──┬──> 50 ──┐
                                       └──> 51 ──┴──> 52
```

## Standards every ticket inherits

Stated once here so no ticket restates them.

**Code contains no comments.** Not sparse comments — none. Every explanation lives in a docstring.

**Docstrings are structured**, in every module the project owns, tests included:

| Level | Must contain |
|---|---|
| File | One-line title, then a 2–3 line description |
| Class | One-line title, 2–3 line description, what it inherits, its attributes and members |
| Function / method | One-line title, 2–3 line description, arguments, returns, raises |

**Tests run in parallel.** Every suite is safe under `pytest -n auto`. A test that needs serial execution must say so
with a marker and justify it in its docstring. No suite may hang: every network-touching test carries a timeout.

**Both environments, always.** A ticket that changes runtime behaviour updates `development` and `testing` together.

**Nothing is hardcoded.** Configuration comes from the environment variable inventory in
[docs/platform/conventions.md](../docs/platform/conventions.md).

**Names come from the registry.** Never invent a container, volume, or network name.

## Definition of done for the whole set

- Every development service healthy, named to the registry, and audited.
- Every documented route returns every documented status code, and the schema proves it.
- WebSockets authenticate and broadcast across two processes.
- The suite passes in a container and on the host, at 100% branch coverage, in parallel.
- `uv run poe check` is green.
