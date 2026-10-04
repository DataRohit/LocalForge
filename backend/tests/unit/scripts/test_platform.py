"""Unit tests for the LocalForge operator command interface.

Exercises the same command seam Poe exposes to developers, beginning with discoverable help so the
interface can evolve without duplicating operational instructions across callers.
"""

import json
import runpy
import sys
import tomllib
import urllib.error
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, override
from unittest.mock import ANY, MagicMock, patch

import pytest
from scripts import manage_platform as platform

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit
DECRYPT_COMMAND_COUNT = 2
ENVIRONMENT_COUNT = 2
DECRYPT_AND_GENERATE_COMMAND_COUNT = 3
SETUP_DECRYPT_COMMAND_COUNT = 4
STATUS_FAILURE = 4
DECRYPT_FAILURE = 5
GENERATION_FAILURE = 6
PREFLIGHT_FAILURE = 7
IMAGE_FAILURE = 8
REBUILD_FAILURE = 9
LOG_FAILURE = 7
TEST_COLLECTION_COUNT = 2031
TEST_CORE_COUNT = 2008
TEST_TIMING_COUNT = 23
FAILED_STABILITY_ATTEMPTS = 2
STABILITY_HEALTH_CHECK_COUNT = 2


@dataclass
class FakeRunner:
    """Record commands and return configured exit codes.

    Supplies the operator interface's process seam without invoking Docker, uv, or another Python
    process, while retaining environment overrides for host-mode testing assertions.

    Attributes:
        results: Exit codes returned in call order.
        calls: Commands and environment overrides observed.

    Members:
        run: Record one command and return its configured result.
    """

    results: list[int] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    calls: list[tuple[tuple[str, ...], dict[str, str] | None, bool]] = field(default_factory=list)

    def run(
        self,
        command: tuple[str, ...],
        environment: dict[str, str] | None = None,
        *,
        capture: bool = False,
    ) -> platform.CommandResult:
        """Record one command invocation.

        Retains the complete argument vector and optional environment while consuming one fabricated
        result for deterministic command sequencing.

        Arguments:
            command: Argument vector requested by the operator interface.
            environment: Optional complete process environment.
            capture: Whether standard output was requested.

        Returns:
            Configured command result, defaulting to success.
        """
        self.calls.append((command, environment, capture))
        code = self.results.pop(0) if self.results else platform.EXIT_OK
        output = self.outputs.pop(0) if capture and self.outputs else ""

        return platform.CommandResult(code=code, output=output)


@dataclass
class ImageStateRunner(FakeRunner):
    """Model exact Docker image-tag inspection.

    Inherits from FakeRunner and answers image inspection from an explicit present-tag set while
    recording every other generated Docker command normally.

    Attributes:
        present_images: Exact image tags Docker should report as present.

    Members:
        run: Return deterministic image IDs or delegate to FakeRunner.
    """

    present_images: set[str] = field(default_factory=set)

    @override
    def run(
        self,
        command: tuple[str, ...],
        environment: dict[str, str] | None = None,
        *,
        capture: bool = False,
    ) -> platform.CommandResult:
        """Execute one fabricated image or ordinary command.

        Returns a stable image ID only for configured exact tags, letting setup tests distinguish
        complete, partial, and empty local image stores.

        Arguments:
            command: Argument vector requested by the operator interface.
            environment: Optional complete process environment.
            capture: Whether standard output was requested.

        Returns:
            Fabricated command outcome.
        """
        if command[:4] == ("docker", "image", "inspect", "--format"):
            self.calls.append((command, environment, capture))
            image = command[-1]
            if image in self.present_images:
                return platform.CommandResult(code=0, output=f"sha256:{image}")
            return platform.CommandResult(code=1, output="")
        if command[:4] == ("docker", "image", "ls", "--format"):
            self.calls.append((command, environment, capture))
            external = {
                image
                for image, _spec, _service in platform.EXTERNAL_PULL_TARGETS
                if image in self.present_images
            }
            output = "\n".join(f"{image}|image-id" for image in sorted(external))
            return platform.CommandResult(code=0, output=output)

        return super().run(command, environment, capture=capture)


def environment_files(root: Path) -> None:
    """Create the three plaintext environment files a command requires.

    Uses harmless placeholder content because these tests exercise command orchestration rather
    than secret parsing.

    Arguments:
        root: Temporary repository root.

    Returns:
        None.
    """
    for name in (".env.development", ".env.testing", ".env.testing.host"):
        (root / name).write_text("NAME=value\nPOSTGRES_HOST=127.0.0.1\n", encoding="utf-8")


def run_testing_integration_audit(root: Path, runner: platform.Runner) -> int:
    """Run the integration audit with deterministic test adapters.

    Supplies successful readiness so unit tests can focus on command sequencing and failure
    propagation without invoking Docker.

    Arguments:
        root: Temporary repository root.
        runner: Fabricated external command adapter.

    Returns:
        Integration audit exit code.
    """
    return platform.testing_integration_audit(
        root,
        runner,
        http_probe=lambda _url, _host: True,
    )


def container_record(
    name: str,
    *,
    status: str = "running",
    health: str | None = "healthy",
    image: str | None = None,
) -> dict[str, object]:
    """Build one safe Docker inspect fixture.

    Uses the production registry for image and project values while allowing one state field to be
    changed by a focused negative test.

    Arguments:
        name: Registered container name.
        status: Docker runtime status.
        health: Docker health status, or ``None`` when no health check exists.
        image: Optional image override.

    Returns:
        Docker inspect-shaped mapping.
    """
    state: dict[str, object] = {"Status": status}
    if health is not None:
        state["Health"] = {"Status": health}

    return {
        "Name": f"/{name}",
        "Config": {
            "Image": image or platform.CONTAINER_IMAGES[name],
            "Labels": {
                "com.docker.compose.project": platform.CONTAINER_PROJECTS[name],
                "com.docker.compose.service": name,
                "com.docker.compose.oneoff": "False",
            },
        },
        "State": state,
    }


def environment_records(spec: platform.EnvironmentSpec) -> list[dict[str, object]]:
    """Build a complete healthy inspect fixture for one environment.

    Mirrors the required production inventory and omits health only for the authoritative
    probeless services.

    Arguments:
        spec: Environment whose required records are requested.

    Returns:
        Complete inspect-shaped container list.
    """
    names = (
        platform.DEVELOPMENT_CONTAINERS
        if spec is platform.DEVELOPMENT
        else platform.TESTING_CONTAINERS
    )

    return [
        container_record(
            name,
            health=None if name in platform.PROBELESS_CONTAINERS else "healthy",
        )
        for name in sorted(names)
    ]


def test_development_inventory_includes_the_registered_worker() -> None:
    """Audit the worker with the rest of the persistent development project.

    Requires operator health and ownership checks to inspect the worker under the registered
    Compose project and application image rather than leaving it outside the managed inventory.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the worker is absent from the required development inventory.
    """
    assert "celery-worker-cw8rt" in platform.DEVELOPMENT_CONTAINERS
    assert platform.CONTAINER_PROJECTS["celery-worker-cw8rt"] == "localforge-dev"
    assert platform.CONTAINER_IMAGES["celery-worker-cw8rt"] == "localforge/django:0.1.0"
    assert "celery-worker-cw8rt" not in platform.PROBELESS_CONTAINERS


def test_development_inventory_includes_the_registered_scheduler() -> None:
    """Audit the scheduler with the persistent development project.

    Requires ownership checks to include the single registered Beat process while accepting that
    the scheduler has no independent protocol health endpoint.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the scheduler is absent, misowned, or incorrectly requires a probe.
    """
    assert "celery-beat-cb4hq" in platform.DEVELOPMENT_CONTAINERS
    assert platform.CONTAINER_PROJECTS["celery-beat-cb4hq"] == "localforge-dev"
    assert platform.CONTAINER_IMAGES["celery-beat-cb4hq"] == "localforge/django:0.1.0"
    assert "celery-beat-cb4hq" in platform.PROBELESS_CONTAINERS


def test_development_inventory_includes_the_registered_worker_dashboard() -> None:
    """Audit Flower with the persistent development project.

    Requires ownership and health checks to include the authenticated dashboard under the shared
    application image while the testing inventory remains headless.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If Flower is absent, misowned, or added to testing.
    """
    assert "flower-fl9zd" in platform.DEVELOPMENT_CONTAINERS
    assert platform.CONTAINER_PROJECTS["flower-fl9zd"] == "localforge-dev"
    assert platform.CONTAINER_IMAGES["flower-fl9zd"] == "localforge/django:0.1.0"
    assert "flower-fl9zd" not in platform.PROBELESS_CONTAINERS
    assert "flower-fl9zd" not in platform.TESTING_CONTAINERS


def test_help_publishes_the_complete_stable_command_surface(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """List every supported workflow with safety and environment guidance.

    Invokes the public command without arguments and requires setup, symmetric environment
    operations, test modes, and destructive reset warnings to be discoverable in one response.

    Arguments:
        capsys: Fixture capturing command output.

    Returns:
        None.

    Raises:
        AssertionError: If the command surface is incomplete or ambiguously documented.
    """
    code = platform.main([])
    output = capsys.readouterr().out

    assert code == platform.EXIT_OK
    assert "./localforge.sh setup" in output
    assert "./localforge.sh development-rebuild" in output
    assert "./localforge.sh testing-rebuild" in output
    assert "./localforge.sh testing-test-container" in output
    assert "./localforge.sh testing-test-host" in output
    assert "./localforge.sh testing-registration-timing-stability" in output
    assert "./localforge.sh testing-verify" in output
    assert "./localforge.sh environments-setup --proxy-only" in output
    assert "./localforge.sh development-up --proxy-only" in output
    assert "./localforge.sh development-rebuild --proxy-only" in output
    assert "./localforge.sh development-reset --proxy-only" in output
    assert "./localforge.sh developer-access-export" in output
    assert "rejected for every other command" in output
    assert "DESTRUCTIVE" in output
    assert "preserves volumes" in output


def test_developer_access_export_dispatches_to_browser_import_generator(
    tmp_path: Path,
) -> None:
    """Expose browser import generation through the stable operator command.

    Invokes the public command name and requires the repository root to reach the dedicated export
    script without Docker or another subprocess.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If the command is missing or dispatches with the wrong repository root.
    """
    with patch.object(
        platform,
        "export_developer_access_main",
        return_value=platform.EXIT_OK,
    ) as export_main:
        code = platform.main(["developer-access-export"], root=tmp_path)

    assert code == platform.EXIT_OK
    export_main.assert_called_once_with(root=tmp_path)


def test_setup_checks_prerequisites_and_tops_up_existing_environment_files(
    tmp_path: Path,
) -> None:
    """Prepare an existing local configuration without replacing its values.

    Supplies all plaintext files and verifies setup delegates only to the authoritative preflight
    and idempotent generator scripts.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If setup decrypts unnecessarily or bypasses an authoritative helper.
    """
    environment_files(tmp_path)
    runner = FakeRunner()

    code = platform.main(["setup"], root=tmp_path, runner=runner)

    assert code == platform.EXIT_OK
    assert [call[0] for call in runner.calls] == [
        (sys.executable, str(tmp_path / "backend" / "scripts" / "preflight.py")),
        (
            sys.executable,
            str(tmp_path / "backend" / "scripts" / "gen_secrets.py"),
            "--environment",
            "all",
        ),
    ]


def test_development_rebuild_preserves_volumes_and_recreates_services(tmp_path: Path) -> None:
    """Rebuild development without invoking the destructive reset path.

    Verifies the stable command delegates one image build, one force-recreate start, and one
    idempotent storage check without a volume-removal argument.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If rebuild deletes data or omits a required step.
    """
    environment_files(tmp_path)
    runner = FakeRunner()

    code = platform.main(["development-rebuild"], root=tmp_path, runner=runner)

    commands = [call[0] for call in runner.calls]
    assert code == platform.EXIT_OK
    assert commands[0][-3:] == ("build", "postgres-pg3ka", "django-uv5n2")
    assert commands[0][2:4] == ("--project-name", "localforge-dev")
    assert commands[1][-8:] == (
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        "600",
        "--no-build",
        "--force-recreate",
        "--remove-orphans",
    )
    assert commands[2] == (
        sys.executable,
        str(tmp_path / "backend" / "scripts" / "seed_storage.py"),
        "--environment",
        "development",
        "--endpoint",
        "http://127.0.0.1:8333",
    )
    assert "--volumes" not in {argument for command in commands for argument in command}


def test_destructive_reset_requires_explicit_confirmation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Refuse data deletion unless the destructive flag is explicit.

    Invokes reset without confirmation and verifies no subprocess is started, keeping a typo from
    deleting named volumes.

    Arguments:
        tmp_path: Temporary repository root.
        capsys: Fixture capturing the refusal message.

    Returns:
        None.

    Raises:
        AssertionError: If reset runs or reports success without confirmation.
    """
    environment_files(tmp_path)
    runner = FakeRunner()

    code = platform.main(["development-reset"], root=tmp_path, runner=runner)

    assert code == platform.EXIT_USAGE
    assert runner.calls == []
    assert "--confirm-destroy-data" in capsys.readouterr().out


def test_setup_falls_back_to_local_generation_without_an_age_key(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Generate local credentials when encrypted values cannot be decrypted.

    Models a fresh clone with both encrypted files and no age key, requiring setup to preserve the
    clear decrypt diagnostic while continuing through the authoritative generator.

    Arguments:
        tmp_path: Temporary repository root.
        capsys: Fixture capturing fallback guidance.

    Returns:
        None.

    Raises:
        AssertionError: If setup stops or bypasses either environment.
    """
    (tmp_path / ".env.development.sops").write_text("ciphertext", encoding="utf-8")
    (tmp_path / ".env.testing.sops").write_text("ciphertext", encoding="utf-8")
    runner = FakeRunner(results=[0, 3, 3, 0])

    code = platform.main(["setup"], root=tmp_path, runner=runner)

    commands = [call[0] for call in runner.calls]
    assert code == platform.EXIT_OK
    assert [command[-1] for command in commands[1:3]] == ["development", "testing"]
    assert commands[-1][-2:] == ("--environment", "all")
    assert capsys.readouterr().out.count("generating local credentials") == ENVIRONMENT_COUNT


def test_setup_propagates_a_real_decryption_failure(tmp_path: Path) -> None:
    """Stop setup when SOPS fails for a reason other than missing prerequisites.

    Ensures a corrupt or otherwise rejected encrypted file cannot be replaced silently by newly
    generated credentials.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If setup masks the failure or runs later steps.
    """
    (tmp_path / ".env.development.sops").write_text("ciphertext", encoding="utf-8")
    runner = FakeRunner(results=[0, 1])

    code = platform.main(["setup"], root=tmp_path, runner=runner)

    assert code == 1
    assert len(runner.calls) == DECRYPT_COMMAND_COUNT


def test_testing_verify_rebuilds_runs_both_modes_and_stops(tmp_path: Path) -> None:
    """Complete the symmetric testing workflow and leave dependencies stopped.

    Verifies the high-level command preserves volumes while sequencing build, startup, both suites,
    and successful teardown.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If a mode is skipped or the safe down command is absent.
    """
    environment_files(tmp_path)
    runner = FakeRunner()

    with (
        patch.object(platform, "inspect_environment_health", return_value=0),
        patch.object(platform, "testing_test", return_value=0) as testing_test,
    ):
        code = platform.main(
            ["testing-verify"],
            root=tmp_path,
            runner=runner,
            http_probe=lambda _url, _host: True,
        )

    commands = [call[0] for call in runner.calls]
    assert code == platform.EXIT_OK
    assert commands[0][-2:] == ("build", "django-test-dt5qx")
    assert "label=com.docker.compose.project=localforge-test" in commands[3]
    assert "wait_for_services.py" in commands[4][1]
    testing_test.assert_called_once()
    assert commands[5][-2:] == ("down", "--remove-orphans")


def test_testing_verify_leaves_the_stack_running_after_a_test_failure(tmp_path: Path) -> None:
    """Preserve a failed testing environment for diagnosis.

    Stops at the first failing mode and omits teardown so logs and service state remain available
    to the operator.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If failure is hidden or the stack is stopped.
    """
    environment_files(tmp_path)
    runner = FakeRunner()

    with (
        patch.object(platform, "rebuild", return_value=0),
        patch.object(platform, "health", return_value=0),
        patch.object(platform, "testing_test", return_value=LOG_FAILURE),
    ):
        code = platform.main(["testing-verify"], root=tmp_path, runner=runner)

    assert code == LOG_FAILURE
    assert all("down" not in call[0] for call in runner.calls)


def test_registration_timing_stability_command_starts_dependencies(
    tmp_path: Path,
) -> None:
    """Dispatch the standalone timing stability command through its supported lifecycle.

    Keeps the user-facing Poe command bound to the shared implementation with environment startup
    enabled and no temporary Mailpit expectation.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If command dispatch changes lifecycle arguments.
    """
    environment_files(tmp_path)
    runner = FakeRunner()
    probe = MagicMock(return_value=True)

    with patch.object(
        platform,
        "registration_timing_stability",
        return_value=0,
    ) as stability:
        code = platform.main(
            ["testing-registration-timing-stability"],
            root=tmp_path,
            runner=runner,
            http_probe=probe,
        )

    assert code == platform.EXIT_OK
    stability.assert_called_once_with(
        tmp_path,
        runner,
        ensure_up=True,
        include_mailpit=False,
        http_probe=probe,
    )


def test_both_test_modes_report_equal_collection_timings_and_postchecks(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run and report both complete modes without hiding their independent evidence.

    Requires one collection and full-suite command per mode, equal counts, per-mode and total
    timings, and health plus residue checks after each completed run.

    Arguments:
        tmp_path: Temporary repository root.
        capsys: Fixture capturing stable operator evidence.

    Returns:
        None.

    Raises:
        AssertionError: If either mode, count, timing, or post-check is omitted.
    """
    environment_files(tmp_path)
    runner = FakeRunner(
        outputs=[
            "2031 tests collected in 9.53s",
            "2008 tests collected in 9.53s",
            "23 tests collected in 9.53s",
            "2031 tests collected in 9.34s",
            "2008 tests collected in 9.34s",
            "23 tests collected in 9.34s",
        ]
    )

    with (
        patch.object(platform, "start_testing_mailpit", return_value=0) as start_mailpit,
        patch.object(platform, "finalize_testing_mailpit", return_value=0) as stop_mailpit,
        patch.object(platform, "health", return_value=0) as health,
        patch.object(platform, "docker_environment_audit", return_value=0) as ownership,
        patch.object(platform, "audit_testing_container_set", return_value=0) as residue,
        patch.object(platform, "audit_testing_logs", return_value=0) as logs,
        patch.object(
            platform,
            "registration_timing_stability",
            return_value=0,
        ) as timing_stability,
    ):
        code = platform.testing_test(
            tmp_path,
            runner,
            "both",
            ensure_up=False,
            http_probe=lambda _url, _host: True,
            now=iter((0.0, 1.0, 4.0, 5.0, 9.0, 10.0)).__next__,
        )

    commands = [call[0] for call in runner.calls]
    output = capsys.readouterr().out
    assert code == platform.EXIT_OK
    assert sum("--collect-only" in command for command in commands) == ENVIRONMENT_COUNT * 3
    assert (
        sum("poe" in command and "test-stages" in command for command in commands)
        == ENVIRONMENT_COUNT
    )
    start_mailpit.assert_called_once()
    stop_mailpit.assert_called_once()
    timing_stability.assert_called_once_with(
        tmp_path,
        runner,
        ensure_up=False,
        include_mailpit=True,
        http_probe=ANY,
    )
    assert health.call_count == ENVIRONMENT_COUNT
    assert ownership.call_count == ENVIRONMENT_COUNT
    assert residue.call_count == ENVIRONMENT_COUNT
    assert logs.call_count == ENVIRONMENT_COUNT
    assert all(not call.kwargs for call in logs.call_args_list)
    assert "COLLECTION mode=container complete=2031 core=2008 timing=23" in output
    assert "COLLECTION mode=host complete=2031 core=2008 timing=23" in output
    assert "TIMING phase=test-container seconds=3.000 status=0" in output
    assert "TIMING phase=test-host seconds=4.000 status=0" in output
    assert "TIMING phase=test-both seconds=10.000 status=0" in output
    assert "RESULT mode=container status=0" in output
    assert "RESULT mode=host status=0" in output


def test_registration_timing_stability_runs_five_independent_processes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Require five successful host processes plus complete runtime post-checks.

    Runs the supported stability seam without environment startup, proving every attempt is a
    separate pytest command and no successful sample can hide a later failure.

    Arguments:
        tmp_path: Temporary repository root.
        capsys: Fixture capturing stable operator evidence.

    Returns:
        None.

    Raises:
        AssertionError: If any attempt, post-check, or result line is omitted.
    """
    runner = FakeRunner()

    with (
        patch.object(platform, "up", return_value=0) as up,
        patch.object(platform, "health", return_value=0) as health,
        patch.object(platform, "docker_environment_audit", return_value=0) as ownership,
        patch.object(platform, "audit_testing_container_set", return_value=0) as residue,
        patch.object(platform, "audit_testing_logs", return_value=0) as logs,
    ):
        code = platform.registration_timing_stability(
            tmp_path,
            runner,
            ensure_up=True,
            include_mailpit=True,
            http_probe=lambda _url, _host: True,
        )

    commands = [call[0] for call in runner.calls]
    output = capsys.readouterr().out
    assert code == platform.EXIT_OK
    assert len(commands) == platform.REGISTRATION_TIMING_STABILITY_ATTEMPTS
    assert all(platform.REGISTRATION_TIMING_TEST in command for command in commands)
    assert all("--no-cov" in command and "-m" in command for command in commands)
    up.assert_called_once()
    assert health.call_count == STABILITY_HEALTH_CHECK_COUNT
    ownership.assert_called_once()
    residue.assert_called_once_with(runner, include_mailpit=True)
    logs.assert_called_once()
    assert "attempt=5/5 status=0" in output
    assert "RESULT registration-timing-stability status=0" in output


@pytest.mark.parametrize(
    ("up_code", "health_code", "expected"),
    [
        (IMAGE_FAILURE, 0, IMAGE_FAILURE),
        (0, LOG_FAILURE, LOG_FAILURE),
    ],
)
def test_registration_timing_stability_propagates_startup_failures(
    tmp_path: Path,
    up_code: int,
    health_code: int,
    expected: int,
) -> None:
    """Stop before timing measurement when startup or readiness fails.

    Covers both environment lifecycle boundaries without allowing a failed dependency check to
    produce success-shaped timing evidence.

    Arguments:
        tmp_path: Temporary repository root.
        up_code: Environment startup result.
        health_code: Dependency readiness result after successful startup.
        expected: Failure expected from the stability command.

    Returns:
        None.

    Raises:
        AssertionError: If timing begins after either startup boundary fails.
    """
    runner = FakeRunner()

    with (
        patch.object(platform, "up", return_value=up_code),
        patch.object(platform, "health", return_value=health_code) as health,
    ):
        code = platform.registration_timing_stability(
            tmp_path,
            runner,
            ensure_up=True,
            include_mailpit=False,
            http_probe=lambda _url, _host: True,
        )

    assert code == expected
    assert not runner.calls
    assert health.call_count == int(up_code == 0)


def test_registration_timing_stability_stops_on_first_failed_process(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stop repeated timing evidence at the first failed independent process.

    Lets one process pass and the next fail, while still requiring every runtime post-check before
    the failure reaches the operator.

    Arguments:
        tmp_path: Temporary repository root.
        capsys: Fixture capturing stable operator evidence.

    Returns:
        None.

    Raises:
        AssertionError: If failure is retried, hidden, or allowed to skip post-checks.
    """
    runner = FakeRunner(results=[0, LOG_FAILURE])

    with (
        patch.object(platform, "health", return_value=0),
        patch.object(platform, "docker_environment_audit", return_value=0),
        patch.object(platform, "audit_testing_container_set", return_value=0),
        patch.object(platform, "audit_testing_logs", return_value=0),
    ):
        code = platform.registration_timing_stability(
            tmp_path,
            runner,
            ensure_up=False,
            include_mailpit=False,
            http_probe=lambda _url, _host: True,
        )

    output = capsys.readouterr().out
    assert code == LOG_FAILURE
    assert len(runner.calls) == FAILED_STABILITY_ATTEMPTS
    assert "attempt=2/5 status=7" in output
    assert "attempt=3/5" not in output
    assert "RESULT registration-timing-stability status=7" in output


def test_both_test_modes_run_independently_and_return_distinct_failures(
    tmp_path: Path,
) -> None:
    """Continue to host mode after container failure and classify the outcome.

    Exercises container-only, host-only, and dual failures so automation can identify which mode
    failed without parsing pytest output.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If a failure skips the other mode or uses an ambiguous exit code.
    """
    environment_files(tmp_path)

    with (
        patch.object(platform, "start_testing_mailpit", return_value=0),
        patch.object(platform, "finalize_testing_mailpit", return_value=0),
        patch.object(platform, "health", return_value=0),
        patch.object(platform, "docker_environment_audit", return_value=0),
        patch.object(platform, "audit_testing_container_set", return_value=0),
        patch.object(platform, "audit_testing_logs", return_value=0) as logs,
    ):
        container_runner = FakeRunner(
            results=[0, 0, 0, 0, 0, 0, LOG_FAILURE, 0],
            outputs=[
                "2031 tests collected",
                "2008 tests collected",
                "23 tests collected",
                "2031 tests collected",
                "2008 tests collected",
                "23 tests collected",
            ],
        )
        container_code = platform.testing_test(
            tmp_path,
            container_runner,
            "both",
            ensure_up=False,
            http_probe=lambda _url, _host: True,
            now=iter((0.0, 1.0, 2.0, 3.0, 4.0, 5.0)).__next__,
        )
        host_runner = FakeRunner(
            results=[0, 0, 0, 0, 0, 0, 0, LOG_FAILURE],
            outputs=[
                "2031 tests collected",
                "2008 tests collected",
                "23 tests collected",
                "2031 tests collected",
                "2008 tests collected",
                "23 tests collected",
            ],
        )
        host_code = platform.testing_test(
            tmp_path,
            host_runner,
            "both",
            ensure_up=False,
            http_probe=lambda _url, _host: True,
            now=iter((0.0, 1.0, 2.0, 3.0, 4.0, 5.0)).__next__,
        )
        both_runner = FakeRunner(
            results=[0, 0, 0, 0, 0, 0, LOG_FAILURE, IMAGE_FAILURE],
            outputs=[
                "2031 tests collected",
                "2008 tests collected",
                "23 tests collected",
                "2031 tests collected",
                "2008 tests collected",
                "23 tests collected",
            ],
        )
        both_code = platform.testing_test(
            tmp_path,
            both_runner,
            "both",
            ensure_up=False,
            http_probe=lambda _url, _host: True,
            now=iter((0.0, 1.0, 2.0, 3.0, 4.0, 5.0)).__next__,
        )

    assert container_code == platform.EXIT_CONTAINER_TEST_FAILED
    assert host_code == platform.EXIT_HOST_TEST_FAILED
    assert both_code == platform.EXIT_BOTH_TEST_MODES_FAILED
    assert ("uv", "run", "poe", "test-stages") in [call[0] for call in container_runner.calls]
    assert logs.call_count == ENVIRONMENT_COUNT * 3
    assert all(not call.kwargs for call in logs.call_args_list)


def test_dual_mode_collection_mismatch_and_unreadable_counts_fail(
    tmp_path: Path,
) -> None:
    """Reject unequal or unparsable collection evidence.

    Requires parity to be evaluated independently of successful test execution and maps a missing
    count to the execution mode whose collection output was unusable.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If collection drift or malformed evidence is accepted.
    """
    environment_files(tmp_path)

    with (
        patch.object(platform, "start_testing_mailpit", return_value=0),
        patch.object(platform, "finalize_testing_mailpit", return_value=0),
        patch.object(platform, "health", return_value=0),
        patch.object(platform, "docker_environment_audit", return_value=0),
        patch.object(platform, "audit_testing_container_set", return_value=0),
        patch.object(platform, "audit_testing_logs", return_value=0) as logs,
    ):
        mismatch = platform.testing_test(
            tmp_path,
            FakeRunner(
                outputs=[
                    "2030 tests collected",
                    "2007 tests collected",
                    "23 tests collected",
                    "2031 tests collected",
                    "2008 tests collected",
                    "23 tests collected",
                ],
            ),
            "both",
            ensure_up=False,
            http_probe=lambda _url, _host: True,
            now=iter((0.0, 1.0, 2.0, 3.0, 4.0, 5.0)).__next__,
        )
        unreadable = platform.testing_test(
            tmp_path,
            FakeRunner(
                outputs=[
                    "collection output unavailable",
                    "2008 tests collected",
                    "23 tests collected",
                ]
            ),
            "container",
            ensure_up=False,
            http_probe=lambda _url, _host: True,
            now=iter((0.0, 1.0, 2.0, 3.0)).__next__,
        )
        partly_unreadable = platform.testing_test(
            tmp_path,
            FakeRunner(
                outputs=[
                    "collection output unavailable",
                    "2008 tests collected",
                    "23 tests collected",
                    "2031 tests collected",
                    "2008 tests collected",
                    "23 tests collected",
                ],
            ),
            "both",
            ensure_up=False,
            http_probe=lambda _url, _host: True,
            now=iter((0.0, 1.0, 2.0, 3.0, 4.0, 5.0)).__next__,
        )

    assert mismatch == platform.EXIT_TEST_COLLECTION_MISMATCH
    assert unreadable == platform.EXIT_FAILED
    assert partly_unreadable == platform.EXIT_CONTAINER_TEST_FAILED
    assert logs.call_count == ENVIRONMENT_COUNT * 2 + 1
    assert all(not call.kwargs for call in logs.call_args_list)


def test_collection_and_testing_container_evidence_helpers() -> None:
    """Parse final counts and reject missing, extra, or unreadable container evidence.

    Covers helper failure boundaries directly so the dual-mode coordinator can remain focused on
    sequencing while the exact headless project set is still enforced.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If parsing or project-scoped residue checks accept invalid evidence.
        ValueError: Expected for an unsupported testing mode.
    """
    assert (
        platform.parse_test_collection("2029 tests collected\n2031 tests collected in 9.0s")
        == TEST_COLLECTION_COUNT
    )
    assert platform.parse_test_collection("23/2044 tests collected") == TEST_TIMING_COUNT
    assert platform.parse_test_collection("no collection summary") is None
    with pytest.raises(ValueError, match="unsupported testing mode"):
        platform.testing_mode_commands("invalid")

    assert platform.audit_testing_container_set(FakeRunner(results=[LOG_FAILURE])) == LOG_FAILURE
    drifted = FakeRunner(
        outputs=[
            "\n".join(
                (
                    *sorted(platform.TESTING_CONTAINERS - {"django-test-dt5qx"}),
                    "django-test-dt5qx-run-deadbeef",
                )
            )
        ]
    )
    assert platform.audit_testing_container_set(drifted) == platform.EXIT_FAILED
    exact = FakeRunner(outputs=["\n".join(sorted(platform.TESTING_CONTAINERS))])
    assert platform.audit_testing_container_set(exact) == platform.EXIT_OK


def test_collection_evidence_requires_all_three_exact_partitions() -> None:
    """Reject failed, unreadable, and arithmetically incomplete stage collections.

    Exercises each collection failure shape directly and verifies valid evidence drives mode result
    properties without remapping an underlying single-mode process failure.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If incomplete collection evidence is accepted.
    """
    failed = platform.collect_testing_mode(
        FakeRunner(
            results=[LOG_FAILURE, 0, 0],
            outputs=[
                "2031 tests collected",
                "2008 tests collected",
                "23 tests collected",
            ],
        ),
        "container",
    )
    partitioned = platform.collect_testing_mode(
        FakeRunner(
            outputs=[
                "2031 tests collected",
                "2007 tests collected",
                "23 tests collected",
            ]
        ),
        "host",
    )
    valid = platform.TestingCollectionResult(
        mode="host",
        complete=TEST_COLLECTION_COUNT,
        core=TEST_CORE_COUNT,
        timing=TEST_TIMING_COUNT,
        code=0,
    )
    inconsistent = platform.TestingCollectionResult(
        mode="host",
        complete=TEST_COLLECTION_COUNT,
        core=TEST_CORE_COUNT - 1,
        timing=TEST_TIMING_COUNT,
        code=0,
    )

    assert failed.code == LOG_FAILURE
    assert failed.valid is False
    assert partitioned.code == platform.EXIT_FAILED
    assert partitioned.valid is False
    assert valid.valid is True
    assert (
        platform.TestingModeResult(
            collection=valid,
            test_code=LOG_FAILURE,
            non_log_post_code=0,
            log_code=0,
            duration=1.0,
            started_at="2026-09-21T00:00:00Z",
            ended_at="2026-09-21T00:00:01Z",
        ).failure_code
        == LOG_FAILURE
    )
    invalid_mode = platform.TestingModeResult(
        collection=inconsistent,
        test_code=0,
        non_log_post_code=0,
        log_code=0,
        duration=1.0,
        started_at="2026-09-21T00:00:00Z",
        ended_at="2026-09-21T00:00:01Z",
    )
    assert invalid_mode.failure_code == platform.EXIT_FAILED
    assert invalid_mode.passed is False


def test_scoped_docker_ownership_audit_covers_both_environments() -> None:
    """Validate complete scoped ownership and every collector failure boundary.

    Reuses registered inspect fixtures while limiting each audit to one project, proving testing
    checks do not require development and development checks do not require testing.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If scoped ownership accepts drift or hides a Docker failure.
    """
    testing_networks = [
        {"Name": name, "Labels": {"com.docker.compose.project": project}}
        for name, project in platform.NETWORK_PROJECTS.items()
        if project == platform.TESTING.project
    ]
    testing_volumes = [
        {"Name": name, "Labels": {"com.docker.compose.project": project}}
        for name, project in platform.VOLUME_PROJECTS.items()
        if name in platform.TESTING_VOLUMES
    ]
    development_networks = [
        {"Name": name, "Labels": {"com.docker.compose.project": project}}
        for name, project in platform.NETWORK_PROJECTS.items()
        if project == platform.DEVELOPMENT.project
    ]
    development_volumes = [
        {"Name": name, "Labels": {"com.docker.compose.project": project}}
        for name, project in platform.VOLUME_PROJECTS.items()
        if name in platform.DEVELOPMENT_VOLUMES
    ]

    with patch.object(
        platform,
        "inspect_docker_objects",
        side_effect=[
            (0, environment_records(platform.TESTING)),
            (0, testing_networks),
            (0, testing_volumes),
        ],
    ):
        assert (
            platform.docker_environment_audit(
                FakeRunner(outputs=["sha256:test"]),
                platform.TESTING,
            )
            == 0
        )
    with patch.object(
        platform,
        "inspect_docker_objects",
        side_effect=[
            (0, environment_records(platform.DEVELOPMENT)),
            (0, development_networks),
            (0, development_volumes),
        ],
    ):
        assert platform.docker_environment_audit(FakeRunner(), platform.DEVELOPMENT) == 0

    failure_results: tuple[list[tuple[int, list[dict[str, object]]]], ...] = (
        [(LOG_FAILURE, [])],
        [(0, []), (IMAGE_FAILURE, [])],
        [(0, []), (0, []), (GENERATION_FAILURE, [])],
    )
    for results in failure_results:
        with patch.object(platform, "inspect_docker_objects", side_effect=results):
            assert platform.docker_environment_audit(FakeRunner(), platform.TESTING) != 0
    with patch.object(
        platform,
        "inspect_docker_objects",
        side_effect=[
            (0, environment_records(platform.TESTING)),
            (0, testing_networks),
            (0, testing_volumes),
        ],
    ):
        assert (
            platform.docker_environment_audit(
                FakeRunner(results=[IMAGE_FAILURE]),
                platform.TESTING,
            )
            == platform.EXIT_FAILED
        )
    with patch.object(
        platform,
        "inspect_docker_objects",
        side_effect=[(0, []), (0, []), (0, [])],
    ):
        assert (
            platform.docker_environment_audit(
                FakeRunner(outputs=["sha256:test"]),
                platform.TESTING,
            )
            == platform.EXIT_FAILED
        )


@pytest.mark.parametrize(
    "line",
    [
        '{"level":"WARNING"}',
        '{"level":"ERROR"}',
        '{"level":"CRITICAL"}',
        '{"level": "WARNING"}',
        "level=WARN",
        "level=ERROR",
        "level=CRITICAL",
        "[warning]",
        "[error]",
        "[critical]",
        "WARNING: checkpoint delayed",
        "2026-09-21 22:00:00.123 UTC [42] ERROR: query failed",
        "2026-09-21 22:00:00 UTC [42] FATAL: role rejected",
        "2026-09-21 22:00:00 UTC [42] PANIC: storage failure",
        "1:M 22 Sep 2026 01:00:00.000 # memory warning",
        "W0922 01:00:00.123456 warning.go:42 warning",
        "E0922 01:00:00.123456 error.go:42 error",
        "F0922 01:00:00.123456 fatal.go:42 fatal",
    ],
)
def test_log_severity_classifier_recognizes_supported_formats(line: str) -> None:
    """Recognize every warning-or-higher format emitted by project services.

    Feeds each explicit structured or vendor severity syntax into the shared classifier.
    Ordinary non-failure text is covered by the bounded log-audit test beside this one.

    Arguments:
        line: Representative structured or vendor log line.

    Returns:
        None.

    Raises:
        AssertionError: If a supported severity syntax is missed.
    """
    assert platform.log_line_is_warning_or_higher(line) is True


def test_testing_log_audit_bounds_expected_and_unexpected_records() -> None:
    """Accept only documented negative-path and connection-close records.

    Covers listing and per-container command failures, clean output, database diagnostics, paired
    connection closes, fixed global-QoS regressions, and unexpected failure-level records.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If bounded log policy accepts or rejects the wrong record.
    """
    assert platform.log_line_is_warning_or_higher("logger=uvicorn.error level=INFO") is False
    assert platform.normalize_log_line("\x1b[31mERROR\x1b[0m") == "ERROR"
    assert platform.log_line_is_warning_or_higher('{"level": "INFO"}') is False
    assert platform.log_line_is_warning_or_higher('{"level": 10}') is False
    assert (
        platform.log_line_is_warning_or_higher(
            "1:M 22 Sep 2026 01:00:00.000 * Ready to accept connections"
        )
        is False
    )
    assert platform.log_line_is_warning_or_higher("I0922 01:00:00.123456 info.go:42 ready") is False
    for marker in (
        "global_qos",
        "By default, this feature is not permitted anymore.",
        "The feature will be removed from a future major RabbitMQ version",
        "To continue using this feature when it is not permitted by default",
        "deprecated_features.permit.global_qos",
    ):
        assert platform.expected_testing_log_line("rabbitmq-tr6mc", marker) is False
    assert (
        platform.expected_testing_log_line(
            "postgres-tp8vn",
            "global_qos",
        )
        is False
    )
    assert (
        platform.expected_testing_log_line(
            "postgres-tp8vn",
            'ERROR: duplicate key value violates unique constraint "accounts_user_email_key"',
        )
        is True
    )
    assert (
        platform.expected_testing_log_line(
            "postgres-tp8vn",
            "ERROR: unrelated database failure",
        )
        is False
    )
    assert (
        platform.expected_testing_log_line(
            "seaweedfs-ts3jd",
            "global_qos",
        )
        is False
    )
    assert (
        platform.expected_testing_log_line(
            "rabbitmq-tr6mc",
            "different warning",
        )
        is False
    )
    prefix = "2026-09-21 22:00:00.000000+00:00 [warning] <0.1.0> "
    assert platform.rabbitmq_connection_close_pair(
        f"{prefix}closing AMQP connection <0.1.0> (client -> server):",
        f"{prefix}client unexpectedly closed TCP connection",
    )
    assert platform.rabbitmq_connection_close_pair(
        f"\x1b[38;5;214m{prefix}closing AMQP connection <0.1.0> (client -> server):\x1b[0m",
        f"\x1b[38;5;214m{prefix}client unexpectedly closed TCP connection\x1b[0m",
    )
    assert (
        platform.rabbitmq_connection_close_pair(
            f"{prefix}closing AMQP connection <0.1.0> (client -> server):",
            "2026-09-21 22:00:01.000000+00:00 [warning] <0.2.0> "
            "client unexpectedly closed TCP connection",
        )
        is False
    )
    assert (
        platform.rabbitmq_connection_close_pair(
            f"{prefix}closing AMQP connection <0.1.0> (client -> server):",
            f"{prefix}client unexpectedly closed TCP connection with protocol error",
        )
        is False
    )
    assert (
        platform.rabbitmq_connection_close_pair(
            f"{prefix}closing AMQP connection <0.1.0> (client -> server): unrelated",
            f"{prefix}client unexpectedly closed TCP connection",
        )
        is False
    )
    assert platform.rabbitmq_connection_close_pair("ordinary", "ordinary") is False

    assert (
        platform.audit_testing_logs(
            FakeRunner(results=[LOG_FAILURE]),
            "2026-09-21T00:00:00Z",
            "2026-09-21T00:00:02Z",
        )
        == LOG_FAILURE
    )
    assert (
        platform.audit_testing_logs(
            FakeRunner(
                results=[0, IMAGE_FAILURE],
                outputs=["rabbitmq-tr6mc", ""],
            ),
            "2026-09-21T00:00:00Z",
            "2026-09-21T00:00:02Z",
        )
        == IMAGE_FAILURE
    )
    expected = FakeRunner(
        outputs=[
            "rabbitmq-tr6mc\npostgres-tp8vn",
            (
                "logger=uvicorn.error level=INFO\n"
                "2026-09-21 22:00:00.000000+00:00 [warning] <0.1.0> "
                "closing AMQP connection <0.1.0> (client -> server):\n"
                "2026-09-21 22:00:00.000000+00:00 [warning] <0.1.0> "
                "client unexpectedly closed TCP connection"
            ),
            'ERROR: duplicate key value violates unique constraint "accounts_user_email_key"',
        ]
    )
    assert (
        platform.audit_testing_logs(
            expected,
            "2026-09-21T00:00:00Z",
            "2026-09-21T00:00:02Z",
        )
        == 0
    )
    assert "-a" in expected.calls[0][0]
    assert "--since" in expected.calls[1][0]
    assert "--until" in expected.calls[1][0]
    unexpected = FakeRunner(
        outputs=[
            "rabbitmq-tr6mc",
            "[warning] client unexpectedly closed TCP connection",
        ]
    )
    assert (
        platform.audit_testing_logs(
            unexpected,
            "2026-09-21T00:00:00Z",
            "2026-09-21T00:00:02Z",
        )
        == platform.EXIT_FAILED
    )
    global_qos = FakeRunner(
        outputs=[
            "rabbitmq-tr6mc",
            "[error] Deprecated features: `global_qos`",
        ]
    )
    assert (
        platform.audit_testing_logs(
            global_qos,
            "2026-09-21T00:00:00Z",
            "2026-09-21T00:00:02Z",
        )
        == platform.EXIT_FAILED
    )


def test_host_runner_captures_standard_error_with_standard_output(tmp_path: Path) -> None:
    """Capture both Docker-style output streams through the process seam.

    Uses the active interpreter as a portable child command, proving bounded log audits can inspect
    records emitted on stderr without changing ordinary inherited-output execution.

    Arguments:
        tmp_path: Temporary process working directory.

    Returns:
        None.

    Raises:
        AssertionError: If either captured stream is lost.
    """
    runner = platform.HostRunner(tmp_path)

    result = runner.run(
        (
            sys.executable,
            "-c",
            "import sys; print('standard'); print('error', file=sys.stderr)",
        ),
        capture=True,
    )

    assert result.code == 0
    assert "standard" in result.output
    assert "error" in result.output


def test_environment_health_reports_an_inspect_process_failure() -> None:
    """Fail health when Docker inspection exits non-zero despite valid output.

    Covers the process-status boundary separately from decoded container state so Docker transport
    failure cannot be hidden by a complete-looking response body.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the inspect process failure is ignored.
    """
    runner = FakeRunner(
        results=[LOG_FAILURE],
        outputs=[json.dumps(environment_records(platform.TESTING))],
    )

    assert platform.inspect_environment_health(runner, platform.TESTING) == platform.EXIT_FAILED
    assert (
        platform.inspect_environment_health(FakeRunner(), platform.TESTING) == platform.EXIT_FAILED
    )


def test_testing_integration_audit_proves_mail_persistence_and_real_degradation(
    tmp_path: Path,
) -> None:
    """Exercise the two live-service scenarios pytest cannot orchestrate itself.

    Requires the operator command to seed Mailpit from both run modes, verify the messages before
    and after container recreation, clear the store, and stop the real cache while both modes
    observe degraded readiness before restoring healthy state.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If either mode, persistence, cleanup, or dependency recovery is omitted.
    """
    environment_files(tmp_path)
    runner = FakeRunner()

    with (
        patch.object(platform, "up", return_value=0) as up,
        patch.object(platform, "health", return_value=0) as health,
    ):
        code = platform.main(
            ["testing-integration-audit"],
            root=tmp_path,
            runner=runner,
        )

    commands = [call[0] for call in runner.calls]
    rendered = [" ".join(command) for command in commands]
    assert code == platform.EXIT_OK
    up.assert_called_once()
    health.assert_called_once()
    assert any("--profile smtp up -d --no-build mailpit-tm7bh" in line for line in rendered)
    assert sum("mailpit-seed" in line for line in rendered) == ENVIRONMENT_COUNT
    assert sum("mailpit-verify" in line for line in rendered) == ENVIRONMENT_COUNT * 2
    assert sum("mailpit-empty" in line for line in rendered) == ENVIRONMENT_COUNT * 2
    smtp_calls = [
        call
        for call in runner.calls
        if "test_registration_activation_loop_round_trips_through_mailpit" in " ".join(call[0])
    ]
    smtp_backend = "django.core.mail.backends.smtp.EmailBackend"
    assert len(smtp_calls) == ENVIRONMENT_COUNT
    assert all(
        (environment is not None and environment.get("EMAIL_BACKEND") == smtp_backend)
        or f"EMAIL_BACKEND={smtp_backend}" in command
        for command, environment, _capture in smtp_calls
    )
    assert any("--force-recreate --no-deps mailpit-tm7bh" in line for line in rendered)
    stop_index = next(
        index for index, line in enumerate(rendered) if "stop valkey-cache-tv4kq" in line
    )
    degraded = [index for index, line in enumerate(rendered) if "health-degraded" in line]
    restore_index = next(
        index
        for index, line in enumerate(rendered)
        if "up -d --no-build valkey-cache-tv4kq" in line
    )
    ready = [index for index, line in enumerate(rendered) if "health-ready" in line]
    assert len(degraded) == ENVIRONMENT_COUNT
    assert len(ready) == ENVIRONMENT_COUNT
    assert stop_index < min(degraded) < restore_index < min(ready)
    assert rendered[-1].endswith("rm -f -s mailpit-tm7bh")


def test_testing_runtime_helpers_cover_each_mode_and_failure(tmp_path: Path) -> None:
    """Cover host, container, invalid, and short-circuit runtime helper behavior.

    Verifies each command carries the configuration its execution location needs, failures stop the
    two-mode sequence, and unsupported modes fail explicitly instead of choosing a silent default.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If a helper omits configuration or hides a failure.
        ValueError: Expected for unsupported runtime and SMTP modes.
    """
    environment_files(tmp_path)
    host = FakeRunner()
    container = FakeRunner()

    assert platform.testing_runtime_probe(tmp_path, host, "health-ready", "host") == 0
    assert host.calls[0][1] is not None
    assert str(tmp_path / "backend" / "src") in host.calls[0][1]["PYTHONPATH"]
    assert platform.testing_runtime_probe(tmp_path, container, "health-ready", "container") == 0
    assert "PYTHONPATH=/app/src:/app" in container.calls[0][0]
    with pytest.raises(ValueError, match="unsupported testing runtime mode"):
        platform.testing_runtime_probe(tmp_path, FakeRunner(), "health-ready", "invalid")

    failed = FakeRunner(results=[LOG_FAILURE])
    assert platform.testing_runtime_probe_both(tmp_path, failed, "health-ready") == LOG_FAILURE
    assert len(failed.calls) == 1

    waiting = FakeRunner()
    assert platform.wait_for_testing_service(tmp_path, waiting, "mailpit") == 0
    assert waiting.calls[0][0][-1] == "mailpit"

    start_failure = FakeRunner(results=[LOG_FAILURE, 0])
    assert platform.start_testing_mailpit(tmp_path, start_failure) == LOG_FAILURE
    assert start_failure.calls[-1][0][-4:] == ("rm", "-f", "-s", "mailpit-tm7bh")
    readiness_failure = FakeRunner(results=[0, LOG_FAILURE, 0])
    assert platform.start_testing_mailpit(tmp_path, readiness_failure) == LOG_FAILURE
    assert readiness_failure.calls[-1][0][-4:] == ("rm", "-f", "-s", "mailpit-tm7bh")

    smtp_host = FakeRunner()
    smtp_container = FakeRunner()
    assert platform.testing_smtp_integration_tests(tmp_path, smtp_host, "host") == 0
    assert smtp_host.calls[0][1] is not None
    assert smtp_host.calls[0][1]["EMAIL_BACKEND"] == platform.SMTP_BACKEND
    assert platform.testing_smtp_integration_tests(tmp_path, smtp_container, "container") == 0
    assert f"EMAIL_BACKEND={platform.SMTP_BACKEND}" in smtp_container.calls[0][0]
    with pytest.raises(ValueError, match="unsupported SMTP integration mode"):
        platform.testing_smtp_integration_tests(tmp_path, FakeRunner(), "invalid")


def test_testing_integration_audit_reports_startup_and_mail_failures(tmp_path: Path) -> None:
    """Stop the runtime audit at failed startup, readiness, or mail assertions.

    Covers each pre-degradation return boundary so a missing Mailpit service or failed persistence
    assertion cannot fall through into a misleading cache recovery result.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If the first failure is not preserved.
    """
    environment_files(tmp_path)

    with patch.object(platform, "up", return_value=LOG_FAILURE):
        assert run_testing_integration_audit(tmp_path, FakeRunner()) == LOG_FAILURE
    with (
        patch.object(platform, "up", return_value=0),
        patch.object(platform, "health", return_value=LOG_FAILURE),
    ):
        assert run_testing_integration_audit(tmp_path, FakeRunner()) == LOG_FAILURE
    with (
        patch.object(platform, "up", return_value=0),
        patch.object(platform, "health", return_value=0),
    ):
        assert (
            run_testing_integration_audit(
                tmp_path,
                FakeRunner(results=[LOG_FAILURE]),
            )
            == LOG_FAILURE
        )
    successful = platform.TestingModeResult(
        collection=platform.TestingCollectionResult(
            mode="container",
            complete=TEST_COLLECTION_COUNT,
            core=TEST_CORE_COUNT,
            timing=TEST_TIMING_COUNT,
            code=0,
        ),
        test_code=0,
        non_log_post_code=0,
        log_code=0,
        duration=1.0,
        started_at="2026-09-21T00:00:00Z",
        ended_at="2026-09-21T00:00:01Z",
    )
    with (
        patch.object(platform, "up", return_value=0),
        patch.object(platform, "health", return_value=0),
        patch.object(platform, "run_testing_mode", return_value=successful),
    ):
        with (
            patch.object(platform, "start_testing_mailpit", return_value=0),
            patch.object(platform, "finalize_testing_mailpit", return_value=0),
        ):
            assert (
                platform.testing_test(
                    tmp_path,
                    FakeRunner(),
                    "container",
                    ensure_up=True,
                    http_probe=lambda _url, _host: True,
                    now=iter((0.0, 1.0)).__next__,
                )
                == platform.EXIT_OK
            )
        readiness_failure = FakeRunner(results=[0, LOG_FAILURE])
        assert run_testing_integration_audit(tmp_path, readiness_failure) == LOG_FAILURE
        assert readiness_failure.calls[-1][0][-4:] == ("rm", "-f", "-s", "mailpit-tm7bh")
        mail_failure = FakeRunner(results=[0, 0, 0, LOG_FAILURE])
        assert run_testing_integration_audit(tmp_path, mail_failure) == LOG_FAILURE
        rendered = [" ".join(call[0]) for call in mail_failure.calls]
        assert sum("mailpit-clear" in line for line in rendered) == ENVIRONMENT_COUNT + 1
        assert rendered[-1].endswith("rm -f -s mailpit-tm7bh")


def test_testing_integration_audit_requires_the_testing_environment_file(
    tmp_path: Path,
) -> None:
    """Refuse the live audit before Docker when testing configuration is absent.

    Dispatches through the public operator interface and requires the ordinary environment-file
    guard to fail explicitly instead of starting services with unresolved configuration.

    Arguments:
        tmp_path: Empty temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If the command reaches its runtime audit.
    """
    runner = FakeRunner()

    code = platform.main(
        ["testing-integration-audit"],
        root=tmp_path,
        runner=runner,
    )

    assert code == platform.EXIT_USAGE
    assert runner.calls == []


def test_testing_cache_audit_restores_cache_after_probe_failure(
    tmp_path: Path,
) -> None:
    """Restore the real cache even when degraded readiness cannot be observed.

    Replaces assertion helpers while retaining direct Compose commands, requiring the audit to
    restart the stopped service before returning the original degraded-probe failure.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If recovery is skipped or another status replaces the probe failure.
    """
    environment_files(tmp_path)
    runner = FakeRunner()

    with (
        patch.object(platform, "testing_runtime_probe_both", return_value=LOG_FAILURE),
        patch.object(platform, "wait_for_testing_service", return_value=0),
    ):
        code = platform.testing_cache_degradation_audit(tmp_path, runner)

    rendered = [" ".join(call[0]) for call in runner.calls]
    assert code == LOG_FAILURE
    assert any("stop valkey-cache-tv4kq" in line for line in rendered)
    assert any("up -d --no-build valkey-cache-tv4kq" in line for line in rendered)


def test_testing_cache_audit_reports_stop_and_restore_failures(
    tmp_path: Path,
) -> None:
    """Propagate failures that prevent or fail the real dependency transition.

    Holds every probe helper green while direct Compose results fail at cache stop and cache
    restart, covering both operational boundaries without running Docker.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If a transition failure is hidden.
    """
    environment_files(tmp_path)

    with (
        patch.object(platform, "testing_runtime_probe_both", return_value=0),
        patch.object(platform, "wait_for_testing_service", return_value=0),
    ):
        stop_runner = FakeRunner(results=[LOG_FAILURE, 0])
        stop_failure = platform.testing_cache_degradation_audit(
            tmp_path,
            stop_runner,
        )
        restore_failure = platform.testing_cache_degradation_audit(
            tmp_path,
            FakeRunner(results=[0, LOG_FAILURE]),
        )

    assert stop_failure == LOG_FAILURE
    assert "up -d --no-build valkey-cache-tv4kq" in " ".join(stop_runner.calls[-1][0])
    assert restore_failure == LOG_FAILURE


def test_mailpit_finalizer_preserves_cleanup_and_stop_failures(tmp_path: Path) -> None:
    """Preserve each cleanup failure while always stopping profile state.

    Covers clear, empty verification, and service-stop failures independently so the finalizer
    cannot return success after leaving persistent audit messages or a running optional service.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If cleanup hides a failure or omits the stop command.
    """
    environment_files(tmp_path)

    clear_failure = FakeRunner(results=[LOG_FAILURE, 0, 0, 0, 0])
    assert platform.finalize_testing_mailpit(tmp_path, clear_failure) == LOG_FAILURE
    assert any(
        "mailpit-clear" in " ".join(command) and "django-test-dt5qx" in command
        for command, _environment, _capture in clear_failure.calls
    )
    assert clear_failure.calls[-1][0][-4:] == ("rm", "-f", "-s", "mailpit-tm7bh")

    both_clear_fail = FakeRunner(results=[LOG_FAILURE, IMAGE_FAILURE, 0])
    assert platform.finalize_testing_mailpit(tmp_path, both_clear_fail) == LOG_FAILURE
    assert all(
        "mailpit-empty" not in " ".join(command)
        for command, _environment, _capture in both_clear_fail.calls
    )

    empty_failure = FakeRunner(results=[0, 0, 0, LOG_FAILURE, 0])
    assert platform.finalize_testing_mailpit(tmp_path, empty_failure) == LOG_FAILURE
    assert empty_failure.calls[-1][0][-4:] == ("rm", "-f", "-s", "mailpit-tm7bh")

    stop_failure = FakeRunner(results=[0, 0, 0, 0, LOG_FAILURE])
    assert platform.finalize_testing_mailpit(tmp_path, stop_failure) == LOG_FAILURE


def test_confirmed_development_reset_is_explicitly_destructive(tmp_path: Path) -> None:
    """Delete only development volumes before a clean rebuild.

    Confirms the named destructive command uses no-cache builds and pinned-image pulls while
    remaining scoped to the development Compose project.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If reset omits deletion, cache invalidation, or image pulls.
    """
    environment_files(tmp_path)
    runner = FakeRunner()

    code = platform.main(
        ["development-reset", "--confirm-destroy-data"],
        root=tmp_path,
        runner=runner,
    )

    commands = [call[0] for call in runner.calls]
    assert code == platform.EXIT_OK
    assert commands[0][-3:] == ("down", "--volumes", "--remove-orphans")
    assert commands[1][-5:] == (
        "build",
        "--no-cache",
        "--pull",
        "postgres-pg3ka",
        "django-uv5n2",
    )
    assert commands[2][-2:] == ("pull", "--ignore-buildable")
    assert "--volumes" not in commands[3]


def test_proxy_only_rebuild_uses_compose_up_with_the_port_override(tmp_path: Path) -> None:
    """Rebuild development without creating a persistent one-off container.

    Confirms the Windows-safe path stays on Compose ``up`` with the registered project and
    proxy-only override, so Docker Desktop can group Django with the development project.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If the workaround uses ``compose run`` or omits the port override.
    """
    environment_files(tmp_path)
    runner = FakeRunner()

    code = platform.main(
        ["development-rebuild", "--proxy-only"],
        root=tmp_path,
        runner=runner,
        sleep=lambda _seconds: None,
        now=iter((0.0, 181.0)).__next__,
    )

    commands = [call[0] for call in runner.calls]
    assert code == platform.EXIT_OK
    assert all("run" not in command for command in commands)
    start_command = commands[1]
    assert start_command[2:4] == ("--project-name", "localforge-dev")
    assert "compose.proxy-only.yaml" in start_command
    assert start_command[-8:] == (
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        "600",
        "--no-build",
        "--force-recreate",
        "--remove-orphans",
    )


def test_health_and_logs_use_public_operational_seams(tmp_path: Path) -> None:
    """Route readiness and log commands through their environment adapters.

    Verifies development uses the aggregate Traefik endpoint, testing uses host-mode values, and
    logs retain both tail and follow controls.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If probes use the wrong environment or log options disappear.
    """
    environment_files(tmp_path)
    runner = FakeRunner(
        outputs=[
            json.dumps(environment_records(platform.DEVELOPMENT)),
            json.dumps(environment_records(platform.TESTING)),
        ]
    )

    development_code = platform.main(
        ["development-health"],
        root=tmp_path,
        runner=runner,
        http_probe=lambda url, host: (
            url == "http://127.0.0.1:8080/health/" and host == "localforge.localhost"
        ),
    )
    testing_code = platform.main(["testing-health"], root=tmp_path, runner=runner)
    logs_code = platform.main(
        ["testing-logs", "--follow", "postgres-tp8vn"],
        root=tmp_path,
        runner=runner,
    )

    assert development_code == testing_code == logs_code == platform.EXIT_OK
    testing_wait = runner.calls[4]
    assert testing_wait[1] is not None
    assert testing_wait[1]["POSTGRES_HOST"] == "127.0.0.1"
    assert runner.calls[-1][0][-6:] == (
        "docker",
        "logs",
        "--tail",
        "200",
        "--follow",
        "postgres-tp8vn",
    )


def test_logs_discover_every_project_container_and_propagate_failures(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """List one bounded tail per project container and validate follow mode.

    Exercises one-off container discovery, empty-project guidance, invalid multi-container follow,
    and the first failed Docker logs command.

    Arguments:
        tmp_path: Temporary repository root.
        capsys: Fixture capturing usage and empty-state messages.

    Returns:
        None.

    Raises:
        AssertionError: If log discovery is incomplete or failures are hidden.
    """
    environment_files(tmp_path)
    discovery_runner = FakeRunner(outputs=["django-uv5n2\ntraefik-tk2jp"])
    empty_runner = FakeRunner(outputs=[""])
    discovery_failure_runner = FakeRunner(results=[LOG_FAILURE])
    failure_runner = FakeRunner(results=[0, LOG_FAILURE], outputs=["django-uv5n2"])

    success = platform.main(["development-logs"], root=tmp_path, runner=discovery_runner)
    empty = platform.main(["testing-logs"], root=tmp_path, runner=empty_runner)
    invalid = platform.main(
        ["development-logs", "--follow", "django-uv5n2", "traefik-tk2jp"],
        root=tmp_path,
        runner=FakeRunner(),
    )
    discovery_failure = platform.main(
        ["development-logs"],
        root=tmp_path,
        runner=discovery_failure_runner,
    )
    failure = platform.main(["development-logs"], root=tmp_path, runner=failure_runner)

    assert success == 0
    assert discovery_runner.calls[1][0][-1] == "django-uv5n2"
    assert discovery_runner.calls[2][0][-1] == "traefik-tk2jp"
    assert empty == platform.EXIT_FAILED
    assert invalid == platform.EXIT_USAGE
    assert discovery_failure == LOG_FAILURE
    assert failure == LOG_FAILURE
    output = capsys.readouterr().out
    assert "no testing containers found" in output
    assert "--follow requires exactly one container name" in output


def test_missing_environment_and_invalid_extra_arguments_fail_before_commands(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject incomplete configuration and misplaced log arguments.

    Ensures validation happens before any subprocess so usage mistakes cannot partially alter an
    environment.

    Arguments:
        tmp_path: Temporary repository root.
        capsys: Fixture capturing command guidance.

    Returns:
        None.

    Raises:
        AssertionError: If Docker is invoked for invalid input.
    """
    runner = FakeRunner()

    missing_code = platform.main(["testing-up"], root=tmp_path, runner=runner)
    service_code = platform.main(
        ["development-status", "django-uv5n2"],
        root=tmp_path,
        runner=runner,
    )
    follow_code = platform.main(
        ["development-status", "--follow"],
        root=tmp_path,
        runner=runner,
    )

    assert (missing_code, service_code, follow_code) == (
        platform.EXIT_USAGE,
        platform.EXIT_USAGE,
        platform.EXIT_USAGE,
    )
    assert runner.calls == []
    assert "run `./localforge.sh setup` first" in capsys.readouterr().out


def test_poe_exposes_every_operator_alias_with_help() -> None:
    """Bind the documented operator commands to Poe.

    Reads the checked-in task table and requires every help command to delegate to the shared
    command module with a concise Poe description.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an alias is missing, undocumented, or bypasses the command module.
    """
    configuration = tomllib.loads(
        (platform.REPOSITORY_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    )
    tasks = configuration["tool"]["poe"]["tasks"]
    expected = {
        line.split()[3]
        for line in platform.HELP_TEXT.splitlines()
        if line.lstrip().startswith("./localforge.sh ")
    }

    assert expected <= set(tasks)
    for name in expected:
        task = tasks[name]
        assert task["help"]
        assert task["cmd"].startswith("python -m scripts.manage_platform")


def test_host_runner_reports_success_and_start_failure(tmp_path: Path) -> None:
    """Adapt subprocess outcomes without raising through the command interface.

    Covers captured success output and an unstartable command, preserving the stable result object
    used by every operation.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If output or process-start failure is misreported.
    """
    runner = platform.HostRunner(tmp_path)
    completed = SimpleNamespace(returncode=0, stdout="ready\n")

    with patch("scripts.manage_platform.subprocess.run", return_value=completed):
        success = runner.run(("tool", "status"), capture=True)
    with patch("scripts.manage_platform.subprocess.run", side_effect=OSError("missing")):
        failure = runner.run(("missing",))

    assert success == platform.CommandResult(code=0, output="ready")
    assert failure == platform.CommandResult(code=1, output="")


def test_setup_and_secret_commands_propagate_helper_results(tmp_path: Path) -> None:
    """Cover prerequisite, generation, and explicit decryption outcomes.

    Verifies setup stops at preflight, generation exposes its result, and decryption stops before
    generation when either committed artifact cannot be recovered.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If helper failures are hidden or later commands still run.
    """
    environment_files(tmp_path)

    preflight_runner = FakeRunner(results=[PREFLIGHT_FAILURE])
    assert platform.main(["setup"], root=tmp_path, runner=preflight_runner) == PREFLIGHT_FAILURE
    generator_runner = FakeRunner(results=[GENERATION_FAILURE])
    assert (
        platform.main(["secrets-generate"], root=tmp_path, runner=generator_runner)
        == GENERATION_FAILURE
    )
    decrypt_runner = FakeRunner(results=[0, DECRYPT_FAILURE])
    assert (
        platform.main(["secrets-decrypt"], root=tmp_path, runner=decrypt_runner) == DECRYPT_FAILURE
    )
    success_runner = FakeRunner()
    assert platform.main(["secrets-decrypt"], root=tmp_path, runner=success_runner) == 0
    assert len(success_runner.calls) == DECRYPT_AND_GENERATE_COMMAND_COUNT


def test_setup_handles_absent_and_successfully_decrypted_artifacts(tmp_path: Path) -> None:
    """Cover fresh generation and successful encrypted recovery.

    Exercises both the no-ciphertext path and the path where each committed artifact decrypts
    before the generator fills derived files.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If either safe setup path omits final generation.
    """
    no_artifacts_runner = FakeRunner()
    assert platform.main(["setup"], root=tmp_path, runner=no_artifacts_runner) == 0
    assert len(no_artifacts_runner.calls) == DECRYPT_COMMAND_COUNT

    (tmp_path / ".env.development.sops").write_text("ciphertext", encoding="utf-8")
    (tmp_path / ".env.testing.sops").write_text("ciphertext", encoding="utf-8")
    decrypted_runner = FakeRunner()
    assert platform.main(["setup"], root=tmp_path, runner=decrypted_runner) == 0
    assert len(decrypted_runner.calls) == SETUP_DECRYPT_COMMAND_COUNT


@pytest.mark.parametrize(
    ("command", "expected_tail"),
    [
        ("development-build", ("build", "postgres-pg3ka", "django-uv5n2")),
        ("testing-build", ("build", "django-test-dt5qx")),
        ("development-down", ("down", "--remove-orphans")),
        ("testing-down", ("down", "--remove-orphans")),
    ],
)
def test_simple_environment_commands_delegate_to_compose(
    tmp_path: Path,
    command: str,
    expected_tail: tuple[str, ...],
) -> None:
    """Keep symmetric commands on one Compose adapter.

    Parameterizes the simple build, stop, status, and logs operations for both environments so
    their public names cannot drift apart.

    Arguments:
        tmp_path: Temporary repository root.
        command: Public command to invoke.
        expected_tail: Compose arguments expected after the shared prefix.

    Returns:
        None.

    Raises:
        AssertionError: If the command targets another environment or operation.
    """
    environment_files(tmp_path)
    runner = FakeRunner()

    code = platform.main([command], root=tmp_path, runner=runner)

    assert code == 0
    assert runner.calls[-1][0][-len(expected_tail) :] == expected_tail


@pytest.mark.parametrize(
    ("command", "project"),
    [
        ("development-status", "localforge-dev"),
        ("testing-status", "localforge-test"),
    ],
)
def test_status_includes_one_off_containers(
    tmp_path: Path,
    command: str,
    project: str,
) -> None:
    """Inspect containers by project label rather than Compose service state.

    Includes the proxy-only Django one-off container and any stopped project container while
    remaining scoped away from other users' Docker resources.

    Arguments:
        tmp_path: Temporary repository root.
        command: Public status command.
        project: Expected Compose project label.

    Returns:
        None.

    Raises:
        AssertionError: If status uses an incomplete or unscoped listing.
    """
    environment_files(tmp_path)
    runner = FakeRunner()

    code = platform.main([command], root=tmp_path, runner=runner)

    assert code == 0
    assert runner.calls[0][0] == (
        "docker",
        "ps",
        "-a",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--format",
        "{{.Names}}\t{{.Status}}",
    )


def test_compose_commands_pin_the_registered_project_name() -> None:
    """Pin ownership independently of dotenv project-name expansion.

    Exercises the shared Compose command seam for both environments and the proxy-only override,
    preventing commands from silently falling back to a directory-derived project.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a command omits or changes a registered project name.
    """
    development = platform.compose_command(platform.DEVELOPMENT, "up", "-d")
    testing = platform.compose_command(platform.TESTING, "up", "-d")
    proxy_only = platform.compose_command(
        platform.DEVELOPMENT,
        "up",
        "-d",
        proxy_only=True,
    )

    assert development[2:4] == ("--project-name", "localforge-dev")
    assert testing[2:4] == ("--project-name", "localforge-test")
    assert "compose.proxy-only.yaml" not in development
    assert "compose.proxy-only.yaml" in proxy_only


def test_compose_command_rejects_testing_proxy_override() -> None:
    """Reject the development-only overlay at the command-building seam.

    Prevents internal callers from constructing an invalid testing Compose project even if they
    bypass public argument validation.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        ValueError: Expected from the invalid environment and overlay combination.
    """
    with pytest.raises(ValueError, match="development only"):
        platform.compose_command(platform.TESTING, "up", "-d", proxy_only=True)


@pytest.mark.parametrize(
    "command",
    [
        "testing-build",
        "testing-up",
        "testing-down",
        "testing-rebuild",
        "testing-reset",
        "testing-status",
        "testing-health",
        "testing-logs",
        "testing-test-container",
        "testing-test-host",
        "testing-test-both",
        "testing-registration-timing-stability",
        "testing-verify",
        "development-build",
    ],
)
def test_proxy_only_is_rejected_before_any_command(
    tmp_path: Path,
    command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject unsupported proxy-only combinations before side effects.

    Includes confirmed destructive testing reset and representative read-only and development
    commands so the public allowlist cannot widen accidentally.

    Arguments:
        tmp_path: Temporary repository root.
        command: Unsupported command to invoke.
        capsys: Fixture capturing usage guidance.

    Returns:
        None.

    Raises:
        AssertionError: If validation starts a subprocess or returns success.
    """
    environment_files(tmp_path)
    runner = FakeRunner()
    arguments = [command, "--proxy-only"]
    if command == "testing-reset":
        arguments.append("--confirm-destroy-data")

    code = platform.main(arguments, root=tmp_path, runner=runner)

    assert code == platform.EXIT_USAGE
    assert runner.calls == []
    assert "development-up" in capsys.readouterr().out


def test_testing_up_starts_the_registered_runner_without_running_tests(tmp_path: Path) -> None:
    """Create the persistent test runner through Compose ``up``.

    Requires the default testing environment to include its registered Django container while
    ensuring setup never invokes a one-off command or either complete test task.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If testing setup omits Django or executes tests.
    """
    environment_files(tmp_path)
    runner = ImageStateRunner(present_images=set(platform.SETUP_IMAGES))

    code = platform.main(["testing-up"], root=tmp_path, runner=runner)

    assert code == platform.EXIT_OK
    commands = [call[0] for call in runner.calls]
    start = next(command for command in commands if "up" in command)
    assert "django-test-dt5qx" in start
    assert "--no-build" in start
    assert all("run" not in command for command in commands)
    assert all(command != ("uv", "run", "poe", "test") for command in commands)


def test_inventory_evaluation_catches_independent_and_stale_containers() -> None:
    """Reject the exact Docker Desktop ownership symptoms.

    Supplies a persistent Compose one-off, a generated test-run name, a wrong project, and a
    duplicate service so the fast audit must identify each concrete ownership failure.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an observed independent-container cause escapes the audit.
    """
    containers: list[dict[str, object]] = [
        {
            "Name": "/django-uv5n2",
            "Config": {
                "Image": "localforge/django:0.1.0",
                "Labels": {
                    "com.docker.compose.project": "localforge-dev",
                    "com.docker.compose.service": "django-uv5n2",
                    "com.docker.compose.oneoff": "True",
                },
            },
            "State": {"Status": "running", "Health": {"Status": "healthy"}},
        },
        {
            "Name": "/django-test-dt5qx-run-69de59f2b448",
            "Config": {
                "Image": "localforge/django-test:0.1.0",
                "Labels": {
                    "com.docker.compose.project": "localforge-test",
                    "com.docker.compose.service": "django-test-dt5qx",
                    "com.docker.compose.oneoff": "True",
                },
            },
            "State": {"Status": "running"},
        },
        {
            "Name": "/postgres-tp8vn",
            "Config": {
                "Image": "docker.io/library/postgres:18.6",
                "Labels": {
                    "com.docker.compose.project": "localforge-dev",
                    "com.docker.compose.service": "postgres-tp8vn",
                    "com.docker.compose.oneoff": "False",
                },
            },
            "State": {"Status": "running", "Health": {"Status": "healthy"}},
        },
        {
            "Name": "/postgres-tp8vn-copy",
            "Config": {
                "Image": "docker.io/library/postgres:18.6",
                "Labels": {
                    "com.docker.compose.project": "localforge-test",
                    "com.docker.compose.service": "django-test-dt5qx",
                    "com.docker.compose.oneoff": "False",
                },
            },
            "State": {"Status": "running", "Health": {"Status": "healthy"}},
        },
    ]

    failures = platform.evaluate_container_inventory(containers)

    assert any("one-off django-uv5n2" in failure for failure in failures)
    assert any(
        "generated-run-name django-test-dt5qx-run-69de59f2b448" in failure for failure in failures
    )
    assert any("wrong-project postgres-tp8vn" in failure for failure in failures)
    assert any(
        "duplicate-service localforge-test/django-test-dt5qx" in failure for failure in failures
    )


def test_inventory_rejects_wrong_images_and_missing_healthchecks() -> None:
    """Reject containers that only look structurally owned.

    Uses correct names and Compose labels while changing Django's image and removing its health
    object, proving both registry checks are independently enforced.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If image or health drift passes the inventory audit.
    """
    wrong_image = container_record("django-uv5n2", image="docker.io/library/alpine:3.22")
    missing_health = container_record("django-uv5n2", health=None)
    canonical_postgres = container_record(
        "postgres-tp8vn",
        image="docker.io/library/postgres:18.6",
    )

    image_failures = platform.evaluate_container_inventory([wrong_image], require_complete=False)
    health_failures = platform.evaluate_container_inventory(
        [missing_health], require_complete=False
    )
    canonical_failures = platform.evaluate_container_inventory(
        [canonical_postgres],
        require_complete=False,
    )

    assert set(platform.CONTAINER_IMAGES) == set(platform.CONTAINER_PROJECTS)
    assert any(failure.startswith("wrong-image django-uv5n2 ") for failure in image_failures)
    assert "missing-healthcheck django-uv5n2" in health_failures
    assert all(not failure.startswith("wrong-image") for failure in canonical_failures)


def test_environment_health_rejects_missing_stopped_and_unhealthy_containers() -> None:
    """Validate every required container before environment-level probes.

    Exercises missing, stopped, unhealthy, and intentionally probeless services against the exact
    environment registry.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an invalid required-container state is accepted.
    """
    healthy = environment_records(platform.DEVELOPMENT)
    missing = [record for record in healthy if record["Name"] != "/django-uv5n2"]
    stopped = [
        container_record("django-uv5n2", status="exited"),
        *[record for record in healthy if record["Name"] != "/django-uv5n2"],
    ]
    unhealthy = [
        container_record("django-uv5n2", health="unhealthy"),
        *[record for record in healthy if record["Name"] != "/django-uv5n2"],
    ]

    assert "missing-container django-uv5n2" in platform.evaluate_environment_health(
        missing,
        platform.DEVELOPMENT,
    )
    assert "not-running django-uv5n2 status=exited" in platform.evaluate_environment_health(
        stopped,
        platform.DEVELOPMENT,
    )
    assert "not-healthy django-uv5n2 health=unhealthy" in platform.evaluate_environment_health(
        unhealthy,
        platform.DEVELOPMENT,
    )
    assert platform.evaluate_environment_health(healthy, platform.DEVELOPMENT) == []


def test_public_health_fails_before_probes_when_container_state_is_invalid(
    tmp_path: Path,
) -> None:
    """Stop health verification before probes when Docker state is incomplete.

    Supplies a missing testing runner and proves neither Compose status nor the service helper runs
    after the container inventory fails.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If health ignores the missing persistent test service.
    """
    environment_files(tmp_path)
    records = environment_records(platform.TESTING)
    records = [record for record in records if record["Name"] != "/django-test-dt5qx"]
    runner = FakeRunner(outputs=[json.dumps(records)])

    code = platform.main(["testing-health"], root=tmp_path, runner=runner)

    assert code == platform.EXIT_FAILED
    assert len(runner.calls) == 1
    assert runner.calls[0][0][0:3] == ("docker", "container", "inspect")


def test_timed_environment_setup_never_runs_application_tests(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Measure setup phases while leaving both environments running.

    Exercises the agent-runnable setup workflow and requires named timing output, explicit project
    ownership, cached builds, persistent ``up`` commands, and no test execution.

    Arguments:
        tmp_path: Temporary repository root.
        capsys: Fixture capturing phase timing output.

    Returns:
        None.

    Raises:
        AssertionError: If setup runs tests, uses one-offs, or omits phase timing.
    """
    environment_files(tmp_path)
    runner = FakeRunner()
    clock = iter(float(value) for value in range(40)).__next__

    with (
        patch.object(platform, "wait_and_verify_environment", return_value=0),
        patch.object(platform, "docker_audit", return_value=0),
    ):
        code = platform.main(
            ["environments-setup", "--proxy-only"],
            root=tmp_path,
            runner=runner,
            http_probe=lambda _url, _host: True,
            sleep=lambda _seconds: None,
            now=clock,
        )

    assert code == platform.EXIT_OK
    commands = [call[0] for call in runner.calls]
    flat = [argument for command in commands for argument in command]
    assert "run" not in flat
    assert ("uv", "run", "poe", "test") not in commands
    assert "--no-cache" not in flat
    assert "--project-name" in flat
    development_start = next(
        command for command in commands if "up" in command and "localforge-dev" in command
    )
    testing_start = next(
        command for command in commands if "up" in command and "localforge-test" in command
    )
    assert "compose.proxy-only.yaml" in development_start
    assert "compose.proxy-only.yaml" not in testing_start
    output = capsys.readouterr().out
    assert "phase=environment" in output
    assert "phase=images" in output
    assert "phase=development-compose-create-start" in output
    assert "phase=development-health-wait" in output
    assert "phase=testing-compose-create-start" in output
    assert "phase=testing-health-wait" in output
    assert "phase=total" in output


def test_missing_image_setup_builds_each_unique_local_image_once() -> None:
    """Build every missing local image through one representative service.

    Starts from an empty image store and proves the shared pgBackRest image has one build target
    even though two persistent services consume it.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If setup duplicates or omits a required local image build.
    """
    runner = ImageStateRunner()

    code = platform.ensure_setup_images(runner)

    assert code == platform.EXIT_OK
    build_commands = [call[0] for call in runner.calls if "build" in call[0]]
    assert len(build_commands) == ENVIRONMENT_COUNT
    assert build_commands[0][-3:] == ("build", "postgres-pg3ka", "django-uv5n2")
    assert build_commands[1][-2:] == ("build", "django-test-dt5qx")
    build_targets = [argument for command in build_commands for argument in command]
    assert build_targets.count("postgres-pg3ka") == 1
    assert "pgbackrest-pb2wj" not in build_targets


def test_existing_image_setup_rebuilds_local_images_and_uses_no_build_up(
    tmp_path: Path,
) -> None:
    """Refresh local images before starting without Compose-time builds.

    Models a repeated setup run with every exact image tag present and requires cached local builds
    before both Compose starts use the no-build path.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If a local image is stale, an external image is pulled, or Compose builds.
    """
    environment_files(tmp_path)
    runner = ImageStateRunner(present_images=set(platform.SETUP_IMAGES))
    with (
        patch.object(platform, "wait_and_verify_environment", return_value=0) as health,
        patch.object(platform, "docker_audit", return_value=0) as audit,
    ):
        code = platform.main(
            ["environments-setup", "--proxy-only"],
            root=tmp_path,
            runner=runner,
            now=iter(float(value) for value in range(40)).__next__,
        )

    commands = [call[0] for call in runner.calls]
    starts = [command for command in commands if "up" in command]
    builds = [command for command in commands if "build" in command]
    assert code == platform.EXIT_OK
    assert all("pull" not in command for command in commands)
    assert len(builds) == ENVIRONMENT_COUNT
    assert builds[0][-3:] == ("build", "postgres-pg3ka", "django-uv5n2")
    assert builds[1][-2:] == ("build", "django-test-dt5qx")
    assert len(starts) == ENVIRONMENT_COUNT
    assert all("--no-build" in command for command in starts)
    assert all("--force-recreate" not in command for command in starts)
    assert health.call_count == ENVIRONMENT_COUNT
    audit.assert_called_once()


def test_partial_image_setup_refreshes_every_local_image() -> None:
    """Refresh every local image from a partial Docker cache.

    Keeps every external image and one local image present while requiring all local targets to be
    rebuilt exactly once through their representative services.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If setup omits a local target or duplicates a shared-image target.
    """
    missing = {"localforge/pgbackrest:18.6", "localforge/django-test:0.1.0"}
    runner = ImageStateRunner(present_images=set(platform.SETUP_IMAGES) - missing)

    code = platform.ensure_setup_images(runner)

    build_commands = [call[0] for call in runner.calls if "build" in call[0]]
    assert code == platform.EXIT_OK
    assert build_commands[0][-3:] == ("build", "postgres-pg3ka", "django-uv5n2")
    assert build_commands[1][-2:] == ("build", "django-test-dt5qx")
    assert all("pgbackrest-pb2wj" not in command for command in build_commands)


def test_ordinary_up_and_testing_reset_cover_environment_specific_steps(tmp_path: Path) -> None:
    """Start development from present images and reset only testing data.

    Contrasts the safe ordinary start with an explicitly confirmed testing reset, including each
    environment's registered storage endpoint.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If environment-specific service or storage arguments drift.
    """
    environment_files(tmp_path)
    development_runner = ImageStateRunner(present_images=set(platform.SETUP_IMAGES))
    testing_runner = FakeRunner()

    development_code = platform.main(
        ["development-up"],
        root=tmp_path,
        runner=development_runner,
    )
    testing_code = platform.main(
        ["testing-reset", "--confirm-destroy-data"],
        root=tmp_path,
        runner=testing_runner,
    )

    assert development_code == testing_code == 0
    development_start = next(call[0] for call in development_runner.calls if "up" in call[0])
    assert "--no-build" in development_start
    assert "--build" not in development_start
    development_seed = next(
        call[0] for call in development_runner.calls if "seed_storage.py" in call[0][1]
    )
    assert development_seed[-1] == "http://127.0.0.1:8333"
    assert testing_runner.calls[0][0][-3:] == ("down", "--volumes", "--remove-orphans")
    assert testing_runner.calls[1][0][-4:] == (
        "build",
        "--no-cache",
        "--pull",
        "django-test-dt5qx",
    )
    assert testing_runner.calls[-1][0][-1] == "http://127.0.0.1:28333"


@pytest.mark.parametrize(
    ("results", "expected", "call_count"),
    [
        ([8], 8, 1),
    ],
)
def test_proxy_only_start_propagates_each_failure(
    tmp_path: Path,
    results: list[int],
    expected: int,
    call_count: int,
) -> None:
    """Stop proxy-only startup at the first failed stage.

    Exercises the single Compose-up failure so no later storage operation runs after a broken
    prerequisite.

    Arguments:
        tmp_path: Temporary repository root.
        results: Fabricated command outcomes.
        expected: Exit code that must propagate.
        call_count: Number of commands permitted before stopping.

    Returns:
        None.

    Raises:
        AssertionError: If a later stage runs after failure.
    """
    environment_files(tmp_path)
    runner = FakeRunner(results=results)

    with patch.object(platform, "ensure_environment_images", return_value=0):
        code = platform.main(
            ["development-up", "--proxy-only"],
            root=tmp_path,
            runner=runner,
            sleep=lambda _seconds: None,
            now=iter((0.0, 1.0)).__next__,
        )

    assert code == expected
    assert len(runner.calls) == call_count


def test_health_propagates_status_and_probe_failures(tmp_path: Path) -> None:
    """Report Compose and HTTP readiness failures exactly.

    Separates a failed status command from an unhealthy aggregate endpoint so neither becomes a
    success-shaped result.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If health hides either failure.
    """
    environment_files(tmp_path)
    status_runner = FakeRunner(
        results=[0, STATUS_FAILURE],
        outputs=[json.dumps(environment_records(platform.DEVELOPMENT))],
    )
    probe_runner = FakeRunner(outputs=[json.dumps(environment_records(platform.DEVELOPMENT))])

    status_code = platform.main(
        ["development-health"],
        root=tmp_path,
        runner=status_runner,
    )
    probe_code = platform.main(
        ["development-health"],
        root=tmp_path,
        runner=probe_runner,
        http_probe=lambda _url, _host: False,
    )

    assert status_code == STATUS_FAILURE
    assert probe_code == 1


def test_internal_start_rebuild_and_reset_failures_stop_immediately(tmp_path: Path) -> None:
    """Cover configuration and staged-operation failure guards.

    Calls the shared operations directly to prove missing configuration, failed build, failed
    teardown, and failed image pull all stop the workflow.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If a guarded helper continues after failure.
    """
    missing_runner = FakeRunner()
    assert (
        platform.up(
            tmp_path,
            missing_runner,
            platform.DEVELOPMENT,
            recreate=False,
            proxy_only=False,
        )
        == platform.EXIT_USAGE
    )
    assert (
        platform.rebuild(
            tmp_path,
            missing_runner,
            platform.TESTING,
            proxy_only=False,
        )
        == platform.EXIT_USAGE
    )
    assert (
        platform.reset(
            tmp_path,
            missing_runner,
            platform.TESTING,
            confirmed=True,
            proxy_only=False,
        )
        == platform.EXIT_USAGE
    )

    environment_files(tmp_path)
    assert (
        platform.rebuild(
            tmp_path,
            FakeRunner(results=[PREFLIGHT_FAILURE]),
            platform.TESTING,
            proxy_only=False,
        )
        == PREFLIGHT_FAILURE
    )
    for results, expected in (([8], 8), ([0, 9], 9), ([0, 0, 10], 10)):
        assert (
            platform.reset(
                tmp_path,
                FakeRunner(results=results),
                platform.DEVELOPMENT,
                confirmed=True,
                proxy_only=False,
            )
            == expected
        )


def test_testing_modes_propagate_start_and_health_failures(tmp_path: Path) -> None:
    """Stop dual-mode execution when environment preparation is not healthy.

    Exercises both prerequisite boundaries through the public command so no collection or suite
    begins against an unavailable testing stack.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If a prerequisite failure is hidden or testing begins.
    """
    environment_files(tmp_path)

    with patch.object(platform, "up", return_value=IMAGE_FAILURE):
        assert (
            platform.main(
                ["testing-test-both"],
                root=tmp_path,
                runner=FakeRunner(),
            )
            == IMAGE_FAILURE
        )
    with (
        patch.object(platform, "up", return_value=0),
        patch.object(platform, "health", return_value=LOG_FAILURE),
    ):
        assert (
            platform.main(
                ["testing-test-both"],
                root=tmp_path,
                runner=FakeRunner(),
            )
            == LOG_FAILURE
        )
    with (
        patch.object(platform, "up", return_value=0),
        patch.object(platform, "health", return_value=0),
        patch.object(platform, "start_testing_mailpit", return_value=LOG_FAILURE),
        patch.object(platform, "execute_testing_modes") as execute,
        patch.object(platform, "finalize_testing_mailpit") as finalize,
    ):
        assert (
            platform.main(
                ["testing-test-both"],
                root=tmp_path,
                runner=FakeRunner(),
            )
            == LOG_FAILURE
        )
    execute.assert_not_called()
    finalize.assert_not_called()


def test_selected_host_test_tasks_use_the_mailpit_lifecycle(tmp_path: Path) -> None:
    """Route every public focused host task through one dependency lifecycle.

    Verifies command dispatch, startup failures, successful execution, post-checks, log audit, and
    cleanup while preserving the selected internal Poe task and first failure code.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If routing, failure propagation, post-checking, or cleanup drifts.
    """
    environment_files(tmp_path)
    with patch.object(platform, "testing_host_task", return_value=0) as selected:
        for command, task in platform.HOST_TEST_TASKS.items():
            assert platform.main([command], root=tmp_path, runner=FakeRunner()) == 0
            assert selected.call_args.args[2] == task

    with patch.object(platform, "up", return_value=IMAGE_FAILURE):
        assert (
            platform.testing_host_task(
                tmp_path,
                FakeRunner(),
                "test-integration-stages",
                http_probe=lambda _url, _host: True,
            )
            == IMAGE_FAILURE
        )
    with (
        patch.object(platform, "up", return_value=0),
        patch.object(platform, "health", return_value=LOG_FAILURE),
    ):
        assert (
            platform.testing_host_task(
                tmp_path,
                FakeRunner(),
                "test-integration-stages",
                http_probe=lambda _url, _host: True,
            )
            == LOG_FAILURE
        )
    with (
        patch.object(platform, "up", return_value=0),
        patch.object(platform, "health", return_value=0),
        patch.object(platform, "start_testing_mailpit", return_value=LOG_FAILURE),
    ):
        assert (
            platform.testing_host_task(
                tmp_path,
                FakeRunner(),
                "test-integration-stages",
                http_probe=lambda _url, _host: True,
            )
            == LOG_FAILURE
        )

    runner = FakeRunner(results=[LOG_FAILURE])
    with (
        patch.object(platform, "up", return_value=0),
        patch.object(platform, "health", return_value=0),
        patch.object(platform, "start_testing_mailpit", return_value=0),
        patch.object(platform, "docker_environment_audit", return_value=0),
        patch.object(platform, "audit_testing_container_set", return_value=0),
        patch.object(platform, "audit_testing_logs", return_value=0),
        patch.object(platform, "finalize_testing_mailpit", return_value=0) as cleanup,
    ):
        assert (
            platform.testing_host_task(
                tmp_path,
                runner,
                "test-integration-stages",
                http_probe=lambda _url, _host: True,
            )
            == LOG_FAILURE
        )

    assert runner.calls[0][0] == ("uv", "run", "poe", "test-integration-stages")
    cleanup.assert_called_once()


def test_testing_verify_propagates_rebuild_failure(tmp_path: Path) -> None:
    """Stop the high-level verification when rebuilding fails.

    Ensures neither test mode nor cleanup runs when the source-matched image could not be built.
    The failed testing stack remains available for diagnosis.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If tests start after a failed build.
    """
    environment_files(tmp_path)
    runner = FakeRunner(results=[REBUILD_FAILURE])

    code = platform.main(["testing-verify"], root=tmp_path, runner=runner)

    assert code == REBUILD_FAILURE
    assert len(runner.calls) == 1

    health_runner = FakeRunner(results=[0, 0, 0, 0, DECRYPT_FAILURE])
    with patch.object(platform, "inspect_environment_health", return_value=0):
        health_code = platform.main(["testing-verify"], root=tmp_path, runner=health_runner)

    assert health_code == DECRYPT_FAILURE
    assert all("--rm" not in call[0] for call in health_runner.calls)

    with patch.object(platform, "health", return_value=DECRYPT_FAILURE):
        direct_health_code = platform.testing_verify(
            tmp_path,
            FakeRunner(),
            http_probe=lambda _url, _host: True,
            now=lambda: 0.0,
        )

    assert direct_health_code == DECRYPT_FAILURE


def test_http_probe_handles_success_non_ok_and_transport_failure() -> None:
    """Convert HTTP responses and exceptions into readiness booleans.

    Covers the successful status, an HTTP response outside the success range, and an unavailable
    endpoint without exposing urllib exceptions to callers.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If probe outcomes are not stable booleans.
    """
    success = MagicMock()
    success.__enter__.return_value.status = 200
    unavailable = MagicMock()
    unavailable.__enter__.return_value.status = 503

    with patch("scripts.manage_platform.urllib.request.urlopen", return_value=success):
        assert platform.probe_http("http://example.test", "example.test")
    with patch("scripts.manage_platform.urllib.request.urlopen", return_value=unavailable):
        assert not platform.probe_http("http://example.test", "example.test")
    with patch(
        "scripts.manage_platform.urllib.request.urlopen",
        side_effect=urllib.error.URLError("unavailable"),
    ):
        assert not platform.probe_http("http://example.test", "example.test")


def test_docker_record_helpers_cover_malformed_and_root_label_shapes() -> None:
    """Normalize inspect records without trusting Docker's decoded shape.

    Covers root-level resource labels, malformed labels, nullable values, missing names, and Docker
    Hub image aliases used by the audit.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If defensive record normalization changes.
    """
    assert platform.record_labels({"Labels": {"project": "value", "empty": None}}) == {
        "project": "value"
    }
    assert platform.record_labels({"Config": {"Labels": "invalid"}}) == {}
    assert platform.record_name({}) == ""
    assert platform.normalize_image_name("docker.io/library/postgres:18.6") == "postgres:18.6"


def test_container_inventory_covers_unowned_stale_and_bad_runtime_states() -> None:
    """Exercise remaining negative container audit branches.

    Supplies unrelated, unowned, stale, wrong-service, stopped, unhealthy, and malformed-state
    records without requiring a complete environment.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any invalid LocalForge container escapes the audit.
    """
    records: list[dict[str, object]] = [
        {"Name": "/unrelated", "Config": {"Image": "alpine:3.22"}, "State": "bad"},
        {
            "Name": "/custom",
            "Config": {"Image": "localforge/django:0.1.0", "Labels": {}},
            "State": {"Status": "running"},
        },
        {
            "Name": "/stale-ab2cd",
            "Config": {
                "Image": "alpine:3.22",
                "Labels": {
                    "com.docker.compose.project": "localforge-dev",
                    "com.docker.compose.service": "stale-ab2cd",
                    "com.docker.compose.oneoff": "False",
                },
            },
            "State": "bad",
        },
        container_record("django-uv5n2", status="exited", health="unhealthy"),
    ]
    labels = records[-1]["Config"]
    assert isinstance(labels, dict)
    docker_labels = labels["Labels"]
    assert isinstance(docker_labels, dict)
    docker_labels["com.docker.compose.service"] = "wrong-service"

    failures = platform.evaluate_container_inventory(records, require_complete=True)

    assert any(failure.startswith("unowned custom ") for failure in failures)
    assert "stale-container stale-ab2cd" in failures
    assert any(failure.startswith("wrong-service django-uv5n2 ") for failure in failures)
    assert "not-running django-uv5n2 status=exited" in failures
    assert "not-healthy django-uv5n2 health=unhealthy" in failures
    assert "missing-container postgres-pg3ka" in failures


def test_environment_and_resource_evaluators_cover_defensive_branches() -> None:
    """Cover malformed container state and optional resource ownership branches.

    Requires probeless services to remain valid without health state while stale, wrong-project,
    and missing required resources produce stable failures.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If defensive health or resource behavior changes.
    """
    records = environment_records(platform.DEVELOPMENT)
    malformed: list[dict[str, object]] = [
        record for record in records if record["Name"] != "/django-uv5n2"
    ]
    malformed.append({"Name": "/django-uv5n2", "State": "bad"})
    health_failures = platform.evaluate_environment_health(malformed, platform.DEVELOPMENT)
    resources: list[dict[str, object]] = [
        {"Name": "unrelated", "Labels": {}},
        {"Name": "stale-net", "Labels": {"com.docker.compose.project": "localforge-dev"}},
        {
            "Name": "edge-net-ne2vk",
            "Labels": {"com.docker.compose.project": "localforge-test"},
        },
    ]
    resource_failures = platform.evaluate_resource_inventory(
        resources,
        platform.NETWORK_PROJECTS,
        "network",
        require_complete=True,
        required_names=frozenset({"edge-net-ne2vk", "app-net-na6hy"}),
    )

    assert "missing-state django-uv5n2" in health_failures
    assert "stale-network stale-net" in resource_failures
    assert any(
        failure.startswith("wrong-project-network edge-net-ne2vk ") for failure in resource_failures
    )
    assert "missing-network app-net-na6hy" in resource_failures


def test_inspect_decoding_and_object_collection_failure_paths() -> None:
    """Reject malformed Docker JSON and propagate listing or inspection failures.

    Exercises empty listings, malformed output, non-object members, successful key normalization,
    and both subprocess failure boundaries.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If inspection failures become success-shaped.
        ValueError: Expected for malformed decoded structures.
    """
    with pytest.raises(ValueError, match="object array"):
        platform.decode_inspect_output("{}")
    with pytest.raises(ValueError, match="object array"):
        platform.decode_inspect_output("[1]")
    assert platform.decode_inspect_output('[{"Name":"/ok"}]') == [{"Name": "/ok"}]

    list_failure = FakeRunner(results=[7])
    empty = FakeRunner(outputs=[""])
    inspect_failure = FakeRunner(results=[0, 8], outputs=["id"])
    malformed = FakeRunner(outputs=["id", "not-json"])
    success = FakeRunner(outputs=["id", '[{"Name":"/ok"}]'])
    arguments = (
        ("docker", "container", "ls", "-aq"),
        ("docker", "container", "inspect"),
    )

    assert platform.inspect_docker_objects(list_failure, *arguments) == (7, [])
    assert platform.inspect_docker_objects(empty, *arguments) == (0, [])
    assert platform.inspect_docker_objects(inspect_failure, *arguments) == (8, [])
    assert platform.inspect_docker_objects(malformed, *arguments) == (1, [])
    assert platform.inspect_docker_objects(success, *arguments) == (
        0,
        [{"Name": "/ok"}],
    )


def test_docker_audit_covers_clean_running_and_subprocess_failures() -> None:
    """Exercise complete clean, running, dirty, and failed Docker audits.

    Patches only the read-only object collector while retaining image-list parsing and every
    inventory evaluator.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If audit result propagation or image validation changes.
    """
    containers = [
        *environment_records(platform.DEVELOPMENT),
        *environment_records(platform.TESTING),
    ]
    networks = [
        {"Name": name, "Labels": {"com.docker.compose.project": project}}
        for name, project in platform.NETWORK_PROJECTS.items()
    ]
    volumes = [
        {"Name": name, "Labels": {"com.docker.compose.project": project}}
        for name, project in platform.VOLUME_PROJECTS.items()
        if name in platform.REQUIRED_VOLUMES
    ]
    images = "\n".join(sorted(platform.LOCALFORGE_IMAGES))

    with patch.object(
        platform,
        "inspect_docker_objects",
        side_effect=[(0, containers), (0, networks), (0, volumes)],
    ):
        assert platform.docker_audit(FakeRunner(outputs=[images]), expect_clean=False) == 0
    with patch.object(
        platform,
        "inspect_docker_objects",
        side_effect=[(0, []), (0, []), (0, [])],
    ):
        assert platform.docker_audit(FakeRunner(outputs=[""]), expect_clean=True) == 0
    with patch.object(
        platform,
        "inspect_docker_objects",
        side_effect=[(0, [container_record("django-uv5n2")]), (0, []), (0, [])],
    ):
        assert (
            platform.docker_audit(
                FakeRunner(outputs=["localforge/obsolete:1"]),
                expect_clean=True,
            )
            == 1
        )
    failure_results: tuple[list[tuple[int, list[dict[str, object]]]], ...] = (
        [(9, [])],
        [(0, []), (8, [])],
        [(0, []), (0, []), (7, [])],
    )
    for results in failure_results:
        with patch.object(platform, "inspect_docker_objects", side_effect=results):
            assert platform.docker_audit(FakeRunner(), expect_clean=False) != 0
    with patch.object(
        platform,
        "inspect_docker_objects",
        side_effect=[(0, []), (0, []), (0, [])],
    ):
        assert (
            platform.docker_audit(
                FakeRunner(results=[GENERATION_FAILURE]),
                expect_clean=False,
            )
            == GENERATION_FAILURE
        )


def test_health_inspection_waiting_and_verification_failures(tmp_path: Path) -> None:
    """Cover inspect, polling, storage, and readiness failure boundaries.

    Uses deterministic clocks and patched seams so timeout and malformed Docker output cannot hang
    the targeted suite.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If a failed health boundary is hidden.
    """
    environment_files(tmp_path)
    healthy = environment_records(platform.TESTING)
    malformed = FakeRunner(outputs=["not-json"])
    nonzero_complete = FakeRunner(
        results=[5],
        outputs=[json.dumps(healthy)],
    )
    assert platform.inspect_environment_health(malformed, platform.TESTING) == 1
    assert platform.inspect_environment_health(nonzero_complete, platform.TESTING) == 1

    success = FakeRunner(outputs=[json.dumps(healthy)])
    timeout = FakeRunner(results=[8])
    bad_json = FakeRunner(outputs=["bad"])
    assert (
        platform.wait_for_environment_containers(
            success,
            platform.TESTING,
            sleep=lambda _seconds: None,
            now=iter((0.0, 1.0)).__next__,
        )
        == 0
    )
    assert (
        platform.wait_for_environment_containers(
            timeout,
            platform.TESTING,
            sleep=lambda _seconds: None,
            now=iter((0.0, 1.0, 601.0)).__next__,
        )
        == 1
    )
    assert (
        platform.wait_for_environment_containers(
            bad_json,
            platform.TESTING,
            sleep=lambda _seconds: None,
            now=iter((0.0, 1.0, 601.0)).__next__,
        )
        == 1
    )

    with patch.object(platform, "wait_for_environment_containers", return_value=7):
        assert (
            platform.wait_and_verify_environment(
                tmp_path,
                FakeRunner(),
                platform.TESTING,
                http_probe=lambda _url, _host: True,
                sleep=lambda _seconds: None,
                now=lambda: 0.0,
            )
            == LOG_FAILURE
        )
    with (
        patch.object(platform, "wait_for_environment_containers", return_value=0),
        patch.object(platform, "seed_storage", return_value=8),
    ):
        assert (
            platform.wait_and_verify_environment(
                tmp_path,
                FakeRunner(),
                platform.TESTING,
                http_probe=lambda _url, _host: True,
                sleep=lambda _seconds: None,
                now=lambda: 0.0,
            )
            == IMAGE_FAILURE
        )


def test_image_planning_and_setup_failure_boundaries(tmp_path: Path) -> None:
    """Cover custom builds, failed image discovery, operations, and timed setup.

    Ensures invalid image operations raise, subprocess failures propagate, and an early setup phase
    prints total timing without running later phases.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If image or setup failures continue into later work.
        ValueError: Expected for an unsupported image operation.
    """
    environment_files(tmp_path)
    custom = FakeRunner()
    assert (
        platform.build(
            custom,
            platform.DEVELOPMENT,
            services=("django-uv5n2",),
        )
        == 0
    )
    assert custom.calls[0][0][-2:] == ("build", "django-uv5n2")
    with pytest.raises(ValueError, match="unsupported image operation"):
        platform.run_image_targets(FakeRunner(), (), set(), "invalid")
    assert (
        platform.run_image_targets(
            FakeRunner(results=[GENERATION_FAILURE]),
            platform.LOCAL_BUILD_TARGETS,
            set(platform.LOCALFORGE_IMAGES),
            "build",
        )
        == GENERATION_FAILURE
    )
    with patch.object(platform, "inspect_images", return_value=(LOG_FAILURE, set())):
        assert (
            platform.ensure_images(
                FakeRunner(),
                platform.LOCAL_BUILD_TARGETS,
                platform.EXTERNAL_PULL_TARGETS,
            )
            == LOG_FAILURE
        )
    with patch.object(platform, "inspect_images", return_value=(0, set())):
        assert (
            platform.ensure_images(
                FakeRunner(results=[IMAGE_FAILURE]),
                (),
                platform.EXTERNAL_PULL_TARGETS,
            )
            == IMAGE_FAILURE
        )
    image_list_failure = FakeRunner(results=[5])
    assert platform.inspect_images(image_list_failure, frozenset({"postgres:18.6"})) == (
        5,
        set(),
    )
    malformed_list = FakeRunner(outputs=["without-separator"])
    assert platform.inspect_images(malformed_list, frozenset({"postgres:18.6"})) == (
        0,
        set(),
    )
    clock = iter((0.0, 1.0, 2.0, 3.0)).__next__
    assert (
        platform.environments_setup(
            tmp_path,
            FakeRunner(results=[REBUILD_FAILURE]),
            proxy_only=False,
            http_probe=lambda _url, _host: True,
            sleep=lambda _seconds: None,
            now=clock,
        )
        == REBUILD_FAILURE
    )


def test_remaining_dispatch_and_health_failure_branches(tmp_path: Path) -> None:
    """Cover final up, health, verification, and command dispatch branches.

    Uses direct seams to keep the coverage gate exhaustive without invoking Docker or any complete
    application test workflow.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If a failure is hidden or a command dispatches incorrectly.
    """
    environment_files(tmp_path)
    with patch.object(
        platform,
        "ensure_environment_images",
        return_value=IMAGE_FAILURE,
    ):
        assert (
            platform.up(
                tmp_path,
                FakeRunner(),
                platform.DEVELOPMENT,
                recreate=False,
                proxy_only=False,
            )
            == IMAGE_FAILURE
        )

    missing_health = environment_records(platform.TESTING)
    missing_health[0] = {
        **missing_health[0],
        "State": {"Status": "running"},
    }
    assert "missing-healthcheck django-test-dt5qx" in platform.evaluate_environment_health(
        missing_health,
        platform.TESTING,
    )

    unhealthy = environment_records(platform.TESTING)
    unhealthy[0] = {
        **unhealthy[0],
        "State": {"Status": "running", "Health": {"Status": "starting"}},
    }
    runner = FakeRunner(outputs=[json.dumps(unhealthy)])
    assert (
        platform.wait_for_environment_containers(
            runner,
            platform.TESTING,
            sleep=lambda _seconds: None,
            now=iter((0.0, 1.0, 601.0)).__next__,
        )
        == 1
    )

    with (
        patch.object(platform, "wait_for_environment_containers", return_value=0),
        patch.object(platform, "seed_storage", return_value=0),
        patch.object(platform, "health", return_value=STATUS_FAILURE),
    ):
        assert (
            platform.wait_and_verify_environment(
                tmp_path,
                FakeRunner(),
                platform.TESTING,
                http_probe=lambda _url, _host: True,
                sleep=lambda _seconds: None,
                now=lambda: 0.0,
            )
            == STATUS_FAILURE
        )

    with (
        patch.object(platform, "rebuild", return_value=0),
        patch.object(platform, "health", return_value=0),
        patch.object(platform, "testing_test", return_value=GENERATION_FAILURE),
    ):
        assert (
            platform.testing_verify(
                tmp_path,
                FakeRunner(),
                http_probe=lambda _url, _host: True,
                now=lambda: 0.0,
            )
            == GENERATION_FAILURE
        )

    with patch.object(
        platform,
        "docker_audit",
        side_effect=(LOG_FAILURE, IMAGE_FAILURE),
    ) as audit:
        assert platform.main(["docker-audit"], root=tmp_path, runner=FakeRunner()) == LOG_FAILURE
        assert (
            platform.main(["docker-clean-check"], root=tmp_path, runner=FakeRunner())
            == IMAGE_FAILURE
        )
        assert audit.call_count == ENVIRONMENT_COUNT


def test_module_entry_point_defaults_to_help(capsys: pytest.CaptureFixture[str]) -> None:
    """Execute the module entry point through its public default.

    Runs the script as `__main__` with no arguments and observes the same help interface Poe
    exposes.

    Arguments:
        capsys: Fixture capturing help output.

    Returns:
        None.

    Raises:
        AssertionError: If module execution does not exit successfully with help.
    """
    with (
        patch.object(sys, "argv", ["manage_platform.py"]),
        pytest.raises(SystemExit) as captured,
    ):
        runpy.run_path(
            str(platform.REPOSITORY_ROOT / "backend" / "scripts" / "manage_platform.py"),
            run_name="__main__",
        )

    assert captured.value.code == 0
    assert "LocalForge environment commands" in capsys.readouterr().out
