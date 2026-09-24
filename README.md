# LocalForge

LocalForge is a fully local Django backend platform. Docker Compose runs the database, cache, channel layer, broker,
object storage, mail capture, reverse proxy, monitoring, logging, backup, and application services; `uv` manages the
host Python environment and Poe exposes the supported operator commands.

## Requirements

- Python 3.14 or newer
- `uv`
- Docker Engine with Compose
- SOPS and age when decrypting committed environment files

## Setup

Install all dependency groups, inspect the commands, then build and start both environments:

```console
uv sync --all-groups --frozen
uv run poe help
uv run poe environments-setup
```

On Windows, or whenever another process owns port 8000, use the first-run variant:

```console
uv run poe environments-setup --proxy-only
```

Package downloads use the Microsoft package feed configured in `pyproject.toml`, with system certificate verification
enabled. The lockfile records this feed's artifact URLs so `uv sync --all-groups --frozen` does not attempt direct
downloads from `files.pythonhosted.org`, which fails TLS negotiation on this managed network.

Changing the default index alone does not redirect URLs already recorded in `uv.lock`. After an approved index change,
run `uv lock` and then `uv sync --all-groups --frozen`. Do not disable TLS certificate verification.

`environments-setup` prepares environment files, pulls missing pinned external images, rebuilds all three local
images through cached deterministic layers, starts both Compose projects with `--no-build`, waits for readiness, and
audits Docker ownership. Rebuilding prevents a mutable local tag from referring to source older than the checkout;
Compose recreates a service only when its rebuilt image identity changed. Setup prints redacted phase and total
timings and never runs application tests, coverage, security tests, or the project quality gate.

`setup` performs only the prerequisite and environment-file portion. It prefers committed SOPS files when plaintext
configuration is absent and an age key is available, then uses the idempotent secret generator to top up all three
local environment files. It never prints a secret or replaces an existing value.

Start and verify the complete development environment:

```console
uv run poe development-up
uv run poe development-health
```

The public application entry point is `http://localforge.localhost:8080/`. If another local process owns port 8000,
use `uv run poe development-up --proxy-only`; Traefik remains available while the direct loopback publication is
omitted.

## Environment commands

Run `uv run poe help` for the complete categorized command list.

| Workflow | Development | Testing |
| --- | --- | --- |
| Prepare both | `uv run poe environments-setup` | `uv run poe environments-setup` |
| Start | `uv run poe development-up` | `uv run poe testing-up` |
| Rebuild, preserve data | `uv run poe development-rebuild` | `uv run poe testing-rebuild` |
| Status | `uv run poe development-status` | `uv run poe testing-status` |
| Health | `uv run poe development-health` | `uv run poe testing-health` |
| Recent logs | `uv run poe development-logs` | `uv run poe testing-logs` |
| Follow one service | `uv run poe development-logs --follow django-uv5n2` | `uv run poe testing-logs --follow postgres-tp8vn` |
| Stop, preserve data | `uv run poe development-down` | `uv run poe testing-down` |
| **Destructive reset** | `uv run poe development-reset` | `uv run poe testing-reset` |

The reset commands remove that environment's named volumes and rebuild without cache. They are intentionally named
separately from ordinary rebuilds.

Run `uv run poe docker-audit` for the complete container, label, network, volume, image, and health inventory.
`uv run poe docker-clean-check` is the inverse precondition: it fails if any LocalForge Docker resource remains.
It is optional and belongs only at the start of a deliberate clean-room rehearsal; failure on an already configured
machine is expected and does not mean the running inventory is invalid. The command never deletes anything.
Run `uv run poe convention-audit` for the deeper live registry comparison: exact names, ports, mounts, internal
flags, anonymous-volume rejection, and active offline probes for both projects. Environment-specific variants are
`convention-audit-development` and `convention-audit-testing`.

Run `uv run poe security-audit` for the deployment, full-history secret, locked dependency, exact image
vulnerability policy, protected-dashboard/private-port, broker-account, image-layer, and runtime-log gates.
`security-audit-static` and `security-audit-runtime` split the network/scanner-heavy and live-environment portions.
Accepted findings and review dates are in
[docs/security/security-audit.md](docs/security/security-audit.md).

For a development machine where port 8000 is already owned by another process, `--proxy-only` is accepted by
`environments-setup`, `development-up`, `development-rebuild`, and `development-reset`. Every other command rejects
the option before running a subprocess. The convention audit reads the live Compose configuration labels and
requires port 8000 in normal mode while requiring it absent in proxy-only mode.

## Runtime verification

Tests and static checks are not the final runtime verdict. After a source rebuild or any service-facing change, run:

```console
uv run poe development-health
uv run poe testing-health
uv run poe docker-audit
uv run poe development-logs django-uv5n2
```

Exercise the changed route, socket, task, scheduler, storage, mail, or operator command against the running stack,
then inspect the affected containers over a bounded window beginning before that exercise. Completion requires:

- Every registered container running and every configured health check healthy.
- The aggregate health endpoint returning `ready` with all seven dependency checks `working`.
- The changed public or operator behavior succeeding through the deployed path.
- No unexplained `WARNING`, `ERROR`, or `CRITICAL` record in affected service logs.
- Success being logged as success: a `2xx` response must not emit a failure-level terminal record.

An expected warning must be named and justified in its governing ADR or service inventory. A green test command
does not waive contradictory runtime evidence.

## Testing environments

Environment setup and rebuild commands never execute tests. Test execution is explicit:

```console
uv run poe testing-test-container
uv run poe testing-test-host
uv run poe testing-integration-audit
```

The testing environment keeps `django-test-dt5qx` running as a Compose service. Every complete host or container
command starts the profile-gated Mailpit service before collection, runs all five real SMTP cases as part of the
same complete suite, then clears and removes Mailpit on success or failure. All other tests retain the in-memory
mail backend. Container-mode tests use `docker compose exec`, so Docker Desktop keeps the runner under
`localforge-test` and no `*-run-*` container is created. `testing-verify` remains an explicit full-suite workflow
and is not part of setup:

`testing-integration-audit` starts and verifies the normal testing environment, starts the profile-gated Mailpit
service, proves host and container SMTP messages survive a container recreate and can be deleted, then stops the
real testing cache while both modes observe degraded readiness before restoring healthy state. It clears and removes
the Mailpit container before returning, including after a failed assertion, while preserving the named volume.

```console
uv run poe testing-verify
uv run poe testing-down
```

`testing-test-both` collects complete, core, and security-timing counts in both modes before either suite starts,
then runs both modes even when one fails. It reports collection arithmetic, suite duration, health, scoped Docker
ownership, headless residue, bounded logs, and total wall-clock duration. Exit `10` identifies container-only
failure, `11` host-only failure, `12` failure in both modes, and `13` complete-count drift. Standalone container or
host commands return the original failed child status unchanged. Pytest warnings are errors, and a controller hook
turns any skipped report into test failure.

## Quality checks

The `.agents` directory is excluded from pre-commit file checks, Markdown/YAML linting, Python linting and formatting,
type-check discovery, and test discovery. Its files remain available and can still be tracked in Git.

Run linting and formatting checks:

```console
uv run poe lint
uv run poe format-check
```

Run both type checkers:

```console
uv run poe typecheck
```

The ty task runs the locked release from uv's trusted tool cache, which also works on Windows systems that block
executables launched directly from a project virtual environment.

Run the complete test gate:

```console
uv run poe test
uv run poe test-parallel
```

Both commands enter the SMTP-aware host orchestrator, run the high-parallel core stage at 100% branch coverage,
then run every statistical credential-timing case in the bounded four-worker timing stage. `test-serial`,
`test-fresh`, and `test-integration` also enter the same dependency, Mailpit, post-check, log, and cleanup lifecycle
before delegating to their internal stage tasks. Run the timing stage directly only when dependencies are already
prepared and timing evidence is the only result needed:

```console
uv run poe test-security-timing
```

Run a focused group without applying the whole-suite coverage threshold:

```console
uv run poe test-unit
uv run poe test-integration
```

Focused integration runs exclude statistical timing cases unless the dedicated task is requested. The complete
test command writes the browsable coverage report to `htmlcov/index.html`, machine-readable coverage to
`coverage.xml`, and separate stage results to `test-results/pytest-core.xml` and
`test-results/pytest-security-timing.xml`. Container evidence is the complete one-off runner output and exit status;
the container never receives a writable repository bind mount. No source lines are excluded from core measurement.

Run every local quality check with one command:

```console
uv run poe check
```

List all configured Poe tasks with `uv run poe`, or use the curated operational guide with `uv run poe help`.

## License

LocalForge is available under the [MIT License](LICENSE).

## Repository policies

- [Contributing](CONTRIBUTING.md)
- [Commit convention](COMMIT_CONVENTION.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)
- [Support policy](SUPPORT.md)
- [Governance](GOVERNANCE.md)
- [Changelog](CHANGELOG.md)

## Local repository setup

Install the dependencies, then enable all configured hook types:

```console
uv sync --all-groups
uv run pre-commit install
```

The installation enables pre-commit, commit-message, and pre-push hooks. Pre-push runs mypy, ty, and the full test suite.
Configure the commit template and repository-local identity using [the commit convention](COMMIT_CONVENTION.md).

Validate the complete repository at any time:

```console
uv run pre-commit run --all-files
```
