# 77: Clean checkout and runtime verification

**What to build:** Prove the restructured monorepo works from a clean checkout through backend quality, Docker, SOPS,
security, and runtime truth gates.

**Blocked by:**

- [76: CI, hooks, and documentation path migration](76-ci-hooks-and-documentation-paths.md)

**Governing sources:**

- [Phase 10 specification](../../docs/architecture/phase-10-monorepo-spec.md)
- [Runtime truth rule](../../AGENTS.md)
- [Security audit](../../docs/security/security-audit.md)
- [Build plan](../../docs/build/plan.md)

**Status:** done

- [x] From a clean checkout, sync backend dependencies and run all static and documentation gates.
- [x] Decrypt both committed environment files and prove parity with the generated local environment contract.
- [x] Rebuild and exercise both Compose environments, public health, affected services, Docker ownership, and bounded
      logs.
- [x] Prove no stale path, duplicate backend project, untracked secret, or accidental frontend file remains.

Evidence: The root `Makefile` is the sole operator entry point. In a clean checkout, `make sync`, `make preflight`,
`make setup`, `make pre-commit`, and `make check` passed. The complete gate passed Django checks, OpenAPI, Ruff,
structured documentation, mypy, ty, architecture tests, 2,158 parallel core tests at 100% branch coverage, 23
security-timing tests, host integration, and five registration-timing stability attempts. `make secrets-decrypt`
recovered both committed encrypted environments; generated development, testing, and testing-host files matched the
documented key contract with no placeholders. `make environments-setup` rebuilt the three local images, recreated
both Compose projects, waited for every required health check, and passed the project-scoped Docker inventory.
`make development-health` and `make testing-health` passed; the deployed `/health/` seam returned HTTP 200 with
readiness `ready`. `make docker-audit`, `make convention-audit`, and `make security-audit` passed, including image
policy, dependency, deployment, history, exposure, and runtime-secret checks. Fresh bounded logs for every container
in `localforge-dev` and `localforge-test` contained no unexplained warning, error, critical, restart, or unhealthy
finding. Repository scans found no deleted wrapper reference, duplicate backend project, untracked secret, or
frontend implementation.
