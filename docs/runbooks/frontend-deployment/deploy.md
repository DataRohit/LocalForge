# Deploy the frontend into development

Procedure to put `frontend-fe6vn` behind the existing Traefik in `localforge-dev`. The service, its routing, the
operator and naming registries, and the image build target are all part of the committed development definition
(`compose.development.yaml`, `backend/scripts/manage_platform.py`, `backend/scripts/audit_naming.py`), so
`make environments-setup` and every `make development-*` command deploy it with no separate step.

Decision record: [ADR 0027](../../adr/0027-frontend-hosting-and-edge-routing.md). Candidate history:
[README.md](./README.md). Undo: [rollback.md](./rollback.md).

Steps marked `[existing development]` change a **running** `development` environment and need the user's explicit
authorization. A fresh machine needs none of them: `make environments-setup` builds the image, starts the container,
and the checks in sections 3 to 5 then confirm it.

## 0. Preconditions `[testing only]`

All of these must have passed on the commit being deployed:

```console
make frontend-check
make frontend-e2e
make frontend-audit-refresh
make frontend-audit
make frontend-image
make frontend-image-check
make check
make testing-test-container
```

`make frontend-audit-refresh` is the one registry step; it saves evidence that `make frontend-audit` reads offline and
that expires after 24 hours, so run it in the same session.

`make frontend-image-check` rehearses the routing and its rollback against the isolated `testing` backend. Do not
continue if any command fails. Run load-heavy suites one at a time, never concurrently.

## 1. Capture the baseline `[existing development]`

Records what "healthy before" looks like, so a later difference is attributable to this change.

```console
make development-health
make docker-audit
make convention-audit-development
```

Note the UTC time as `T0`. Save the output with the deployment record.

## 2. Build and start `[existing development]`

```console
docker compose --project-name localforge-dev --env-file .env.development -f compose.yaml -f compose.development.yaml build frontend-fe6vn
docker compose --project-name localforge-dev --env-file .env.development -f compose.yaml -f compose.development.yaml up --detach --no-deps frontend-fe6vn django-uv5n2
```

`--no-deps` leaves every other service untouched. Django is recreated because its router labels changed from a
whole-host rule to the owned-prefix rule at priority 100; this restarts only that container, keeps its named volumes,
and touches no database, cache, broker, or object-storage data. Expect a brief interval in which Django restarts.
On a fresh machine `make environments-setup` performs both operations as part of the normal start.

## 3. Health

```console
docker inspect --format "{{.State.Health.Status}} {{.RestartCount}}" frontend-fe6vn django-uv5n2
make development-health
make docker-audit
make convention-audit-development
```

Required: both containers `healthy` with restart count `0` for the frontend; `development-health`, the project-scoped
Docker audit, and the convention audit pass with `frontend-fe6vn` present on `edge-net-ne2vk` only, no published port,
and no volume.

## 4. Behavior through the deployed seam

The local entry point is Traefik on loopback port 8080 with the `localforge.localhost` host.

```console
curl.exe -s -D - -o NUL -H "Host: localforge.localhost" http://127.0.0.1:8080/sign-in
curl.exe -s -o NUL -w "%{http_code}\n" -H "Host: localforge.localhost" http://127.0.0.1:8080/account
curl.exe -s -w "\n%{http_code}\n" -H "Host: localforge.localhost" http://127.0.0.1:8080/health/
curl.exe -s -w "\n%{http_code}\n" -H "Host: localforge.localhost" http://127.0.0.1:8080/api/v1/image-proof-missing/
curl.exe -s -o NUL -w "%{http_code}\n" -H "Host: localforge.localhost" http://127.0.0.1:8080/api/v1/browser/csrf/
```

Required: `/sign-in` and `/account` return `200` with the single-page application and the strict
`Content-Security-Policy` (`default-src 'self'`, no `unsafe-inline`, no `unsafe-eval`); `/health/` returns `200` with
readiness `ready`; the unknown API path returns Django's `404` error envelope, **not** the application shell; the CSRF
route returns `204`. Then sign in through a browser at `http://localforge.localhost:8080/` and confirm the
notification socket connects.

Public edge, when the Cloudflare Tunnel is connected (needs the user's authorization to address the public host):

```console
curl.exe -s -o NUL -w "%{http_code}\n" https://localforge.datarohit.com/sign-in
curl.exe -s -o NUL -w "%{http_code}\n" https://localforge.datarohit.com/api/v1/browser/csrf/
curl.exe -s -o NUL -w "%{http_code}\n" https://localforge.datarohit.com/admin/
```

Required: `200` for the application shell, `204` for the CSRF route, and the public admin path answered by Django's
block, not the shell.

## 5. Bounded log window

Let `T1` be the UTC time after step 4.

```console
docker logs --since <T0> --until <T1> frontend-fe6vn
docker logs --since <T0> --until <T1> traefik-tk2jp
docker logs --since <T0> --until <T1> django-uv5n2
```

Required: no unexplained `WARNING`, `ERROR`, or `CRITICAL`, no restart, no unhealthy state, and no success response
logged as a failure. The frontend's access records are JSON and omit query strings; its error records are escaped JSON
with their original severity. Alloy already collects them through the project label, so the same window is queryable
in Loki by the `frontend-fe6vn` service label.

## Status ceiling

Even a fully passing run is **Locally Verified**. Production-Ready and Production-Deployed need the user's
public-edge evidence.
