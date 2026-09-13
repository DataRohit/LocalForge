# Contributing

## Principles

- Keep changes focused, reviewable, and reversible.
- Prefer explicit behavior over hidden assumptions.
- Keep secrets and machine-specific state outside version control.
- Update documentation and tests with behavior changes.
- Preserve local-first operation and reproducible Docker workflows.

## Before making a change

Open or reference an issue for changes that affect architecture, security boundaries, stored data, public interfaces, or
operational behavior. Agree on scope before investing in a large implementation.

## Development workflow

1. Create a short-lived branch from the current default branch.
2. Make the smallest cohesive change that solves the stated problem.
3. Add or update tests when implementation files exist.
4. Run `pre-commit run --all-files`.
5. Use the format in [COMMIT_CONVENTION.md](COMMIT_CONVENTION.md).
6. Review the staged diff for secrets, generated files, and unrelated changes.
7. Submit a pull request using the repository template when a shared remote is available.

## Quality expectations

Changes must be deterministic, documented, typed where practical, and designed for failure. New dependencies require a
clear purpose and must be pinned through the dependency-management approach selected for the project. Runtime services
must use least privilege, health checks, bounded resources, and graceful shutdown once container definitions exist.

## Review expectations

Authors are responsible for responding to feedback and keeping changes current. Reviewers evaluate correctness,
security, maintainability, tests, documentation, and operational impact. Approval does not transfer ownership away from
the author.

## Breaking changes

Call out compatibility impact in the pull request and changelog. Include a migration or rollback path before merging.

## Conduct and security

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Do not report vulnerabilities in public issues;
follow [SECURITY.md](SECURITY.md).
