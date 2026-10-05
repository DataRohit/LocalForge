# Phase 8 SOLID architecture audit

Authoritative for: how Phase 8 interprets SOLID in this Python and Django project, what the audit covers, what counts
as evidence, and which gates close the phase.

## 1. Outcome

Phase 8 reviews every project-owned Python module against the five SOLID principles. Confirmed design problems are
fixed at the narrowest responsible seam. Behaviour, public routes, protocols, operational commands, security
properties, and runtime topology remain unchanged unless an existing governing source is updated first.

The phase produces three durable artifacts:

1. This plan, which defines the audit method.
2. `docs/architecture/solid-findings.md`, which records the complete module inventory and every finding.
3. `docs/handover/phase-8.md`, which records final changes, verification, runtime evidence, and deferred work.

Phase 8 does not claim mathematical proof or third-party certification. Completion means every scoped module has an
evidence-backed disposition, every confirmed violation is fixed, and every final gate passes.

## 2. Design vocabulary

SOLID applies to modules, interfaces, seams, and adapters. A module can be a function, class, file, package, or
tier-spanning slice. The audit does not treat classes as the only design unit.

| Term | Meaning in Phase 8 |
| --- | --- |
| Module | Anything with an interface and implementation |
| Interface | Everything a caller must know: inputs, outputs, invariants, ordering, errors, configuration, and cost |
| Seam | A place where behaviour can vary without editing the caller |
| Adapter | A concrete implementation that satisfies an interface at a seam |
| Depth | Behaviour and complexity hidden behind a small interface |
| Leverage | Capability callers gain from learning one interface |
| Locality | Change and verification concentrated in one place |
| Composition root | The module that selects and wires concrete adapters |

Deep modules are preferred: small interfaces that hide substantial behaviour. A new abstraction needs an observed
variation, multiple adapters, a test seam, or repeated policy. One adapter with no credible variation does not
justify a new protocol or abstract base class.

## 3. Project interpretation of SOLID

### Single Responsibility Principle

A module has one coherent reason to change. A reason means an actor, policy, protocol, or lifecycle concern, not
one method or one source file.

Evidence of a violation includes unrelated policies changing together, callers needing only unrelated subsets of a
large module, or one change requiring broad edits because responsibilities are mixed.

File length, function count, and class count are not evidence by themselves.

### Open/Closed Principle

A stable module accepts a real extension without repeated edits to established policy. Extension points belong only
where variation already exists or is required by a governing source.

Evidence of a violation includes repeated type switches, duplicated dispatch policy, or each new adapter requiring
edits across unrelated callers.

Phase 8 must not add speculative plugin systems, registries, factories, or inheritance trees.

### Liskov Substitution Principle

Every subtype and adapter preserves the promises of the interface it satisfies. It accepts the supported input
domain, preserves invariants, returns compatible results, and does not strengthen preconditions or weaken
postconditions.

This principle applies only where substitution exists: inheritance, protocols, framework hooks, callables, or
multiple adapters. A standalone function with no substitutable role is marked not applicable.

### Interface Segregation Principle

Callers depend only on the interface they use. Interfaces include constructor requirements, configuration,
callbacks, fixtures, and framework contracts, not only Python `Protocol` declarations.

Evidence of a violation includes callers receiving broad context objects, test fixtures exposing unrelated state,
or adapters implementing methods they cannot support.

Phase 8 must not split cohesive interfaces into tiny pass-through wrappers that reduce depth.

### Dependency Inversion Principle

Policy depends on stable interfaces. Concrete process, transport, storage, clock, randomness, and framework details
sit behind seams and are selected by composition roots.

Framework composition roots may import concrete Django, Celery, Redis, Docker, and subprocess adapters. Moving
those imports behind another wrapper without improving substitution, tests, or locality is not an improvement.

## 4. Scope

| Area | Included |
| --- | --- |
| Application code | All project-owned modules under `backend/src/accounts`, `backend/src/config`, and `backend/src/notifications` |
| Operator code | Project-owned Python scripts and their Poe command interfaces |
| Test architecture | Shared fixtures, factories, communicators, runtime probes, helpers, and tests as interface clients |
| Cross-module design | Import direction, composition roots, shared policies, adapters, framework coupling, and cycles |
| Runtime proof | Development and testing behaviour affected by any refactor |

Database migrations, generated OpenAPI, vendored assets, dependency code, Dockerfiles, Compose YAML, and
configuration data are not scored as object-oriented modules. Their interfaces with project-owned Python code
remain in scope. Historical migrations change only when correctness requires it.

## 5. Finding standard

Every inventory row in `docs/architecture/solid-findings.md` records:

- module or module cluster;
- callers and interface;
- composition root and concrete adapters, when present;
- applicable SOLID principles;
- evidence examined;
- finding identifier or `clear`;
- disposition and verification.

Each finding records one principle, exact source evidence, observable maintenance or correctness impact, proposed
seam, and regression proof. A finding cannot rely only on taste, size, naming, or a hypothetical future need.

Findings use these priorities:

| Priority | Meaning |
| --- | --- |
| Required | Confirmed violation causing change spread, invalid substitution, broad dependencies, or blocked testing |
| Improvement | Evidence-backed design debt with a safe, local improvement and measurable locality or leverage gain |
| Observation | Potential concern without enough evidence for a code change |

Required findings and improvements must be fixed. Observations remain only when the ledger explains why changing
code would be speculative or would create a shallower module.

## 6. Audit and repair loop

Each area ticket follows the same loop:

1. Run the smallest existing tests that prove current behaviour.
2. Map modules, callers, interfaces, seams, adapters, and composition roots.
3. Review all five principles and mark non-applicable principles with a reason.
4. Record findings before editing code.
5. Add regression or characterization tests at the affected interface.
6. Refactor in small behaviour-preserving steps.
7. Run targeted tests, then the complete quality and runtime gates required by the changed seam.
8. Update the findings ledger with evidence and final disposition.

Tests and callers cross the same interface. A refactor that requires tests to bypass the interface indicates the
module shape is still wrong.

## 7. Guardrails

- Preserve the fixed HTTP and WebSocket surface.
- Preserve response bodies, status codes, close codes, timing protections, command names, exit codes, and logs.
- Preserve the two environments and exact Docker registry.
- Add no dependency unless existing tools cannot express an evidence-backed seam.
- Add no abstraction for a single concrete implementation without a second adapter, real variation, or test seam.
- Prefer extraction or consolidation over compatibility layers. Remove obsolete paths in the same ticket.
- Keep 100% branch coverage, zero warnings, zero skips, strict typing, and parallel safety.
- Use no SOLID score, class-count threshold, file-length threshold, or heuristic pass percentage.
- Do not weaken a quality, security, convention, or runtime gate to make a refactor pass.

## 8. Final gate

Ticket 62 reruns the full supported verification sequence after all area audits:

```console
make sync
make development-health
make testing-health
make docker-audit
make testing-test-both
make testing-integration-audit
make check
make convention-audit
make security-audit
```

Any runtime-affecting change also requires the affected environment rebuild, deployed seam exercise, project-scoped
health and ownership checks, and bounded post-exercise logs. Any unexplained warning-or-higher record blocks Phase
8 completion.

Phase 8 closes only when the inventory is complete, every required finding and improvement is fixed, observations
are justified, both independent repository audits report no legitimate finding, and ticket 63 reconciles all
documentation with the final code.
