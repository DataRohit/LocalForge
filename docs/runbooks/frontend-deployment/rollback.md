# Roll back the frontend deployment

Restores the routing that existed before the frontend candidate: Django again owns the whole `localforge.localhost`
and `localforge.datarohit.com` hosts. The frontend is stateless, so **no data is restored, migrated, or lost**.
`frontend-fe6vn` has no volume, secret, or bind mount, and rollback does not touch Postgres, Valkey, RabbitMQ,
SeaweedFS, or any Django volume. Steps marked `[AUTHORIZATION REQUIRED]` address `development`.

Deployment: [deploy.md](./deploy.md). Candidate files: [README.md](./README.md).

## Rehearsal `[testing only]`

```console
make frontend-image-check
```

The proof starts the real image, applies the candidate routing, then stops the disposable Traefik and restarts it with
the pre-candidate routing taken from the committed `compose.development.yaml` label. It requires, in the rolled-back
state, that a client path such as `/account` no longer returns the application shell, that the backend seam still
answers (`/api/v1/browser/csrf/` returns `204`), that the frontend container has no mounts, and that bounded logs are
clean for both routing states. The rehearsal first passed on **2026-10-12** against the `testing` backend and ran with
the standalone image only; it did not address `development`.

## Procedure `[AUTHORIZATION REQUIRED]`

1. Stop serving the frontend so Traefik drops its routers:

   ```console
   docker stop frontend-fe6vn
   ```

2. Recreate Django from the unmodified project files, which restores its whole-host router labels:

   ```console
   docker compose --project-name localforge-dev --env-file .env.development -f compose.yaml -f compose.development.yaml up --detach --no-deps django-uv5n2
   ```

3. Remove the frontend container and revert the registry patch:

   ```console
   docker rm frontend-fe6vn
   git apply --reverse docs/runbooks/frontend-deployment/registry.candidate.patch
   ```

   If the candidate was already merged into `compose.development.yaml` (deploy step 6), revert that change first and
   use `git revert` on the adopting commit instead of the reverse patch.

4. Optionally remove the image: `docker image rm localforge/frontend:0.1.0`. Skip this if a retry is planned.

## Verification `[AUTHORIZATION REQUIRED]`

```console
make development-health
make docker-audit
make convention-audit-development
curl.exe -s -w "\n%{http_code}\n" -H "Host: localforge.localhost" http://127.0.0.1:8080/health/
curl.exe -s -o NUL -w "%{http_code}\n" -H "Host: localforge.localhost" http://127.0.0.1:8080/api/v1/browser/csrf/
curl.exe -s -o NUL -w "%{http_code}\n" -H "Host: localforge.localhost" http://127.0.0.1:8080/account
```

Required: health and both audits pass with no `frontend-fe6vn` anywhere; `/health/` is `200` ready; the CSRF route is
`204`; `/account` is answered by Django (a `404`), not the application shell. Inspect a bounded window from the time of
step 1 for `django-uv5n2` and `traefik-tk2jp` with the same rule as deployment: no unexplained `WARNING`, `ERROR`, or
`CRITICAL`, restart, or unhealthy state.

## What is not rolled back

Nothing else changed: users, sessions, and tokens live in the backend and are unaffected. Browser sessions held by
clients stay valid because cookie names and the `/api/v1/browser/` contract are unchanged by this deployment.
