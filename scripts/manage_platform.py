"""Operator commands for the LocalForge environments.

Presents setup and symmetric development/testing workflows behind one cross-platform interface,
keeping Compose and secret-management details out of Poe tasks and onboarding instructions.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Protocol

from scripts.gen_secrets import parse_env_text

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
ENVIRONMENT_HEALTH_TIMEOUT_SECONDS = 600
HEALTH_POLL_SECONDS = 2
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
DECRYPT_FALLBACK_CODES = frozenset({2, 3})
TESTING_SERVICES = (
    "postgres-tp8vn",
    "valkey-cache-tv4kq",
    "valkey-channels-tv9zw",
    "rabbitmq-tr6mc",
    "seaweedfs-ts3jd",
    "django-test-dt5qx",
)
DEVELOPMENT_FOUNDATION = (
    "postgres-pg3ka",
    "postgres-replica-pg6vy",
    "pgbackrest-pb2wj",
    "valkey-cache-vc5tn",
    "valkey-channels-vh8dm",
    "rabbitmq-rq4sx",
    "mailpit-mp6gb",
    "seaweedfs-sw9cr",
    "loki-lk3ny",
)
DEVELOPMENT_AFTER_DJANGO = (
    "traefik-tk2jp",
    "pgadmin-pa7fe",
    "cadvisor-cv8mh",
    "postgres-exporter-pe4rk",
    "valkey-cache-exporter-ve7ts",
    "valkey-channels-exporter-vx4nq",
    "alloy-al6wz",
    "prometheus-pm5db",
    "grafana-gf7qv",
)
DEVELOPMENT_CONTAINERS = frozenset(
    (*DEVELOPMENT_FOUNDATION, "django-uv5n2", *DEVELOPMENT_AFTER_DJANGO)
)
TESTING_CONTAINERS = frozenset(TESTING_SERVICES)
TESTING_OPTIONAL_CONTAINERS = frozenset({"mailpit-tm7bh"})
CONTAINER_PROJECTS = {
    **dict.fromkeys(DEVELOPMENT_CONTAINERS, "localforge-dev"),
    **dict.fromkeys(TESTING_CONTAINERS, "localforge-test"),
    **dict.fromkeys(TESTING_OPTIONAL_CONTAINERS, "localforge-test"),
}
REQUIRED_CONTAINERS = DEVELOPMENT_CONTAINERS | TESTING_CONTAINERS
CONTAINER_IMAGES = {
    "traefik-tk2jp": "traefik:v3.7.13",
    "django-uv5n2": "localforge/django:0.1.0",
    "postgres-pg3ka": "localforge/pgbackrest:18.6",
    "postgres-replica-pg6vy": "postgres:18.6",
    "pgbackrest-pb2wj": "localforge/pgbackrest:18.6",
    "pgadmin-pa7fe": "dpage/pgadmin4:9.17",
    "valkey-cache-vc5tn": "valkey/valkey:9.1.2",
    "valkey-channels-vh8dm": "valkey/valkey:9.1.2",
    "rabbitmq-rq4sx": "rabbitmq:4.3.5-management",
    "mailpit-mp6gb": "axllent/mailpit:v1.31.1",
    "seaweedfs-sw9cr": "chrislusf/seaweedfs:4.46",
    "prometheus-pm5db": "prom/prometheus:v3.14.0",
    "grafana-gf7qv": "grafana/grafana-oss:13.0.2",
    "loki-lk3ny": "grafana/loki:3.7.7",
    "alloy-al6wz": "grafana/alloy:v1.19.2",
    "cadvisor-cv8mh": "ghcr.io/google/cadvisor:v0.60.5",
    "postgres-exporter-pe4rk": "quay.io/prometheuscommunity/postgres-exporter:v0.20.1",
    "valkey-cache-exporter-ve7ts": "oliver006/redis_exporter:v1.91.1",
    "valkey-channels-exporter-vx4nq": "oliver006/redis_exporter:v1.91.1",
    "django-test-dt5qx": "localforge/django-test:0.1.0",
    "postgres-tp8vn": "postgres:18.6",
    "valkey-cache-tv4kq": "valkey/valkey:9.1.2",
    "valkey-channels-tv9zw": "valkey/valkey:9.1.2",
    "rabbitmq-tr6mc": "rabbitmq:4.3.5",
    "seaweedfs-ts3jd": "chrislusf/seaweedfs:4.46",
    "mailpit-tm7bh": "axllent/mailpit:v1.31.1",
}
PROBELESS_CONTAINERS = frozenset(
    {
        "loki-lk3ny",
        "valkey-cache-exporter-ve7ts",
        "valkey-channels-exporter-vx4nq",
    }
)
DEVELOPMENT_NETWORKS = frozenset(
    {
        "edge-net-ne2vk",
        "access-net-ha4mz",
        "app-net-na6hy",
        "data-net-nd9pc",
        "obsv-net-nb4xt",
    }
)
TESTING_NETWORKS = frozenset({"access-net-ht6pn", "app-net-nt5rk", "data-net-nt8fq"})
NETWORK_PROJECTS = {
    **dict.fromkeys(DEVELOPMENT_NETWORKS, "localforge-dev"),
    **dict.fromkeys(TESTING_NETWORKS, "localforge-test"),
}
DEVELOPMENT_VOLUMES = frozenset(
    {
        "postgres-pg3ka-data",
        "postgres-pg3ka-socket",
        "postgres-replica-pg6vy-data",
        "pgbackrest-pb2wj-repo",
        "pgadmin-pa7fe-data",
        "valkey-cache-vc5tn-data",
        "valkey-channels-vh8dm-data",
        "rabbitmq-rq4sx-data",
        "mailpit-mp6gb-data",
        "seaweedfs-sw9cr-data",
        "prometheus-pm5db-data",
        "grafana-gf7qv-data",
        "loki-lk3ny-data",
        "alloy-al6wz-data",
        "django-uv5n2-static",
    }
)
TESTING_VOLUMES = frozenset(
    {
        "postgres-tp8vn-data",
        "valkey-cache-tv4kq-data",
        "valkey-channels-tv9zw-data",
        "rabbitmq-tr6mc-data",
        "seaweedfs-ts3jd-data",
    }
)
TESTING_OPTIONAL_VOLUMES = frozenset({"mailpit-tm7bh-data"})
VOLUME_PROJECTS = {
    **dict.fromkeys(DEVELOPMENT_VOLUMES, "localforge-dev"),
    **dict.fromkeys(TESTING_VOLUMES, "localforge-test"),
    **dict.fromkeys(TESTING_OPTIONAL_VOLUMES, "localforge-test"),
}
REQUIRED_VOLUMES = DEVELOPMENT_VOLUMES | TESTING_VOLUMES
LOCALFORGE_IMAGES = frozenset(
    {
        "localforge/django:0.1.0",
        "localforge/django-test:0.1.0",
        "localforge/pgbackrest:18.6",
    }
)

HELP_TEXT = """\
LocalForge environment commands

First setup
  uv sync --all-groups --frozen
  uv run poe setup                 Check prerequisites and prepare environment files
  uv run poe environments-setup    Bootstrap missing images, start, time, and audit both
  uv run poe docker-clean-check     Require zero LocalForge Docker resources
  uv run poe docker-audit           Verify running resources, labels, health, and ownership

Development (each safe default preserves volumes)
  uv run poe development-build     Build local development images
  uv run poe development-up        Build if needed and start the full stack
  uv run poe development-rebuild   Rebuild and recreate the full stack
  uv run poe development-status    Show service state
  uv run poe development-health    Verify service and application readiness
  uv run poe development-logs      Show recent logs
  uv run poe development-down      Stop the stack and preserve data
  uv run poe development-reset     DESTRUCTIVE: remove data and rebuild from scratch

Testing (each safe default preserves volumes)
  uv run poe testing-build          Build the test runner image
  uv run poe testing-up             Build if needed and start the headless environment
  uv run poe testing-rebuild        Rebuild and recreate the headless environment
  uv run poe testing-status         Show service state
  uv run poe testing-health         Verify testing dependency readiness
  uv run poe testing-logs           Show recent logs
  uv run poe testing-test-container Run the complete suite in the test container
  uv run poe testing-test-host      Run the complete suite from the host
  uv run poe testing-test-both      Run container mode, then host mode
  uv run poe testing-verify         Rebuild, run both modes, then stop on success
  uv run poe testing-down           Stop the stack and preserve data
  uv run poe testing-reset          DESTRUCTIVE: remove data and rebuild from scratch

Secrets
  uv run poe secrets-generate      Create or top up local environment files
  uv run poe secrets-decrypt       Recover committed encrypted environment files

Options
  uv run poe environments-setup --proxy-only
  uv run poe development-up --proxy-only
  uv run poe development-rebuild --proxy-only
  uv run poe development-reset --proxy-only
                                     Do not publish Django host port 8000
                                     rejected for every other command
  --confirm-destroy-data           Required by direct reset invocations
  --follow                         Follow logs after printing the recent tail
"""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Outcome of one external command.

    Carries the exit code and captured output needed by health polling while ordinary commands
    inherit the terminal streams for immediate operator feedback.

    Attributes:
        code: Process exit status.
        output: Captured standard output, or an empty string.

    Members:
        None.
    """

    code: int
    output: str


class Runner(Protocol):
    """External command seam used by the operator interface.

    Allows command composition and failure propagation to be tested without invoking Docker,
    Python, uv, or Poe. Inherits from ``Protocol`` so fabricated and host adapters satisfy the
    interface structurally.

    Members:
        run: Execute one argument vector with an optional environment.
    """

    def run(
        self,
        command: tuple[str, ...],
        environment: dict[str, str] | None = None,
        *,
        capture: bool = False,
    ) -> CommandResult:
        """Execute one command.

        Runs the supplied argument vector with the requested output behavior and never raises for
        an ordinary non-zero process result.

        Arguments:
            command: Argument vector to execute.
            environment: Optional complete child-process environment.
            capture: Whether to capture standard output.

        Returns:
            Process exit code and optional output.
        """


class HostRunner:
    """Run operator commands on the current host.

    Implements Runner with bounded subprocess calls rooted at the repository, preserving live
    output unless a caller explicitly requests capture.

    Attributes:
        root: Repository working directory.

    Members:
        run: Execute one command.
    """

    def __init__(self, root: Path) -> None:
        """Bind command execution to a repository.

        Stores the working directory every Docker, uv, Poe, and helper invocation must use.
        The binding keeps callers independent of the shell's current directory.

        Arguments:
            root: Repository working directory.

        Returns:
            None.
        """
        self.root = root

    def run(
        self,
        command: tuple[str, ...],
        environment: dict[str, str] | None = None,
        *,
        capture: bool = False,
    ) -> CommandResult:
        """Execute one bounded subprocess.

        Preserves live terminal output by default and captures standard output only for state
        polling that must interpret it.

        Arguments:
            command: Argument vector to execute.
            environment: Optional complete child-process environment.
            capture: Whether to capture standard output.

        Returns:
            Process exit code and optional output.
        """
        try:
            completed = subprocess.run(
                command,
                cwd=self.root,
                env=environment,
                capture_output=capture,
                text=True,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            print(f"command failed to start: {error}")
            return CommandResult(code=EXIT_FAILED, output="")

        return CommandResult(
            code=completed.returncode,
            output=completed.stdout.strip() if capture else "",
        )


@dataclass(frozen=True, slots=True)
class EnvironmentSpec:
    """Describe one Compose environment.

    Holds filenames and service selections that vary between development and testing while every
    operation reuses the same command-building implementation.

    Attributes:
        name: Stable environment name.
        env_file: Plaintext environment file Compose loads.
        overlay: Environment-specific Compose overlay.
        project: Compose project label used for status inspection.
        services: Services started by the ordinary up operation, or empty for every service.

    Members:
        None.
    """

    name: str
    env_file: str
    overlay: str
    project: str
    services: tuple[str, ...]


DEVELOPMENT = EnvironmentSpec(
    name="development",
    env_file=".env.development",
    overlay="compose.development.yaml",
    project="localforge-dev",
    services=(),
)
TESTING = EnvironmentSpec(
    name="testing",
    env_file=".env.testing",
    overlay="compose.testing.yaml",
    project="localforge-test",
    services=TESTING_SERVICES,
)
LOCAL_BUILD_TARGETS = (
    ("localforge/pgbackrest:18.6", DEVELOPMENT, "postgres-pg3ka"),
    ("localforge/django:0.1.0", DEVELOPMENT, "django-uv5n2"),
    ("localforge/django-test:0.1.0", TESTING, "django-test-dt5qx"),
)
EXTERNAL_PULL_TARGETS = (
    ("traefik:v3.7.13", DEVELOPMENT, "traefik-tk2jp"),
    ("postgres:18.6", DEVELOPMENT, "postgres-replica-pg6vy"),
    ("dpage/pgadmin4:9.17", DEVELOPMENT, "pgadmin-pa7fe"),
    ("valkey/valkey:9.1.2", DEVELOPMENT, "valkey-cache-vc5tn"),
    ("rabbitmq:4.3.5-management", DEVELOPMENT, "rabbitmq-rq4sx"),
    ("axllent/mailpit:v1.31.1", DEVELOPMENT, "mailpit-mp6gb"),
    ("chrislusf/seaweedfs:4.46", DEVELOPMENT, "seaweedfs-sw9cr"),
    ("prom/prometheus:v3.14.0", DEVELOPMENT, "prometheus-pm5db"),
    ("grafana/grafana-oss:13.0.2", DEVELOPMENT, "grafana-gf7qv"),
    ("grafana/loki:3.7.7", DEVELOPMENT, "loki-lk3ny"),
    ("grafana/alloy:v1.19.2", DEVELOPMENT, "alloy-al6wz"),
    ("ghcr.io/google/cadvisor:v0.60.5", DEVELOPMENT, "cadvisor-cv8mh"),
    (
        "quay.io/prometheuscommunity/postgres-exporter:v0.20.1",
        DEVELOPMENT,
        "postgres-exporter-pe4rk",
    ),
    ("oliver006/redis_exporter:v1.91.1", DEVELOPMENT, "valkey-cache-exporter-ve7ts"),
    ("rabbitmq:4.3.5", TESTING, "rabbitmq-tr6mc"),
)
DEVELOPMENT_LOCAL_BUILD_TARGETS = LOCAL_BUILD_TARGETS[:2]
TESTING_LOCAL_BUILD_TARGETS = LOCAL_BUILD_TARGETS[2:]
DEVELOPMENT_EXTERNAL_PULL_TARGETS = tuple(
    target for target in EXTERNAL_PULL_TARGETS if target[1] is DEVELOPMENT
)
TESTING_EXTERNAL_PULL_TARGETS = (
    ("postgres:18.6", TESTING, "postgres-tp8vn"),
    ("valkey/valkey:9.1.2", TESTING, "valkey-cache-tv4kq"),
    ("rabbitmq:4.3.5", TESTING, "rabbitmq-tr6mc"),
    ("chrislusf/seaweedfs:4.46", TESTING, "seaweedfs-ts3jd"),
)
SETUP_IMAGES = frozenset(
    image for image, _spec, _service in (*LOCAL_BUILD_TARGETS, *EXTERNAL_PULL_TARGETS)
)


def python_command(root: Path, script: str, *arguments: str) -> tuple[str, ...]:
    """Build one helper-script command using the active uv interpreter.

    Reuses the interpreter that launched this command module so no bare system Python can enter an
    operational workflow.

    Arguments:
        root: Repository root containing the script.
        script: Script filename beneath ``scripts``.
        *arguments: Command-line values passed to the script.

    Returns:
        Complete argument vector.
    """
    return (sys.executable, str(root / "scripts" / script), *arguments)


def compose_command(
    spec: EnvironmentSpec,
    *arguments: str,
    proxy_only: bool = False,
) -> tuple[str, ...]:
    """Build one environment-scoped Compose command.

    Centralizes the env-file and two-file Compose prefix so callers cannot accidentally target the
    other environment or an implicit default project.

    Arguments:
        spec: Environment whose project and files the command targets.
        *arguments: Compose operation and its arguments.
        proxy_only: Whether to include the development port-suppression overlay.

    Returns:
        Complete Docker Compose argument vector.

    Raises:
        ValueError: If the development-only proxy override is requested for testing.
    """
    if proxy_only and spec is not DEVELOPMENT:
        msg = "--proxy-only is development only"
        raise ValueError(msg)

    prefix: tuple[str, ...] = (
        "docker",
        "compose",
        "--project-name",
        spec.project,
        "--env-file",
        spec.env_file,
        "-f",
        "compose.yaml",
        "-f",
        spec.overlay,
    )
    if proxy_only:
        prefix = (*prefix, "-f", "compose.proxy-only.yaml")

    return (*prefix, *arguments)


def run_steps(
    runner: Runner,
    steps: Sequence[tuple[str, ...]],
    *,
    environment: dict[str, str] | None = None,
) -> int:
    """Run commands in order and stop at the first failure.

    Propagates the original non-zero code and never runs a later state-changing step after an
    earlier prerequisite failed.

    Arguments:
        runner: External command adapter.
        steps: Commands to execute.
        environment: Optional complete child-process environment.

    Returns:
        Zero when every command passes, otherwise the first non-zero code.
    """
    for command in steps:
        result = runner.run(command, environment)
        if result.code != EXIT_OK:
            return result.code

    return EXIT_OK


def require_environment_file(root: Path, spec: EnvironmentSpec) -> int:
    """Require one plaintext environment file before Docker is invoked.

    Produces one actionable setup instruction instead of letting Compose fail with a missing-file
    diagnostic after work has already begun.

    Arguments:
        root: Repository root.
        spec: Environment whose file is required.

    Returns:
        Zero when present, otherwise the usage exit code.
    """
    path = root / spec.env_file
    if path.is_file():
        return EXIT_OK

    print(f"{path.name} is missing; run `uv run poe setup` first")
    return EXIT_USAGE


def setup(root: Path, runner: Runner) -> int:
    """Check prerequisites and prepare all plaintext environment files.

    Prefers committed encrypted material when a plaintext file is absent and decryption is
    available, then uses the idempotent generator to fill any missing variables and testing-host
    values without replacing existing credentials.

    Arguments:
        root: Repository root.
        runner: External command adapter.

    Returns:
        Zero on success, otherwise the first helper failure.
    """
    preflight = runner.run(python_command(root, "preflight.py"))
    if preflight.code != EXIT_OK:
        return preflight.code

    for spec in (DEVELOPMENT, TESTING):
        if (root / spec.env_file).is_file():
            continue
        encrypted = root / f"{spec.env_file}.sops"
        if not encrypted.is_file():
            continue
        result = runner.run(
            python_command(
                root,
                "sops_env.py",
                "--mode",
                "decrypt",
                "--environment",
                spec.name,
            )
        )
        if result.code not in DECRYPT_FALLBACK_CODES | {EXIT_OK}:
            return result.code
        if result.code in DECRYPT_FALLBACK_CODES:
            print(f"{spec.name}: encrypted values unavailable; generating local credentials")

    return runner.run(python_command(root, "gen_secrets.py", "--environment", "all")).code


def generate_secrets(root: Path, runner: Runner) -> int:
    """Create or top up every local environment file.

    Delegates generation to the sole repository authority, preserving all existing values unless
    that helper is explicitly invoked with its own destructive option.

    Arguments:
        root: Repository root.
        runner: External command adapter.

    Returns:
        Generator exit code.
    """
    return runner.run(python_command(root, "gen_secrets.py", "--environment", "all")).code


def decrypt_secrets(root: Path, runner: Runner) -> int:
    """Decrypt both committed environment artifacts and create host testing values.

    Stops on the first decryption failure, then uses the generator only to fill derived or newly
    introduced variables after both encrypted sources are available.

    Arguments:
        root: Repository root.
        runner: External command adapter.

    Returns:
        Zero on success, otherwise the first helper failure.
    """
    steps = [
        python_command(
            root,
            "sops_env.py",
            "--mode",
            "decrypt",
            "--environment",
            spec.name,
        )
        for spec in (DEVELOPMENT, TESTING)
    ]
    code = run_steps(runner, steps)
    if code != EXIT_OK:
        return code

    return runner.run(python_command(root, "gen_secrets.py", "--environment", "all")).code


def seed_storage(root: Path, runner: Runner, spec: EnvironmentSpec) -> int:
    """Verify the environment's media bucket through its host endpoint.

    Uses the existing idempotent storage helper and the registered published port for the selected
    environment.

    Arguments:
        root: Repository root.
        runner: External command adapter.
        spec: Environment whose bucket is required.

    Returns:
        Storage helper exit code.
    """
    endpoint = "http://127.0.0.1:8333" if spec is DEVELOPMENT else "http://127.0.0.1:28333"
    return runner.run(
        python_command(
            root,
            "seed_storage.py",
            "--environment",
            spec.name,
            "--endpoint",
            endpoint,
        )
    ).code


def build(
    runner: Runner,
    spec: EnvironmentSpec,
    *,
    clean: bool = False,
    services: Sequence[str] | None = None,
) -> int:
    """Build local images for one environment.

    Reuses Docker's layer cache on ordinary builds and refreshes pinned bases only for the
    explicitly destructive reset workflow.

    Arguments:
        runner: External command adapter.
        spec: Environment to build.
        clean: Whether to disable cache and refresh pinned bases.
        services: Optional representative build targets.

    Returns:
        Compose build exit code.
    """
    arguments = ["build"]
    if clean:
        arguments.extend(("--no-cache", "--pull"))
    selected = services
    if selected is None:
        selected = (
            ("postgres-pg3ka", "django-uv5n2") if spec is DEVELOPMENT else ("django-test-dt5qx",)
        )
    arguments.extend(selected)

    return runner.run(compose_command(spec, *arguments)).code


def ordinary_up(  # noqa: PLR0913
    root: Path,
    runner: Runner,
    spec: EnvironmentSpec,
    *,
    recreate: bool,
    proxy_only: bool = False,
    wait: bool = True,
    verify_storage: bool = True,
) -> int:
    """Start one environment through its ordinary Compose path.

    Waits for Compose health before verifying object storage, and starts the complete default
    service set for the selected environment.

    Arguments:
        root: Repository root.
        runner: External command adapter.
        spec: Environment to start.
        recreate: Whether to force container recreation.
        proxy_only: Whether to suppress Django's direct host port through a Compose override.
        wait: Whether Compose should wait for service health.
        verify_storage: Whether to verify the media bucket after startup.

    Returns:
        Zero when startup and storage verification pass.
    """
    arguments = ["up", "-d"]
    if wait:
        arguments.extend(("--wait", "--wait-timeout", str(ENVIRONMENT_HEALTH_TIMEOUT_SECONDS)))
    arguments.append("--no-build")
    if recreate:
        arguments.append("--force-recreate")
    arguments.append("--remove-orphans")
    arguments.extend(spec.services)
    code = runner.run(compose_command(spec, *arguments, proxy_only=proxy_only)).code
    if code != EXIT_OK:
        return code

    return seed_storage(root, runner, spec) if verify_storage else EXIT_OK


def up(  # noqa: PLR0913
    root: Path,
    runner: Runner,
    spec: EnvironmentSpec,
    *,
    recreate: bool,
    proxy_only: bool,
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> int:
    """Start one environment with its selected publication mode.

    Requires configuration before any Docker operation and chooses the ordinary or explicit
    proxy-only development adapter without changing the caller's command name.

    Arguments:
        root: Repository root.
        runner: External command adapter.
        spec: Environment to start.
        recreate: Whether to recreate containers.
        proxy_only: Whether development should omit the direct host port.
        sleep: Callable pausing between health polls.
        now: Monotonic clock.

    Returns:
        Startup exit code.
    """
    missing = require_environment_file(root, spec)
    if missing != EXIT_OK:
        return missing
    _ = sleep, now
    code = ensure_environment_images(runner, spec)
    if code != EXIT_OK:
        return code
    return ordinary_up(
        root,
        runner,
        spec,
        recreate=recreate,
        proxy_only=proxy_only,
    )


def rebuild(  # noqa: PLR0913
    root: Path,
    runner: Runner,
    spec: EnvironmentSpec,
    *,
    proxy_only: bool,
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> int:
    """Rebuild and recreate an environment while preserving named volumes.

    Builds source-matched images first and starts containers only after that succeeds, keeping
    ordinary rebuild distinct from destructive reset.

    Arguments:
        root: Repository root.
        runner: External command adapter.
        spec: Environment to rebuild.
        proxy_only: Whether development should omit the direct host port.
        sleep: Callable pausing between health polls.
        now: Monotonic clock.

    Returns:
        Zero when build and recreation pass.
    """
    missing = require_environment_file(root, spec)
    if missing != EXIT_OK:
        return missing
    _ = sleep, now
    code = build(runner, spec)
    if code != EXIT_OK:
        return code

    return ordinary_up(
        root,
        runner,
        spec,
        recreate=True,
        proxy_only=proxy_only,
    )


def down(runner: Runner, spec: EnvironmentSpec, *, volumes: bool) -> int:
    """Stop one environment with optional named-volume deletion.

    Always removes orphans but preserves named data unless the explicit reset workflow requests
    volume deletion.

    Arguments:
        runner: External command adapter.
        spec: Environment to stop.
        volumes: Whether to delete named volumes.

    Returns:
        Compose exit code.
    """
    arguments = ["down"]
    if volumes:
        arguments.append("--volumes")
    arguments.append("--remove-orphans")

    return runner.run(compose_command(spec, *arguments)).code


def reset(  # noqa: PLR0913
    root: Path,
    runner: Runner,
    spec: EnvironmentSpec,
    *,
    confirmed: bool,
    proxy_only: bool,
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> int:
    """Delete environment data and perform a clean no-cache rebuild.

    Refuses unconfirmed calls and sequences scoped teardown, clean build, pinned-image pull, and
    startup without touching Docker resources outside LocalForge.

    Arguments:
        root: Repository root.
        runner: External command adapter.
        spec: Environment to reset.
        confirmed: Whether destructive deletion was explicitly approved.
        proxy_only: Whether development should omit the direct host port.
        sleep: Callable pausing between health polls.
        now: Monotonic clock.

    Returns:
        Zero on success, usage when unconfirmed, or the first failed step.
    """
    if not confirmed:
        print("reset requires --confirm-destroy-data")
        return EXIT_USAGE
    _ = sleep, now
    missing = require_environment_file(root, spec)
    if missing != EXIT_OK:
        return missing
    code = down(runner, spec, volumes=True)
    if code != EXIT_OK:
        return code
    code = build(runner, spec, clean=True)
    if code != EXIT_OK:
        return code
    if spec is DEVELOPMENT:
        code = runner.run(compose_command(spec, "pull", "--ignore-buildable")).code
        if code != EXIT_OK:
            return code

    return ordinary_up(
        root,
        runner,
        spec,
        recreate=False,
        proxy_only=proxy_only,
    )


def status(runner: Runner, spec: EnvironmentSpec) -> int:
    """Show one environment's Compose state.

    Includes stopped containers so an operator can distinguish a missing environment from a
    partially stopped one.

    Arguments:
        runner: External command adapter.
        spec: Environment to inspect.

    Returns:
        Compose exit code.
    """
    return runner.run(
        (
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={spec.project}",
            "--format",
            "{{.Names}}\t{{.Status}}",
        )
    ).code


def record_labels(record: dict[str, object]) -> dict[str, str]:
    """Read string labels from one Docker inspect record.

    Treats absent and malformed label objects as empty so the audit reports ownership failures
    rather than crashing on an unrelated Docker object.

    Arguments:
        record: Decoded Docker inspect object.

    Returns:
        String label mapping.
    """
    config = record.get("Config")
    source = config.get("Labels") if isinstance(config, dict) else record.get("Labels")
    if not isinstance(source, dict):
        return {}

    return {str(key): str(value) for key, value in source.items() if value is not None}


def record_name(record: dict[str, object]) -> str:
    """Read the normalized name from one Docker inspect record.

    Removes Docker's leading slash from container names while leaving resource names unchanged.
    The normalized value can then be compared directly with the frozen registry.

    Arguments:
        record: Decoded Docker inspect object.

    Returns:
        Normalized object name.
    """
    return str(record.get("Name", "")).removeprefix("/")


def normalize_image_name(image: str) -> str:
    """Normalize Docker Hub image aliases without loosening tag matching.

    Removes only Docker Hub's optional registry prefix and official-image ``library`` namespace.
    Other registries, repositories, and tags remain exact.

    Arguments:
        image: Image reference reported by Docker.

    Returns:
        Canonical comparison form.
    """
    normalized = image.removeprefix("docker.io/")
    return normalized.removeprefix("library/")


def evaluate_container_inventory(  # noqa: C901, PLR0912, PLR0915
    containers: Sequence[dict[str, object]],
    *,
    require_complete: bool = True,
) -> list[str]:
    """Evaluate LocalForge container ownership and runtime state.

    Selects containers by frozen name, local image, generated run-name prefix, or LocalForge
    project label so an unlabelled or mislabelled application container cannot escape inspection.

    Arguments:
        containers: Decoded Docker container inspect records.
        require_complete: Whether every registered default service must exist.

    Returns:
        Stable failure descriptions.
    """
    failures: list[str] = []
    service_counts: dict[tuple[str, str], int] = {}
    observed_names: set[str] = set()
    expected_names = set(CONTAINER_PROJECTS)
    for record in containers:
        name = record_name(record)
        labels = record_labels(record)
        config = record.get("Config")
        image = str(config.get("Image", "")) if isinstance(config, dict) else ""
        project = labels.get("com.docker.compose.project", "")
        service = labels.get("com.docker.compose.service", "")
        is_generated_run = "-run-" in name and (
            project in {"localforge-dev", "localforge-test"}
            or any(expected in name for expected in expected_names)
        )
        is_localforge = (
            name in expected_names
            or is_generated_run
            or image.startswith("localforge/")
            or project in {"localforge-dev", "localforge-test"}
        )
        if not is_localforge:
            continue

        observed_names.add(name)
        if is_generated_run:
            failures.append(f"generated-run-name {name}")
        if not project or not service:
            failures.append(
                f"unowned {name} project={project or '<missing>'} service={service or '<missing>'}"
            )
        if labels.get("com.docker.compose.oneoff", "").casefold() == "true":
            failures.append(f"one-off {name}")
        expected_project = CONTAINER_PROJECTS.get(name)
        if expected_project is None and not is_generated_run:
            failures.append(f"stale-container {name}")
        elif expected_project is not None and project != expected_project:
            failures.append(
                f"wrong-project {name} expected={expected_project} actual={project or '<missing>'}"
            )
        if expected_project is not None and service != name:
            failures.append(f"wrong-service {name} expected={name} actual={service or '<missing>'}")
        expected_image = CONTAINER_IMAGES.get(name)
        if expected_image is not None and normalize_image_name(image) != expected_image:
            failures.append(
                f"wrong-image {name} expected={expected_image} "
                f"actual={normalize_image_name(image) or '<missing>'}"
            )
        if service and project:
            key = (project, service)
            service_counts[key] = service_counts.get(key, 0) + 1

        state = record.get("State")
        if isinstance(state, dict):
            status_value = str(state.get("Status", ""))
            health = state.get("Health")
            health_value = str(health.get("Status", "")) if isinstance(health, dict) else "none"
            print(
                f"CONTAINER {name} image={image} status={status_value} health={health_value} "
                f"project={project or '<missing>'} service={service or '<missing>'} "
                f"oneoff={labels.get('com.docker.compose.oneoff', '<missing>')}"
            )
            if require_complete and status_value != "running":
                failures.append(f"not-running {name} status={status_value}")
            if (
                name in CONTAINER_PROJECTS
                and health_value == "none"
                and name not in PROBELESS_CONTAINERS
            ):
                failures.append(f"missing-healthcheck {name}")
            if require_complete and health_value not in {"none", "healthy"}:
                failures.append(f"not-healthy {name} health={health_value}")

    for (project, service), count in sorted(service_counts.items()):
        if count > 1:
            failures.append(f"duplicate-service {project}/{service} count={count}")
    if require_complete:
        failures.extend(
            f"missing-container {name}" for name in sorted(REQUIRED_CONTAINERS - observed_names)
        )

    return failures


def evaluate_environment_health(
    containers: Sequence[dict[str, object]],
    spec: EnvironmentSpec,
) -> list[str]:
    """Evaluate required container state for one environment.

    Requires every registered default service to be running and healthy, except for the exact
    services whose authoritative Compose definitions intentionally omit health checks.

    Arguments:
        containers: Decoded Docker container inspect records.
        spec: Environment whose required services are checked.

    Returns:
        Stable missing, stopped, unhealthy, or missing-healthcheck failures.
    """
    expected = DEVELOPMENT_CONTAINERS if spec is DEVELOPMENT else TESTING_CONTAINERS
    by_name = {record_name(record): record for record in containers}
    failures: list[str] = []
    for name in sorted(expected):
        record = by_name.get(name)
        if record is None:
            failures.append(f"missing-container {name}")
            continue
        state = record.get("State")
        if not isinstance(state, dict):
            failures.append(f"missing-state {name}")
            continue
        status_value = str(state.get("Status", ""))
        if status_value != "running":
            failures.append(f"not-running {name} status={status_value or '<missing>'}")
        health = state.get("Health")
        if not isinstance(health, dict):
            if name not in PROBELESS_CONTAINERS:
                failures.append(f"missing-healthcheck {name}")
            continue
        health_value = str(health.get("Status", ""))
        if health_value != "healthy":
            failures.append(f"not-healthy {name} health={health_value or '<missing>'}")

    return failures


def inspect_environment_health(runner: Runner, spec: EnvironmentSpec) -> int:
    """Inspect every required container for one environment once.

    Uses registered container names directly so missing services fail even when Compose itself
    returns a successful status command.

    Arguments:
        runner: External command adapter.
        spec: Environment whose containers are checked.

    Returns:
        Zero when every required container is running and healthy as configured.
    """
    expected = DEVELOPMENT_CONTAINERS if spec is DEVELOPMENT else TESTING_CONTAINERS
    result = runner.run(
        ("docker", "container", "inspect", *sorted(expected)),
        capture=True,
    )
    records: list[dict[str, object]] = []
    if result.output:
        try:
            records = decode_inspect_output(result.output)
        except (json.JSONDecodeError, ValueError) as error:
            print(f"FAIL docker-inspect {error}")
            return EXIT_FAILED
    failures = evaluate_environment_health(records, spec)
    if result.code != EXIT_OK and not failures:
        failures.append(f"container-inspect {spec.name} exit={result.code}")
    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return EXIT_FAILED

    return EXIT_OK


def evaluate_resource_inventory(
    records: Sequence[dict[str, object]],
    expected_projects: dict[str, str],
    kind: str,
    *,
    require_complete: bool,
    required_names: frozenset[str] | None = None,
) -> list[str]:
    """Evaluate LocalForge network or volume ownership.

    Selects resources by frozen name or LocalForge project label and requires exact project
    ownership without treating unrelated Docker resources as part of this repository.

    Arguments:
        records: Decoded Docker inspect records.
        expected_projects: Frozen resource-name to project mapping.
        kind: Human-readable resource kind.
        require_complete: Whether every expected resource must exist.
        required_names: Required subset when the registry includes profile-gated resources.

    Returns:
        Stable failure descriptions.
    """
    failures: list[str] = []
    observed: set[str] = set()
    for record in records:
        name = record_name(record)
        labels = record_labels(record)
        project = labels.get("com.docker.compose.project", "")
        if name not in expected_projects and project not in {"localforge-dev", "localforge-test"}:
            continue
        observed.add(name)
        expected_project = expected_projects.get(name)
        if expected_project is None:
            failures.append(f"stale-{kind} {name}")
        elif project != expected_project:
            failures.append(
                f"wrong-project-{kind} {name} expected={expected_project} "
                f"actual={project or '<missing>'}"
            )
        print(f"{kind.upper()} {name} project={project or '<missing>'}")

    if require_complete:
        required = set(expected_projects) if required_names is None else set(required_names)
        failures.extend(f"missing-{kind} {name}" for name in sorted(required - observed))

    return failures


def decode_inspect_output(output: str) -> list[dict[str, object]]:
    """Decode one Docker inspect JSON array.

    Rejects unexpected shapes so an incomplete audit cannot report a false clean result.
    Every returned member remains an unmodified object mapping for later validation.

    Arguments:
        output: Captured JSON text.

    Returns:
        Docker records from the array.

    Raises:
        ValueError: If Docker returned a non-array or non-object member.
    """
    decoded = json.loads(output)
    if not isinstance(decoded, list) or not all(isinstance(item, dict) for item in decoded):
        msg = "Docker inspect output is not an object array"
        raise ValueError(msg)

    records: list[dict[str, object]] = []
    for item in decoded:
        record: dict[str, object] = {}
        for key, value in item.items():
            record[str(key)] = value
        records.append(record)

    return records


def inspect_docker_objects(
    runner: Runner,
    list_command: tuple[str, ...],
    inspect_prefix: tuple[str, ...],
) -> tuple[int, list[dict[str, object]]]:
    """List and inspect one Docker object kind.

    Uses Docker IDs from a scoped read-only listing and decodes the inspect response only when
    objects exist.

    Arguments:
        runner: External command adapter.
        list_command: Docker command producing one object ID per line.
        inspect_prefix: Docker inspect command prefix.

    Returns:
        Exit code and decoded records.
    """
    listed = runner.run(list_command, capture=True)
    if listed.code != EXIT_OK:
        return listed.code, []
    identifiers = tuple(line for line in listed.output.splitlines() if line)
    if not identifiers:
        return EXIT_OK, []
    inspected = runner.run((*inspect_prefix, *identifiers), capture=True)
    if inspected.code != EXIT_OK:
        return inspected.code, []
    try:
        return EXIT_OK, decode_inspect_output(inspected.output)
    except (json.JSONDecodeError, ValueError) as error:
        print(f"FAIL docker-inspect {error}")
        return EXIT_FAILED, []


def docker_audit(runner: Runner, *, expect_clean: bool) -> int:
    """Audit all LocalForge Docker ownership at the real CLI seam.

    Inspects containers, networks, volumes, and tagged local images while ignoring unrelated
    objects on the shared Docker engine.

    Arguments:
        runner: External command adapter.
        expect_clean: Whether no LocalForge object should exist.

    Returns:
        Zero for an exact clean or running inventory, otherwise failure.
    """
    code, containers = inspect_docker_objects(
        runner,
        ("docker", "container", "ls", "-aq"),
        ("docker", "container", "inspect"),
    )
    if code != EXIT_OK:
        return code
    code, networks = inspect_docker_objects(
        runner,
        ("docker", "network", "ls", "-q"),
        ("docker", "network", "inspect"),
    )
    if code != EXIT_OK:
        return code
    code, volumes = inspect_docker_objects(
        runner,
        ("docker", "volume", "ls", "-q"),
        ("docker", "volume", "inspect"),
    )
    if code != EXIT_OK:
        return code
    image_result = runner.run(
        ("docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"),
        capture=True,
    )
    if image_result.code != EXIT_OK:
        return image_result.code
    images = {line for line in image_result.output.splitlines() if line.startswith("localforge/")}

    if expect_clean:
        failures = evaluate_container_inventory(containers, require_complete=False)
        failures.extend(
            evaluate_resource_inventory(
                networks,
                NETWORK_PROJECTS,
                "network",
                require_complete=False,
            )
        )
        failures.extend(
            evaluate_resource_inventory(
                volumes,
                VOLUME_PROJECTS,
                "volume",
                require_complete=False,
            )
        )
        local_containers = [
            record_name(record)
            for record in containers
            if record_name(record) in CONTAINER_PROJECTS
            or record_labels(record).get("com.docker.compose.project")
            in {"localforge-dev", "localforge-test"}
        ]
        local_networks = [
            record_name(record)
            for record in networks
            if record_name(record) in NETWORK_PROJECTS
            or record_labels(record).get("com.docker.compose.project")
            in {"localforge-dev", "localforge-test"}
        ]
        local_volumes = [
            record_name(record)
            for record in volumes
            if record_name(record) in VOLUME_PROJECTS
            or record_labels(record).get("com.docker.compose.project")
            in {"localforge-dev", "localforge-test"}
        ]
        failures.extend(f"present-container {name}" for name in local_containers)
        failures.extend(f"present-network {name}" for name in local_networks)
        failures.extend(f"present-volume {name}" for name in local_volumes)
        failures.extend(f"present-image {name}" for name in sorted(images))
    else:
        failures = evaluate_container_inventory(containers)
        failures.extend(
            evaluate_resource_inventory(
                networks,
                NETWORK_PROJECTS,
                "network",
                require_complete=True,
            )
        )
        failures.extend(
            evaluate_resource_inventory(
                volumes,
                VOLUME_PROJECTS,
                "volume",
                require_complete=True,
                required_names=REQUIRED_VOLUMES,
            )
        )
        failures.extend(f"missing-image {name}" for name in sorted(LOCALFORGE_IMAGES - images))
        failures.extend(f"stale-image {name}" for name in sorted(images - LOCALFORGE_IMAGES))

    if failures:
        for failure in sorted(set(failures)):
            print(f"FAIL {failure}")
        return EXIT_FAILED

    print(
        "PASS LocalForge Docker inventory is clean"
        if expect_clean
        else "PASS LocalForge Docker inventory"
    )
    return EXIT_OK


def wait_for_environment_containers(
    runner: Runner,
    spec: EnvironmentSpec,
    *,
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> int:
    """Wait for every registered default container in one environment.

    Reads Docker state directly so timed setup can separate container creation from the health
    wait while accepting running services that intentionally define no health check.

    Arguments:
        runner: External command adapter.
        spec: Environment whose default containers must become ready.
        sleep: Callable pausing between polls.
        now: Monotonic clock.

    Returns:
        Zero when every container is running and healthy where applicable, otherwise failure.
    """
    expected = DEVELOPMENT_CONTAINERS if spec is DEVELOPMENT else TESTING_CONTAINERS
    deadline = now() + ENVIRONMENT_HEALTH_TIMEOUT_SECONDS
    while now() < deadline:
        result = runner.run(
            ("docker", "container", "inspect", *sorted(expected)),
            capture=True,
        )
        if result.code == EXIT_OK:
            try:
                records = decode_inspect_output(result.output)
            except json.JSONDecodeError, ValueError:
                records = []
            if not evaluate_environment_health(records, spec):
                return EXIT_OK
        sleep(HEALTH_POLL_SECONDS)

    print(
        f"{spec.name} containers did not become ready within "
        f"{ENVIRONMENT_HEALTH_TIMEOUT_SECONDS} seconds"
    )
    return EXIT_FAILED


def timed_phase(
    label: str,
    action: Callable[[], int],
    *,
    now: Callable[[], float],
) -> int:
    """Run one setup phase and print its duration.

    Emits only the stable phase name, elapsed seconds, and exit status so timing evidence cannot
    disclose environment values or command arguments.

    Arguments:
        label: Stable phase label.
        action: Operation to execute.
        now: Monotonic clock.

    Returns:
        Operation exit code.
    """
    started = now()
    code = action()
    elapsed = now() - started
    print(f"TIMING phase={label} seconds={elapsed:.3f} status={code}")
    return code


def inspect_images(
    runner: Runner,
    images: frozenset[str],
) -> tuple[int, set[str]]:
    """Inspect selected exact image tags required by bootstrap.

    Reads image IDs directly rather than inferring availability from running containers, preserving
    correct first-run behavior when no container exists.

    Arguments:
        runner: External command adapter.
        images: Exact image tags required by the selected workflow.

    Returns:
        Exit code and the set of exact tags currently present.
    """
    present: set[str] = set()
    local_images = set(LOCALFORGE_IMAGES) & images
    external_images = images - local_images
    for image in sorted(local_images):
        result = runner.run(
            ("docker", "image", "inspect", "--format", "{{.Id}}", image),
            capture=True,
        )
        if result.code == EXIT_OK and result.output.startswith("sha256:"):
            present.add(image)
            print(f"IMAGE present {image} id={result.output.removeprefix('sha256:')[:12]}")
        else:
            print(f"IMAGE missing {image}")

    listed = runner.run(
        ("docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}|{{.ID}}"),
        capture=True,
    )
    if listed.code != EXIT_OK:
        return listed.code, present
    observed_external: dict[str, str] = {}
    for line in listed.output.splitlines():
        reference, separator, image_id = line.partition("|")
        if separator:
            observed_external[normalize_image_name(reference)] = image_id
    for image in sorted(external_images):
        external_id = observed_external.get(image)
        if external_id is None:
            print(f"IMAGE missing {image}")
        else:
            present.add(image)
            print(f"IMAGE present {image} id={external_id[:12]}")

    return EXIT_OK, present


def run_image_targets(
    runner: Runner,
    targets: Sequence[tuple[str, EnvironmentSpec, str]],
    missing: set[str],
    operation: str,
) -> int:
    """Run one Compose image operation for each environment with missing targets.

    Groups representative services by project so every distinct image is pulled or built once,
    including the pgBackRest image shared by two development services.

    Arguments:
        runner: External command adapter.
        targets: Image, environment, and representative service tuples.
        missing: Exact image tags absent from Docker.
        operation: Either ``pull`` or ``build``.

    Returns:
        Zero when every required operation succeeds, otherwise its first failure.

    Raises:
        ValueError: If an unsupported operation is requested.
    """
    if operation not in {"pull", "build"}:
        msg = f"unsupported image operation: {operation}"
        raise ValueError(msg)
    for spec in (DEVELOPMENT, TESTING):
        services = tuple(
            service
            for image, target_spec, service in targets
            if target_spec is spec and image in missing
        )
        if not services:
            continue
        arguments = [operation]
        if operation == "pull":
            arguments.extend(("--policy", "missing"))
        arguments.extend(services)
        code = runner.run(compose_command(spec, *arguments)).code
        if code != EXIT_OK:
            return code

    return EXIT_OK


def ensure_images(
    runner: Runner,
    local_targets: Sequence[tuple[str, EnvironmentSpec, str]],
    external_targets: Sequence[tuple[str, EnvironmentSpec, str]],
) -> int:
    """Provide selected images through unique representative services.

    Pulls only absent external images and builds only absent local image tags through one
    representative service per distinct image.

    Arguments:
        runner: External command adapter.
        local_targets: Local image build targets.
        external_targets: External image pull targets.

    Returns:
        Zero when every selected image tag exists or was created successfully.
    """
    required = frozenset(image for image, _spec, _service in (*local_targets, *external_targets))
    code, present = inspect_images(runner, required)
    if code != EXIT_OK:
        return code
    missing = set(required) - present
    code = run_image_targets(runner, external_targets, missing, "pull")
    if code != EXIT_OK:
        return code

    return run_image_targets(runner, local_targets, missing, "build")


def ensure_setup_images(runner: Runner) -> int:
    """Provide every image needed by two-environment bootstrap.

    Uses one global target list so images shared across environments are inspected and acquired
    once.

    Arguments:
        runner: External command adapter.

    Returns:
        Zero when every exact setup image tag exists or was created successfully.
    """
    return ensure_images(runner, LOCAL_BUILD_TARGETS, EXTERNAL_PULL_TARGETS)


def ensure_environment_images(runner: Runner, spec: EnvironmentSpec) -> int:
    """Provide missing images for one environment without refreshing source.

    Selects the same unique representative-image logic as combined setup while limiting pulls and
    builds to the requested environment.

    Arguments:
        runner: External command adapter.
        spec: Environment whose images are required.

    Returns:
        Zero when every required environment image exists.
    """
    if spec is DEVELOPMENT:
        return ensure_images(
            runner,
            DEVELOPMENT_LOCAL_BUILD_TARGETS,
            DEVELOPMENT_EXTERNAL_PULL_TARGETS,
        )

    return ensure_images(
        runner,
        TESTING_LOCAL_BUILD_TARGETS,
        TESTING_EXTERNAL_PULL_TARGETS,
    )


def start_without_wait(
    root: Path,
    runner: Runner,
    spec: EnvironmentSpec,
    *,
    proxy_only: bool,
) -> int:
    """Create and start one environment without folding readiness into its timing.

    Uses persistent Compose services only and defers health and storage checks to the following
    measured phase.

    Arguments:
        root: Repository root.
        runner: External command adapter.
        spec: Environment to start.
        proxy_only: Whether development should omit the direct host port.

    Returns:
        Compose startup exit code.
    """
    return ordinary_up(
        root,
        runner,
        spec,
        recreate=False,
        proxy_only=proxy_only,
        wait=False,
        verify_storage=False,
    )


def wait_and_verify_environment(  # noqa: PLR0913
    root: Path,
    runner: Runner,
    spec: EnvironmentSpec,
    *,
    http_probe: Callable[[str, str], bool],
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> int:
    """Wait for container health, storage readiness, and the public environment probe.

    Keeps all readiness work in the measured health phase after Compose has returned from its
    create/start operation.

    Arguments:
        root: Repository root.
        runner: External command adapter.
        spec: Environment to verify.
        http_probe: HTTP readiness adapter.
        sleep: Callable pausing between polls.
        now: Monotonic clock.

    Returns:
        Zero when every readiness gate passes.
    """
    code = wait_for_environment_containers(runner, spec, sleep=sleep, now=now)
    if code != EXIT_OK:
        return code
    code = seed_storage(root, runner, spec)
    if code != EXIT_OK:
        return code

    return health(root, runner, spec, http_probe=http_probe)


def environments_setup(  # noqa: PLR0913
    root: Path,
    runner: Runner,
    *,
    proxy_only: bool,
    http_probe: Callable[[str, str], bool],
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> int:
    """Prepare, build, start, and verify both environments with timings.

    Runs no application tests and leaves both Compose projects running, reporting configuration,
    image, startup, health, and total durations without printing environment values.

    Arguments:
        root: Repository root.
        runner: External command adapter.
        proxy_only: Whether development should omit the direct host port.
        http_probe: HTTP readiness adapter.
        sleep: Callable pausing between polls.
        now: Monotonic clock.

    Returns:
        Zero when both environments and the ownership audit pass.
    """
    total_started = now()
    phases: tuple[tuple[str, Callable[[], int]], ...] = (
        ("environment", lambda: setup(root, runner)),
        ("images", lambda: ensure_setup_images(runner)),
        (
            "development-compose-create-start",
            lambda: start_without_wait(
                root,
                runner,
                DEVELOPMENT,
                proxy_only=proxy_only,
            ),
        ),
        (
            "development-health-wait",
            lambda: wait_and_verify_environment(
                root,
                runner,
                DEVELOPMENT,
                http_probe=http_probe,
                sleep=sleep,
                now=now,
            ),
        ),
        (
            "testing-compose-create-start",
            lambda: start_without_wait(root, runner, TESTING, proxy_only=False),
        ),
        (
            "testing-health-wait",
            lambda: wait_and_verify_environment(
                root,
                runner,
                TESTING,
                http_probe=http_probe,
                sleep=sleep,
                now=now,
            ),
        ),
        ("docker-audit", lambda: docker_audit(runner, expect_clean=False)),
    )
    for label, action in phases:
        code = timed_phase(label, action, now=now)
        if code != EXIT_OK:
            total = now() - total_started
            print(f"TIMING phase=total seconds={total:.3f} status={code}")
            return code

    total = now() - total_started
    print(f"TIMING phase=total seconds={total:.3f} status=0")
    return EXIT_OK


def load_environment(root: Path, filename: str) -> dict[str, str]:
    """Load one dotenv file into a child-process environment.

    Overlays parsed values on the current process without mutating it, preserving executable search
    paths while selecting host-mode service addresses.

    Arguments:
        root: Repository root.
        filename: Repository-relative dotenv filename.

    Returns:
        Current environment overlaid with the file's values.
    """
    values = parse_env_text((root / filename).read_text(encoding="utf-8"))

    return {**os.environ, **values}


def probe_http(url: str, host: str) -> bool:
    """Probe one HTTP readiness endpoint.

    Uses an explicit Host header for the Traefik route and converts transport or parsing failures
    into one false readiness result.

    Arguments:
        url: Endpoint URL.
        host: Host header value.

    Returns:
        Whether the endpoint answered successfully.
    """
    request = urllib.request.Request(url, headers={"Host": host})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return int(response.status) == HTTPStatus.OK
    except OSError, ValueError, urllib.error.URLError:
        return False


def health(
    root: Path,
    runner: Runner,
    spec: EnvironmentSpec,
    *,
    http_probe: Callable[[str, str], bool],
) -> int:
    """Verify one running environment's readiness.

    Shows Compose state first, then verifies development through the aggregate health route or
    testing through the existing service-specific readiness helper.

    Arguments:
        root: Repository root.
        runner: External command adapter.
        spec: Environment to verify.
        http_probe: HTTP readiness adapter.

    Returns:
        Zero when ready, otherwise failure.
    """
    code = inspect_environment_health(runner, spec)
    if code != EXIT_OK:
        return code
    code = status(runner, spec)
    if code != EXIT_OK:
        return code
    if spec is DEVELOPMENT:
        if http_probe("http://127.0.0.1:8080/health/", "localforge.localhost"):
            print("development is ready")
            return EXIT_OK
        print("development readiness endpoint failed")
        return EXIT_FAILED

    environment = load_environment(root, ".env.testing.host")
    return runner.run(
        python_command(
            root,
            "wait_for_services.py",
            "postgres",
            "valkey-cache",
            "valkey-channels",
            "rabbitmq",
            "seaweedfs",
        ),
        environment,
    ).code


def logs(
    runner: Runner,
    spec: EnvironmentSpec,
    services: Sequence[str],
    *,
    follow: bool,
) -> int:
    """Show recent logs for an environment.

    Applies one bounded default tail while allowing callers to select services and opt into
    streaming without duplicating Compose syntax.

    Arguments:
        runner: External command adapter.
        spec: Environment to inspect.
        services: Optional service names to restrict output.
        follow: Whether to keep following new records.

    Returns:
        Zero when every selected container log command succeeds, otherwise the first failure.
    """
    if follow and len(services) != 1:
        print("--follow requires exactly one container name")
        return EXIT_USAGE
    selected = tuple(services)
    if not selected:
        result = runner.run(
            (
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={spec.project}",
                "--format",
                "{{.Names}}",
            ),
            capture=True,
        )
        if result.code != EXIT_OK:
            return result.code
        selected = tuple(line for line in result.output.splitlines() if line)
        if not selected:
            print(f"no {spec.name} containers found")
            return EXIT_FAILED

    for container in selected:
        arguments = ["docker", "logs", "--tail", "200"]
        if follow:
            arguments.append("--follow")
        arguments.append(container)
        result = runner.run(tuple(arguments))
        if result.code != EXIT_OK:
            return result.code

    return EXIT_OK


def testing_test(  # noqa: PLR0913
    root: Path,
    runner: Runner,
    mode: str,
    *,
    ensure_up: bool,
    http_probe: Callable[[str, str], bool],
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> int:
    """Run one or both complete testing modes.

    Optionally prepares and verifies the testing environment, then delegates container execution
    to Compose and host execution to the existing complete Poe test task.

    Arguments:
        root: Repository root.
        runner: External command adapter.
        mode: ``container``, ``host``, or ``both``.
        ensure_up: Whether to start and verify the testing environment first.
        http_probe: HTTP readiness adapter.
        sleep: Callable pausing between health polls.
        now: Monotonic clock.

    Returns:
        Zero when every requested mode passes, otherwise its first failure.
    """
    if ensure_up:
        code = up(
            root,
            runner,
            TESTING,
            recreate=False,
            proxy_only=False,
            sleep=sleep,
            now=now,
        )
        if code != EXIT_OK:
            return code
        code = health(root, runner, TESTING, http_probe=http_probe)
        if code != EXIT_OK:
            return code

    if mode in {"container", "both"}:
        code = runner.run(
            compose_command(TESTING, "exec", "-T", "django-test-dt5qx", "poe", "test")
        ).code
        if code != EXIT_OK:
            return code
    if mode in {"host", "both"}:
        return runner.run(("uv", "run", "poe", "test")).code

    return EXIT_OK


def testing_verify(
    root: Path,
    runner: Runner,
    *,
    http_probe: Callable[[str, str], bool],
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> int:
    """Rebuild testing, run both modes, and stop after success.

    Leaves a failed environment running for diagnosis, but returns a successful environment to the
    documented stopped state while preserving its named volumes.

    Arguments:
        root: Repository root.
        runner: External command adapter.
        http_probe: HTTP readiness adapter.
        sleep: Callable pausing between health polls.
        now: Monotonic clock.

    Returns:
        Zero on complete success; failures leave the stack running for diagnosis.
    """
    code = rebuild(
        root,
        runner,
        TESTING,
        proxy_only=False,
        sleep=sleep,
        now=now,
    )
    if code != EXIT_OK:
        return code
    code = health(root, runner, TESTING, http_probe=http_probe)
    if code != EXIT_OK:
        return code
    code = testing_test(
        root,
        runner,
        "both",
        ensure_up=False,
        http_probe=http_probe,
        sleep=sleep,
        now=now,
    )
    if code != EXIT_OK:
        return code

    return down(runner, TESTING, volumes=False)


def build_parser() -> argparse.ArgumentParser:
    """Build the flat stable command parser.

    Keeps every Poe alias and direct script invocation on the same names and shared optional
    arguments.

    Arguments:
        None.

    Returns:
        Configured parser.
    """
    commands = (
        "help",
        "setup",
        "environments-setup",
        "docker-audit",
        "docker-clean-check",
        "secrets-generate",
        "secrets-decrypt",
        *(
            f"{environment}-{action}"
            for environment in ("development", "testing")
            for action in ("build", "up", "down", "rebuild", "reset", "status", "health", "logs")
        ),
        "testing-test-container",
        "testing-test-host",
        "testing-test-both",
        "testing-verify",
    )
    parser = argparse.ArgumentParser(description="Operate LocalForge environments.")
    parser.add_argument("command", nargs="?", default="help", choices=commands)
    parser.add_argument("services", nargs="*")
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--confirm-destroy-data", action="store_true")
    parser.add_argument("--proxy-only", action="store_true")

    return parser


def main(  # noqa: C901, PLR0911, PLR0912, PLR0913
    argv: Sequence[str] | None = None,
    *,
    root: Path = REPOSITORY_ROOT,
    runner: Runner | None = None,
    http_probe: Callable[[str, str], bool] = probe_http,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> int:
    """Run the operator command interface.

    Dispatches stable user-facing names to shared environment operations and propagates the first
    failure without converting it into success-shaped output.

    Arguments:
        argv: Command arguments, or ``None`` to read the process arguments.
        root: Repository root.
        runner: External command adapter, or the host implementation by default.
        http_probe: HTTP readiness adapter.
        sleep: Callable pausing between health polls.
        now: Monotonic clock.

    Returns:
        Process exit code.
    """
    arguments = build_parser().parse_args(argv)
    active_runner = runner if runner is not None else HostRunner(root)
    command = arguments.command

    proxy_only_commands = {
        "environments-setup",
        "development-up",
        "development-rebuild",
        "development-reset",
    }
    if arguments.proxy_only and command not in proxy_only_commands:
        print(
            "--proxy-only is accepted only by environments-setup, development-up, "
            "development-rebuild, and development-reset"
        )
        return EXIT_USAGE
    if command == "help":
        print(HELP_TEXT)
        return EXIT_OK
    if arguments.services and not command.endswith("-logs"):
        print("service names are accepted only by log commands")
        return EXIT_USAGE
    if arguments.follow and not command.endswith("-logs"):
        print("--follow is accepted only by log commands")
        return EXIT_USAGE
    if command == "setup":
        return setup(root, active_runner)
    if command == "environments-setup":
        return environments_setup(
            root,
            active_runner,
            proxy_only=arguments.proxy_only,
            http_probe=http_probe,
            sleep=sleep,
            now=now,
        )
    if command == "docker-audit":
        return docker_audit(active_runner, expect_clean=False)
    if command == "docker-clean-check":
        return docker_audit(active_runner, expect_clean=True)
    if command == "secrets-generate":
        return generate_secrets(root, active_runner)
    if command == "secrets-decrypt":
        return decrypt_secrets(root, active_runner)
    if command.startswith("testing-test-"):
        return testing_test(
            root,
            active_runner,
            command.removeprefix("testing-test-"),
            ensure_up=True,
            http_probe=http_probe,
            sleep=sleep,
            now=now,
        )
    if command == "testing-verify":
        return testing_verify(
            root,
            active_runner,
            http_probe=http_probe,
            sleep=sleep,
            now=now,
        )

    environment_name, action = command.split("-", maxsplit=1)
    spec = DEVELOPMENT if environment_name == "development" else TESTING
    missing = require_environment_file(root, spec)
    if missing != EXIT_OK:
        return missing
    if action == "build":
        return build(active_runner, spec)
    if action == "up":
        return up(
            root,
            active_runner,
            spec,
            recreate=False,
            proxy_only=arguments.proxy_only,
            sleep=sleep,
            now=now,
        )
    if action == "down":
        return down(active_runner, spec, volumes=False)
    if action == "rebuild":
        return rebuild(
            root,
            active_runner,
            spec,
            proxy_only=arguments.proxy_only,
            sleep=sleep,
            now=now,
        )
    if action == "reset":
        return reset(
            root,
            active_runner,
            spec,
            confirmed=arguments.confirm_destroy_data,
            proxy_only=arguments.proxy_only,
            sleep=sleep,
            now=now,
        )
    if action == "status":
        return status(active_runner, spec)
    if action == "health":
        return health(root, active_runner, spec, http_probe=http_probe)

    return logs(active_runner, spec, arguments.services, follow=arguments.follow)


if __name__ == "__main__":
    raise SystemExit(main())
