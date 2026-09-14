# 01: Prerequisite gate and preflight script

**What to build:** a single command that tells a developer whether this machine can build the platform, and exactly
what to install if it cannot. Running it prints a pass/fail table covering every tool, version floor, and resource
the build needs, and exits non-zero when a required item is missing.

**Blocked by:** None (can start immediately).

**Status:** done

- [x] The script checks every item in `docs/build/prerequisites.md`, including Docker, Compose, Git, the project
      virtualenv interpreter, uv, kubectl, host CPU and memory, and free disk.
- [x] Optional items are reported as warnings, not failures, and the output says when each one starts to matter.
- [x] `--json` emits the same result machine-readably.
- [x] Exit codes follow the contract in `docs/platform/conventions.md`: `0` all required pass, `1` at least one
      required failure.
- [x] Output names the remediation command for every failing item.
- [x] The script runs correctly under the project virtualenv interpreter and does not assume the `PATH` interpreter
      is the right one.
- [x] The script is covered by unit tests that fake both a passing and a failing environment.
