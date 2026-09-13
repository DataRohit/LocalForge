---
status: accepted
date: 2026-09-13
---

# DRF with drf-spectacular for OpenAPI, Swagger UI, and ReDoc

The platform needs a REST layer that emits an OpenAPI schema and renders it through both Swagger UI and ReDoc.
Django REST Framework 3.18.1 (2026-09-07) with drf-spectacular 0.30.0 (2026-07-06) is the pairing: DRF declares
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

## Considered options

**django-ninja 1.7.0** — actively maintained, declares Django 6.0/6.1 and Python 3.14, and would satisfy the OpenAPI
requirement. Rejected because it replaces DRF's serializer and permission model rather than layering on it, and the
account endpoints in [0017](./0017-first-party-account-endpoints.md) are built on DRF serializers, permissions, and
throttling. Choosing it would mean reimplementing that machinery.

**drf-yasg 1.21.15** — not archived, five releases in twelve months, but a dead end: Swagger/OpenAPI **2.0 only**,
no declared Django 6.0 or Python 3.14 support, 242 open issues, and a README that steers new projects elsewhere.
