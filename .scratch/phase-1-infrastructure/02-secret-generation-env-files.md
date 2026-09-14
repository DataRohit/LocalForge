# 02: Secret generation and per-environment env files

**What to build:** a developer with a fresh clone runs one command and ends up with complete, valid, git-ignored
environment files for both environments, containing strong unique secrets that nobody typed by hand. Re-running it
is safe and does not invalidate a running stack.

**Blocked by:** 01.

**Status:** done

- [x] A committed example file lists every variable in the inventory in `docs/platform/conventions.md` with
      placeholder values and no real secrets.
- [x] The generator produces the development file, the testing file, and the host-mode testing variant, each with
      the correct per-environment overrides.
- [x] Every secret is generated with a cryptographically secure source, and no two services share a credential.
- [x] A default run never overwrites an existing value; it only fills in variables that are absent.
- [x] A forced run regenerates everything and warns which volumes hold credential-derived state and must be
      recreated.
- [x] The generator refuses to write over a Git-tracked file and exits with the documented code.
- [x] No secret is ever printed to stdout or written to a log.
- [x] Git ignores the generated files and does not ignore the example or the encrypted variants.
- [x] An encrypt/decrypt helper round-trips an environment file, and exits with a clear message when the encryption
      tooling is absent rather than failing obscurely.
- [x] Unit tests cover idempotency, the refusal cases, and every exit code.
