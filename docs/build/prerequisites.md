# Prerequisites

Authoritative for: what must exist on the machine before phase 4, how to check each item, and what was actually
observed here.

Run this list first, via `backend/scripts/preflight.py`. Everything below was probed on **this machine on 2026-09-13**;
re-verify, because the machine may have moved on.

The supported first-run interface performs that check and prepares local environment files without replacing
existing values:

```console
uv sync --project backend --all-groups --frozen
./localforge.sh help
./localforge.sh environments-setup
```

If port 8000 is already occupied, use `./localforge.sh environments-setup --proxy-only`; the override applies only to
the development project and the testing project remains unchanged.

The combined command reports redacted durations for environment preparation, image work, Compose startup, health
waiting, the Docker ownership audit, and the total. It pulls absent external images, rebuilds every local image
through cached deterministic layers, starts with `--no-build`, and runs no application tests.

Optional or deliberately unused tools appear as `INFO`, not `WARN`. On this project that includes the bare PATH
Python, kind/minikube, and host `psql`; their absence does not weaken a required gate.

Use `./localforge.sh secrets-decrypt` when committed encrypted values must replace absent plaintext files explicitly.
Use `./localforge.sh secrets-generate` to create or top up machine-local values without decrypting.

## 1. Checklist

| # | Requirement | Check | Minimum | Observed 2026-09-13 | If missing |
| --- | --- | --- | --- | --- | --- |
| 1 | Docker Engine | `docker --version` | 27.0 | **29.7.2** (build a7dcaa6) — pass | Docker Desktop for Windows, or Docker Engine + WSL2 |
| 2 | Docker Compose | `docker compose version` | 2.24 | **v5.5.1** — pass | Ships with Docker Desktop; else the `docker-compose-plugin` package |
| 3 | Git | `git --version` | 2.40 | **2.53.0.windows.4** — pass | `git-scm.com` |
| 4 | Python on `PATH` | `python --version` | 3.14 | **3.12.10 — see Section 2** | Not a blocker. Always use `uv run` |
| 5 | Project virtualenv | `backend\.venv\Scripts\python.exe --version` | 3.14 | **3.14.6** — pass | `uv venv --python 3.14 backend/.venv` |
| 6 | uv | `uv --version` | 0.5 | **0.12.1** — pass | `pipx install uv` or the official installer |
| 7 | Host resources | `docker info` | 8 GB RAM, 4 CPU | **16 CPU, 31.3 GiB** — pass | Raise Docker Desktop's limits |
| 8 | Disk | `docker system df`, free space | 20 GB | **C: 277 GB free, Q: 1.5 TB free.** Docker already holds 2.2 GB images and 3.3 GB volumes from other work — pass | Free space, or move Docker's data root |
| 9 | kubectl | `kubectl version --client` | 1.30 | **v1.37.0**, Kustomize v5.8.1 — pass | `winget install Kubernetes.kubectl` |
| 10 | kind *or* minikube | `kind version` / `minikube version` | kind 0.33, minikube 1.39 | **NOT PRESENT** — optional, see Section 3 | `winget install Kubernetes.kind` |
| 11 | SOPS | `sops --version` | 3.13 | **3.13.3** — pass | `winget install --id SecretsOPerationS.SOPS --exact` |
| 12 | age | `age --version` | 1.3 | **1.3.1** — pass | `winget install --id FiloSottile.age --exact` |
| 13 | `psql` on the host | `psql --version` | 16 | **NOT PRESENT** — optional | `docker exec -it postgres-pg3ka psql` needs no host client |

Items 1 through 9 pass, and SOPS and age were installed during ticket 02. Nothing blocks phase 4.

The published winget identifiers are not the ones an obvious guess produces: SOPS is
**`SecretsOPerationS.SOPS`**, not `getsops.sops`, and both IDs need `--exact` to resolve. `winget install
getsops.sops` returns "No package found matching input criteria".

## 2. The Python gap

`python` on `PATH` is **3.12.10**, while `pyproject.toml` requires `>=3.14` and `.python-version` pins `3.14.6`. The
project virtualenv is correctly on 3.14.6, so this is a `PATH` ordering artefact, not a missing dependency.

It changes how every command is run:

- Always use the backend project — `uv run --project backend pytest`, `./localforge.sh check`, and
  `uv run --project backend python backend/scripts/gen_secrets.py`. These commands resolve to 3.14.6.
- A bare `python backend/scripts/gen_secrets.py` silently runs under 3.12.10 and may fail on 3.14-only syntax, or worse,
  succeed while testing the wrong interpreter.
- The container image uses `python:3.14-slim`, so container and virtualenv agree; only the host `PATH` disagrees.

## 3. What is genuinely missing, and when it matters

**SOPS and age (items 11, 12)** were the only missing items on the critical path, and not until the encrypted env
files are first committed. They were installed during ticket 02 and are now present. Phases 4 through 8 run without
them regardless: `gen_secrets.py` writes plaintext `.env` files that are git-ignored, and `sops_env.py` exits `2`
with a clear message if the binaries are absent.

**kind and minikube (item 10)** are not needed at all in this phase. Kubernetes is reasoning-only — see
[../platform/kubernetes-mapping.md](../platform/kubernetes-mapping.md). Note that a Docker network named `kind`
already exists on this machine, so something created a cluster here before; the binary is simply not on `PATH`.

For reference if a cluster is ever wanted: kind **v0.33.0** (2026-08-26) supports Kubernetes v1.34–v1.37 and
defaults to v1.37.0; minikube **v1.39.0** (2026-09-01) is tested against v1.28–v1.37, also defaults to v1.37.0, and
now uses containerd as its default runtime. The installed `kubectl` v1.37.0 matches both defaults. kind's own
release notes disagree with themselves about the default version, so pin the node image by digest.

**`psql` (item 13)** is a convenience. Every documented command uses `docker exec`.

## 4. Network access

The platform is offline at runtime. Three operations need the network **once**:

1. `docker pull` for the 14 pinned images.
2. `docker build` for `localforge/django` and `localforge/pgbackrest` — the latter installs `pgbackrest` from the
   PGDG apt repository that `postgres:18.6` already has configured.
3. `uv sync --project backend` to resolve dependencies.

After that, `docker compose up` must succeed with no external network. `internal: true` on three of the four
development networks enforces it, and audit 6.5 in
[../platform/service-inventory.md](../platform/service-inventory.md) proves it.

`pyproject.toml` pins a Microsoft package feed as the default index and `uv.lock` records that feed's artifact URLs.
Do not change the index and do not disable TLS verification. If resolution fails, run `uv lock` then
`uv sync --project backend --all-groups --frozen`.

## 5. This machine is shared

Docker here already runs **six containers, five volumes, and four networks** belonging to other work, including a
`kind` bridge network.

Two consequences, both load-bearing:

1. Every audit command must filter by `label=com.docker.compose.project=localforge-*`. An unfiltered `docker ps`
   counts other people's containers and fails the naming audit for the wrong reason.
2. **Never run `docker system prune -a`.** It would delete images and volumes that have nothing to do with this
   project. Clean up with `docker compose ... down --volumes --remove-orphans`, which is scoped to the project.
