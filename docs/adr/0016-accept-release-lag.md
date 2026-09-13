---
status: accepted
date: 2026-09-13
---

# Accept release lag on three upstream-green dependencies

Three dependencies this platform needs — `celery`, `channels-redis`, and `django-storages` — are in the same
unusual state: **their default branch is CI-green on the target platform, but their most recent published artifact
predates that work.** This ADR names that state **release lag**, records the evidence for each, and fixes one
uniform response, so the three are not re-litigated separately.

The first version of this document treated all three as open questions and proposed switching tools. Checking the
CI matrices instead of the PyPI classifiers showed that was the wrong response: the code works, only the release
does not say so.

## The evidence

All checked 2026-09-13 against the projects' workflow files and packaging metadata on their default branch.

| Dependency | Released artifact | Says what | Default branch says | Lag |
|---|---|---|---|---|
| `celery` | 5.6.3, 2026-03-26, `requires_python>=3.9` | classifiers stop at Python 3.13 | CI matrix tests **3.14** on Ubuntu and Windows, unit and integration, plus the tox envlist; `setup.py` on `main` is `python_requires=">=3.10"` with a 3.14 classifier, landed in PR #10176 on **2026-04-12** | 3.14 support landed 17 days *after* 5.6.3 shipped |
| `channels-redis` | 4.3.0, 2025-07-22, no classifiers at all | nothing | CI matrix tests **3.14**, added 2025-12-08 in commit `da2b07d9`; 3.14 is now the lint pin | ~5 months of unreleased 3.14 work |
| `django-storages` | 1.14.6, 2025-04-02, `requires_python>=3.7` | Django classifiers stop at 5.1 | `ci.yml` on `master` has an explicit `django-version: "6.0"` matrix entry run against Python 3.12/3.13/3.14; `tox.ini` installs `django~=6.0.0`; `pyproject.toml` carries `Framework :: Django :: 6.0` and `Django>=4.2` with no upper pin — all in commit `85928d63`, PR #1545, **2026-08-02**, which also changed backend source and tests | ~16 months, and the CHANGELOG's unreleased section is itself stale, still naming only Django 5.2 |

Two facts make this workable rather than merely hopeful:

1. **Nothing blocks installation.** `celery` 5.6.3 declares `requires_python>=3.9` and `django-storages` 1.14.6
   declares `>=3.7`. Neither has an upper bound, so `uv` resolves both on Python 3.14 without a metadata override.
   The classifiers are cosmetic; `requires_python` is the gate, and it is open.
2. **Someone ran the tests.** A green CI matrix on the default branch is stronger evidence than a trove classifier,
   which is a hand-edited string that drifts. `celery`'s own README on `main` still claims Python 3.9–3.13 while
   its CI tests 3.14 — the classifier and the README are both stale, and the workflow file is the truth.

## The decision

Pin the released version. Treat release lag as a known, bounded risk with a rehearsed escape, not as a reason to
choose a different tool.

Escape hatch, used **only** if the phase 6 gate in [../build/plan.md](../build/plan.md) actually fails, and only for
the dependency that failed:

```toml
# Pin to the exact commit whose CI run proves the platform works.
celery = { git = "https://github.com/celery/celery", rev = "<the commit CI went green on>" }
```

`uv` supports Git dependencies natively, so this is a one-line change with a lockfile entry, not a fork. Record the
commit and the CI run URL in this file when it happens.

## Considered options

**Switch tools** — django-q2 for Celery, a hand-written boto3 storage backend for django-storages. Rejected: it
trades a dependency that is proven to work for one that is merely newer, and costs Flower, AMQP, and roughly a
hundred lines this project would then own. The earlier draft of this ADR recommended exactly this, on classifier
evidence alone. It was wrong.

**Pin every one of the three to a Git commit immediately.** Rejected: it makes the build depend on three unreleased
moving targets and skips the released artifact that will probably just work. Reach for it per-dependency, on
failure.

**Wait for releases.** Not available: `celery` has no 5.7 on PyPI, not even a release candidate, and
`django-storages` has shipped nothing in about sixteen months while remaining active.

## Consequences

- Each of the three needs a real end-to-end gate in phase 6, not an import check. A round-trip task, a message
  across two consumers, a file uploaded and read back.
- Check for new releases at execution time before pinning. `celery`'s maintainers have stated 5.7 will require
  Python ≥3.10, which matches `setup.py` on `main` — a 5.7 release resolves the lag outright.
- `django-storages` carries the most lag and the least release momentum. If its gate fails, the hand-written boto3
  backend from the rejected option becomes the fallback after all — Django's `Storage` API is small and stable.
