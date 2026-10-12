# Roll back the frontend deployment

Restores the routing that existed before the frontend: Django again owns the whole `localforge.localhost` and
`localforge.datarohit.com` hosts. The frontend is stateless, so **no data is restored, migrated, or lost**.
`frontend-fe6vn` has no volume, secret, or bind mount, and rollback does not touch Postgres, Valkey, RabbitMQ,
SeaweedFS, or any Django volume. Steps marked `[existing development]` address a running `development` environment and
need the user's explicit authorization.

Deployment: [deploy.md](./deploy.md). Background: [README.md](./README.md).

## Rehearsal `[testing only]`

```console
make frontend-image-check
```

The proof starts the real image, applies the routing committed in `compose.development.yaml`, then stops the
disposable Traefik and restarts it with the frontend removed, which returns Django to the whole-host rule that the
frontend's own local router carries. It requires, in the rolled-back state, that a client path such as `/account` no
longer returns the application shell, that the backend seam still answers (`/api/v1/browser/csrf/` returns `204`), that
the frontend container has no mounts, and that bounded logs are clean for both routing states. The rehearsal never
addresses `development`.

## Procedure `[existing development]`

1. Revert the commit that adopted the frontend, so the committed development definition has no `frontend-fe6vn`
   service, no frontend registry entries, and the original Django router labels:

   ```console
   git revert <adopting-commit>
   ```

2. Stop serving the frontend so Traefik drops its routers:

   ```console
   docker stop frontend-fe6vn
   ```

3. Recreate Django from the reverted project files, which restores its whole-host router labels, then remove the
   frontend container:

   ```console
   docker compose --project-name localforge-dev --env-file .env.development -f compose.yaml -f compose.development.yaml up --detach --no-deps django-uv5n2
   docker rm frontend-fe6vn
   ```

4. Optionally remove the image: `docker image rm localforge/frontend:0.1.0`. Skip this if a retry is planned.

## Verification `[existing development]`

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
step 2 for `django-uv5n2` and `traefik-tk2jp` with the same rule as deployment: no unexplained `WARNING`, `ERROR`, or
`CRITICAL`, restart, or unhealthy state.

## What is not rolled back

Nothing else changed: users, sessions, and tokens live in the backend and are unaffected. Browser sessions held by
clients stay valid because cookie names and the `/api/v1/browser/` contract are unchanged by this deployment.
