---
status: accepted
date: 2026-09-13
---

# DRF with drf-spectacular for OpenAPI, Swagger UI, and ReDoc

The platform needs a REST layer that emits an OpenAPI schema and renders it through both Swagger UI and ReDoc.
Django REST Framework 3.18.0 (2026-08-19) with drf-spectacular 0.30.0 (2026-07-06) is the pairing: DRF declares
Django 5.2, 6.0, and 6.1 plus Python 3.14, and drf-spectacular is the only schema generator that declares Django
6.0 while emitting OpenAPI 3.1 with both viewers.

**Scope:** the schema endpoint, the Swagger UI view, and the ReDoc view are infrastructure. The application surface
they document is fixed and listed in [AGENTS.md](../../AGENTS.md); adding a route outside it is a documentation
change first. Every documented route must carry every status code it can return with a response example — see
[0018](./0018-api-error-contract.md), which is the requirement that ultimately decided
[0017](./0017-first-party-account-endpoints.md) too.

## Offline asset serving

drf-spectacular loads Swagger UI and ReDoc assets from a CDN by default, which renders both dashboards blank on an
offline network. `drf-spectacular-sidecar` 2026.9.1 (2026-09-01, bundling Swagger UI 5.32.14 and Redoc 2.5.3) ships
the assets locally. Add `drf_spectacular_sidecar` to `INSTALLED_APPS` and set all three keys to the string
`'SIDECAR'`:

```python
SPECTACULAR_SETTINGS = {
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
}
```

Verified against the drf-spectacular documentation on 2026-09-13. Omitting the sidecar is the single most likely way
to end this phase with two dashboards that load but display nothing.

The three documentation routes share the required `DJANGO_API_DOCUMENTATION_ENABLED` flag. Development sets it to
`true`; generated testing configuration sets it to `false`, keeping the headless environment free of both browser
UIs, the network-exposed schema, and static interception. Contract tests do not need that route: they invoke the
schema generator directly. The finalizer removes optional documentation infrastructure paths before completing the
fixed application contract, so direct generation is identical whether the flag is on or off. An operator can
therefore remove the complete documentation surface at process startup without changing code.

drf-spectacular's bundled ReDoc template still references Google Fonts even when `REDOC_DIST` is `SIDECAR`.
LocalForge overrides only that HTML shell, retaining the sidecar JavaScript while removing both remote font links.
The rendered Swagger UI and ReDoc pages must contain no external HTTP asset URL.

Uvicorn serves development HTTP through `config.asgi:application`, not `runserver`, so that entry point conditionally
wraps the existing HTTP stack in Django's `ASGIStaticFilesHandler`. Django labels this handler as development-only;
that is accepted here because LocalForge has no production environment, the flag enables it only in the local
development environment, and the normal entry point still runs `collectstatic` into the registered static volume.
The handler resolves only installed static-finder resources, preserves Django's content types, `Last-Modified` and
conditional `304` behavior, and maps unsafe finder paths to `404`. LocalForge reapplies its browser hardening headers
to intercepted responses.

Static interception is outside request logging, Prometheus, API throttling, and API body limiting, whose existing
order remains unchanged for application traffic. Documentation assets therefore cannot consume API admission or
metrics. Their `/static/` URLs are infrastructure resources rather than Django URL patterns or application routes;
the fixed application route table remains unchanged. The existing Traefik router forwards them to the same
`django-uv5n2` ASGI service, so no additional router, container, dependency, or externally exposed port is required.

## The pinned DRF version is 3.18.0, not 3.18.1

The first draft of this decision named 3.18.1 (2026-09-07). Measured 2026-09-14 while resolving the dependency
baseline: the configured package index — `https://packagefeedproxy.microsoft.io/pypi/simple/`, the only index this
project resolves against — mirrors `djangorestframework` up to **3.18.0** (`requires-python >=3.10`). Requesting
3.18.1 fails with `there is no version of djangorestframework==3.18.1`, while
`https://pypi.org/pypi/djangorestframework/json` reports 3.18.1 as the current release. The gap is the mirror's, not
upstream's, so this is index lag rather than the release lag of [0016](./0016-accept-release-lag.md).

The floor is therefore `>=3.18.0`. Nothing in this decision depends on the patch: the Django 6.0 and Python 3.14
declarations that chose DRF are present in 3.18.0, and the lockfile records the exact resolution. When the mirror
catches up, `uv lock --upgrade-package djangorestframework` moves it with no code change.

## Considered options

**django-ninja 1.7.0** — actively maintained, declares Django 6.0/6.1 and Python 3.14, and would satisfy the OpenAPI
requirement. Rejected because it replaces DRF's serializer and permission model rather than layering on it, and the
account endpoints in [0017](./0017-first-party-account-endpoints.md) are built on DRF serializers, permissions, and
throttling. Choosing it would mean reimplementing that machinery.

**drf-yasg 1.21.15** — not archived, five releases in twelve months, but a dead end: Swagger/OpenAPI **2.0 only**,
no declared Django 6.0 or Python 3.14 support, 242 open issues, and a README that steers new projects elsewhere.
