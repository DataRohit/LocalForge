"""Unit tests for the live Docker convention auditor.

Exercises every registry comparison and command boundary without mutating the shared Docker engine,
leaving the real audit command as the end-to-end proof of current runtime state.
"""

from __future__ import annotations

import runpy
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from scripts import audit_naming as audit
from scripts import manage_platform as platform

pytestmark = pytest.mark.unit
FAILURE = 7
PAIR_COUNT = 2
WILDCARD_IPV4 = next(address for address in audit.WILDCARD_HOSTS if "." in address)
CONTAINER_MEMBERSHIPS = {
    **audit.DEVELOPMENT_MEMBERSHIPS,
    **audit.TESTING_MEMBERSHIPS,
}


@dataclass
class FakeRunner:
    """Record commands and return configured audit results.

    Supplies deterministic combined output and statuses in call order. Inherits nothing and
    structurally satisfies the audit runner protocol.

    Attributes:
        results: Exit codes consumed in call order.
        outputs: Captured output consumed by capture calls.
        calls: Commands observed.

    Members:
        run: Record one command and return its configured result.
    """

    results: list[int] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def run(
        self,
        command: tuple[str, ...],
        environment: dict[str, str] | None = None,
        *,
        capture: bool = False,
    ) -> platform.CommandResult:
        """Record one external command.

        Consumes configured results and captured output independently so tests can model every
        Docker list, inspect, and active probe boundary.

        Arguments:
            command: Argument vector requested by the audit.
            environment: Optional child environment, unused by this audit.
            capture: Whether captured output is requested.

        Returns:
            Configured process result.
        """
        del environment
        self.calls.append(command)
        code = self.results.pop(0) if self.results else platform.EXIT_OK
        output = self.outputs.pop(0) if capture and self.outputs else ""
        return platform.CommandResult(code=code, output=output)


def container_record(
    name: str,
    *,
    ports: dict[str, list[dict[str, str]]] | None = None,
    mounts: list[object] | None = None,
    proxy_only: bool = False,
) -> dict[str, object]:
    """Build one healthy registered container inspect record.

    Uses authoritative image and label registries while allowing focused port and mount overrides
    for convention-drift cases.

    Arguments:
        name: Registered container name.
        ports: Optional Docker port mapping.
        mounts: Optional Docker mount records.
        proxy_only: Whether the development proxy-only overlay created the container.

    Returns:
        Docker inspect-shaped mapping.
    """
    config_files = (
        "Q:\\projects\\localforge\\compose.yaml,Q:\\projects\\localforge\\compose.development.yaml"
    )
    if proxy_only:
        config_files = f"{config_files},Q:\\projects\\localforge\\compose.proxy-only.yaml"
    return {
        "Name": f"/{name}",
        "Config": {
            "Image": platform.CONTAINER_IMAGES[name],
            "Labels": {
                "com.docker.compose.project": platform.CONTAINER_PROJECTS[name],
                "com.docker.compose.service": name,
                "com.docker.compose.oneoff": "False",
                "com.docker.compose.project.config_files": config_files,
            },
        },
        "State": {"Status": "running", "Health": {"Status": "healthy"}},
        "NetworkSettings": {
            "Ports": ports or {},
            "Networks": {network: {} for network in CONTAINER_MEMBERSHIPS[name]},
        },
        "Mounts": mounts or [],
    }


def resource_records(
    projects: dict[str, str],
    names: set[str] | frozenset[str],
    *,
    internal: dict[str, bool] | None = None,
) -> list[dict[str, object]]:
    """Build labelled network or volume records.

    Retains project ownership and optional internal flags in the same shapes returned by Docker
    inspect.

    Arguments:
        projects: Name to Compose project mapping.
        names: Resource names to include.
        internal: Optional network internal flags.

    Returns:
        Docker inspect-shaped records.
    """
    return [
        {
            "Name": name,
            "Labels": {"com.docker.compose.project": projects[name]},
            **({"Internal": internal[name]} if internal is not None else {}),
        }
        for name in sorted(names)
    ]


def test_port_registry_and_normalization_cover_exact_and_malformed_bindings() -> None:
    """Normalize Docker bindings and detect missing, extra, and remapped ports.

    Exercises absent, malformed, exact, remapped, wrong-interface, and extra-publication shapes
    against the development registry.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If port comparison accepts drift.
    """
    ports = {
        "8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}],
    }
    record = container_record("django-uv5n2", ports=ports)
    malformed: dict[str, object] = {
        "Name": "/django-uv5n2",
        "NetworkSettings": {
            "Ports": {
                "7000/tcp": None,
                "8000/tcp": "invalid",
                "9000/tcp": [
                    {"HostIp": WILDCARD_IPV4, "HostPort": "9000"},
                    "invalid",
                    {"HostIp": "::", "HostPort": "9001"},
                ],
            }
        },
    }

    assert audit.live_port_bindings({"Name": "/empty"}) == {}
    assert audit.live_port_bindings(malformed) == {
        "8000/tcp": audit.LivePortBinding(
            host_ports=frozenset(),
            host_ips=frozenset(),
        ),
        "9000/tcp": audit.LivePortBinding(
            host_ports=frozenset({"9000", "9001"}),
            host_ips=frozenset({WILDCARD_IPV4, "::"}),
        ),
    }
    assert audit.live_port_bindings(record) == {
        "8000/tcp": audit.LivePortBinding(
            host_ports=frozenset({"8000"}),
            host_ips=frozenset({"127.0.0.1"}),
        )
    }
    assert audit.port_failures([record], audit.DEVELOPMENT_SPEC) == []
    proxy_only = container_record("django-uv5n2", proxy_only=True)
    assert audit.port_failures([proxy_only], audit.DEVELOPMENT_SPEC) == []
    assert audit.development_uses_proxy_only([record]) == (False, [])
    assert audit.development_uses_proxy_only([proxy_only]) == (True, [])
    assert audit.development_uses_proxy_only([]) == (None, [])
    assert audit.port_failures([], audit.DEVELOPMENT_SPEC) == []
    assert audit.port_failures([], audit.TESTING_SPEC) == []

    wrong = container_record(
        "django-uv5n2",
        ports={"8000/tcp": [{"HostIp": WILDCARD_IPV4, "HostPort": "9000"}]},
    )
    assert audit.port_failures([wrong], audit.DEVELOPMENT_SPEC)
    extra = container_record(
        "django-uv5n2",
        ports={
            "8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8000"}],
            "9000/tcp": [{"HostIp": WILDCARD_IPV4, "HostPort": "9000"}],
        },
    )
    assert audit.port_failures([extra], audit.DEVELOPMENT_SPEC)
    missing_label = container_record("django-uv5n2")
    labels = cast("dict[str, str]", cast("dict[str, object]", missing_label["Config"])["Labels"])
    del labels[audit.COMPOSE_CONFIG_FILES_LABEL]
    missing = "missing=['compose.development.yaml', 'compose.yaml'] actual=[]"
    expected = f"compose-config-files django-uv5n2 {missing}"
    assert audit.port_failures([missing_label], audit.DEVELOPMENT_SPEC) == [expected]


def test_mount_rules_reject_anonymous_writable_and_unregistered_mounts() -> None:
    """Validate exact bind destinations and named-volume ownership.

    Compares a valid testing runner with anonymous volumes, foreign names, writable system binds,
    and incomplete registered bind sets.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If unsafe or unregistered mounts are accepted.
    """
    assert audit.normalize_bind_source("/") == "/"
    assert audit.normalize_bind_source("/sys/") == "/sys"
    repository = str(audit.REPOSITORY_ROOT).replace("\\", "/")
    assert (
        audit.normalize_bind_source(f"{repository}/docker/seaweedfs/s3.json")
        == "docker/seaweedfs/s3.json"
    )
    with patch.object(audit, "REPOSITORY_ROOT", Path("Q:/projects/localforge")):
        assert (
            audit.normalize_bind_source(
                "/run/desktop/mnt/host/q/projects/localforge/docker/seaweedfs/s3.json"
            )
            == "docker/seaweedfs/s3.json"
        )

    valid = container_record(
        "django-test-dt5qx",
        mounts=[
            {
                "Type": "bind",
                "Source": str(audit.REPOSITORY_ROOT / ".env.development"),
                "Destination": "/app/.env.development",
                "RW": False,
            },
            {
                "Type": "bind",
                "Source": str(audit.REPOSITORY_ROOT / ".env.testing"),
                "Destination": "/app/.env.testing",
                "RW": False,
            },
            {"Type": "tmpfs", "Destination": "/run/localforge"},
        ],
    )
    assert audit.mount_failures([valid], audit.TESTING_SPEC) == []

    invalid = container_record(
        "django-test-dt5qx",
        mounts=[
            {"Type": "volume", "Name": "", "Destination": "/anonymous"},
            {"Type": "volume", "Name": "foreign-data", "Destination": "/foreign"},
            {
                "Type": "bind",
                "Source": "/var/run/docker.sock",
                "Destination": "/var/run/docker.sock",
                "RW": True,
            },
            "invalid",
        ],
    )
    failures = audit.mount_failures([invalid], audit.TESTING_SPEC)
    assert any("volume-mounts" in failure for failure in failures)
    assert any("binds" in failure for failure in failures)

    postgres = container_record(
        "postgres-tp8vn",
        mounts=[
            {
                "Type": "volume",
                "Name": "postgres-tp8vn-data",
                "Destination": "/var/lib/postgresql",
                "RW": True,
            }
        ],
    )
    assert audit.mount_failures([postgres], audit.TESTING_SPEC) == []
    swapped = container_record(
        "django-test-dt5qx",
        mounts=[
            {
                "Type": "volume",
                "Name": "postgres-tp8vn-data",
                "Destination": "/var/lib/postgresql",
                "RW": True,
            }
        ],
    )
    assert audit.mount_failures([swapped], audit.TESTING_SPEC)

    foreign_source = container_record(
        "django-test-dt5qx",
        mounts=[
            {
                "Type": "bind",
                "Source": "/foreign/.env.development",
                "Destination": "/app/.env.development",
                "RW": False,
            },
            {
                "Type": "bind",
                "Source": str(audit.REPOSITORY_ROOT / ".env.testing"),
                "Destination": "/app/.env.testing",
                "RW": False,
            },
        ],
    )
    assert any(
        "bind " in failure for failure in audit.mount_failures([foreign_source], audit.TESTING_SPEC)
    )


def test_device_rules_require_only_cadvisor_kernel_messages() -> None:
    """Restrict cAdvisor to its registered kernel message device.

    Accepts the exact Docker device grant and rejects missing or widened mappings.
    Confirms unrelated containers require no host device.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If device drift is accepted.
    """
    valid = container_record("cadvisor-cv8mh")
    valid["HostConfig"] = {
        "Devices": [
            {
                "PathOnHost": "/dev/kmsg",
                "PathInContainer": "/dev/kmsg",
                "CgroupPermissions": "r",
            }
        ]
    }
    widened = container_record("cadvisor-cv8mh")
    widened["HostConfig"] = {
        "Devices": [
            {
                "PathOnHost": "/dev/kmsg",
                "PathInContainer": "/dev/kmsg",
                "CgroupPermissions": "r",
            },
            {
                "PathOnHost": "/dev/sda",
                "PathInContainer": "/dev/sda",
                "CgroupPermissions": "rwm",
            },
        ]
    }

    assert audit.device_failures([valid]) == []
    assert audit.device_failures([container_record("django-uv5n2")]) == []
    assert audit.device_failures([container_record("cadvisor-cv8mh")])
    assert audit.device_failures([widened])


def test_container_network_and_volume_rules_detect_registry_drift() -> None:
    """Detect missing, unexpected, malformed, default, and wrong-internal resources.

    Builds exact testing registries before replacing members with stale names and incorrect
    topology attributes.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If registry drift is not reported.
    """
    containers = [container_record(name) for name in sorted(audit.TESTING_SPEC.containers)]
    assert audit.container_name_failures(containers, audit.TESTING_SPEC) == []
    membership_drift = container_record("django-test-dt5qx")
    network_settings = membership_drift["NetworkSettings"]
    assert isinstance(network_settings, dict)
    attached = network_settings["Networks"]
    assert isinstance(attached, dict)
    attached["access-net-ht6pn"] = {}
    assert audit.container_name_failures([membership_drift], audit.TESTING_SPEC)
    stopped = container_record("django-test-dt5qx")
    stopped["State"] = {
        "Status": "exited",
        "Health": {"Status": "unhealthy"},
    }
    assert audit.container_name_failures([stopped], audit.TESTING_SPEC)
    drifted_containers: list[dict[str, object]] = [
        *containers[:-1],
        {
            "Name": "/bad-1",
            "Config": {
                "Image": "localforge/unknown:1",
                "Labels": {
                    "com.docker.compose.project": "localforge-test",
                    "com.docker.compose.service": "bad-1",
                    "com.docker.compose.oneoff": "True",
                },
            },
            "State": {"Status": "exited"},
        },
    ]
    assert audit.container_name_failures(drifted_containers, audit.TESTING_SPEC)

    networks = resource_records(
        platform.NETWORK_PROJECTS,
        set(audit.TESTING_SPEC.networks),
        internal=dict(audit.TESTING_SPEC.networks),
    )
    assert audit.network_failures(networks, audit.TESTING_SPEC) == []
    unexpected_network: dict[str, object] = {
        "Name": "localforge-test_default",
        "Labels": {"com.docker.compose.project": "localforge-test"},
        "Internal": False,
    }
    drifted_networks: list[dict[str, object]] = [
        *networks[:-1],
        unexpected_network,
    ]
    assert audit.network_failures(drifted_networks, audit.TESTING_SPEC)
    wrong_internal = [dict(record) for record in networks]
    wrong_internal[0]["Internal"] = not bool(wrong_internal[0]["Internal"])
    assert audit.network_failures(wrong_internal, audit.TESTING_SPEC)

    volumes = resource_records(
        platform.VOLUME_PROJECTS,
        audit.TESTING_SPEC.required_volumes,
    )
    assert audit.volume_failures(volumes, audit.TESTING_SPEC) == []
    assert audit.volume_failures([], audit.TESTING_SPEC)


def test_offline_probe_requires_nonzero_and_empty_output() -> None:
    """Reject successful resolution and diagnostic output on internal networks.

    Models both required testing networks and requires every disposable probe to fail silently
    while retaining ``--rm`` cleanup.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an online internal network is accepted.
    """
    clean = FakeRunner(results=[audit.GETENT_NOT_FOUND_EXIT] * PAIR_COUNT)
    assert audit.offline_failures(clean, audit.TESTING_SPEC) == []
    online = FakeRunner(
        results=[0, FAILURE],
        outputs=["203.0.113.1 example.com", ""],
    )
    failures = audit.offline_failures(online, audit.TESTING_SPEC)
    assert len(failures) == PAIR_COUNT
    assert all("--rm" in command for command in online.calls)


def test_audit_environment_sequences_every_gate_and_propagates_failures() -> None:
    """Run every convention evaluator and stop at failed Docker collection.

    Covers shared baseline failure, each object collector, a clean complete evaluation, and a
    convention violation after collection.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If audit sequencing or status propagation changes.
    """
    empty_records: list[dict[str, object]] = []
    with (
        patch.object(platform, "docker_environment_audit", return_value=0),
        patch.object(
            audit,
            "inspect_names",
            side_effect=[
                (0, empty_records),
                (0, empty_records),
                (0, empty_records),
            ],
        ),
        patch.object(audit, "container_name_failures", return_value=[]),
        patch.object(audit, "port_failures", return_value=[]),
        patch.object(audit, "mount_failures", return_value=[]),
        patch.object(audit, "device_failures", return_value=[]),
        patch.object(audit, "network_failures", return_value=[]),
        patch.object(audit, "volume_failures", return_value=[]),
        patch.object(audit, "offline_failures", return_value=[]),
    ):
        assert audit.audit_environment(FakeRunner(), audit.TESTING_SPEC) == 0
    with patch.object(platform, "docker_environment_audit", return_value=FAILURE):
        assert audit.audit_environment(FakeRunner(), audit.TESTING_SPEC) == FAILURE
    failure_results: tuple[list[tuple[int, list[dict[str, object]]]], ...] = (
        [(FAILURE, [])],
        [(0, []), (FAILURE, [])],
        [(0, []), (0, []), (FAILURE, [])],
    )
    for results in failure_results:
        with (
            patch.object(platform, "docker_environment_audit", return_value=0),
            patch.object(audit, "inspect_names", side_effect=results),
        ):
            assert audit.audit_environment(FakeRunner(), audit.TESTING_SPEC) == FAILURE
    with (
        patch.object(platform, "docker_environment_audit", return_value=0),
        patch.object(
            audit,
            "inspect_names",
            side_effect=[(0, []), (0, []), (0, [])],
        ),
        patch.object(
            audit,
            "container_name_failures",
            return_value=["container drift"],
        ),
        patch.object(audit, "port_failures", return_value=[]),
        patch.object(audit, "mount_failures", return_value=[]),
        patch.object(audit, "device_failures", return_value=[]),
        patch.object(audit, "network_failures", return_value=[]),
        patch.object(audit, "volume_failures", return_value=[]),
        patch.object(audit, "offline_failures", return_value=[]),
    ):
        assert audit.audit_environment(FakeRunner(), audit.TESTING_SPEC) == 1


def test_inspection_delegation_parser_and_main_dispatch(tmp_path: Path) -> None:
    """Cover shared inspection delegation and environment CLI dispatch.

    Verifies exact parser choices, combined environment order, first-failure propagation, and
    single-environment selection.

    Arguments:
        tmp_path: Temporary repository path retained for signature consistency.

    Returns:
        None.

    Raises:
        AssertionError: If parser choices, delegation, or environment order changes.
    """
    del tmp_path
    runner = FakeRunner()
    with patch.object(
        platform,
        "inspect_docker_objects",
        return_value=(0, [{"Name": "/ok"}]),
    ) as inspect:
        assert audit.inspect_names(runner, ("list",), ("inspect",)) == (
            0,
            [{"Name": "/ok"}],
        )
        inspect.assert_called_once()

    assert audit.build_parser().parse_args(["--environment", "testing"]).environment == "testing"
    with patch.object(audit, "audit_environment", return_value=0) as run:
        assert audit.main(["--environment", "all"], runner=runner) == 0
        assert [call.args[1] for call in run.call_args_list] == [
            audit.DEVELOPMENT_SPEC,
            audit.TESTING_SPEC,
        ]
    with patch.object(audit, "audit_environment", side_effect=[0, FAILURE]) as run:
        assert audit.main(["--environment", "all"], runner=runner) == FAILURE
        assert run.call_count == PAIR_COUNT
    with patch.object(audit, "audit_environment", return_value=0) as run:
        assert audit.main(["--environment", "development"], runner=runner) == 0
        run.assert_called_once_with(runner, audit.DEVELOPMENT_SPEC)

    with (
        patch.object(sys, "argv", ["audit_naming", "--environment", "testing"]),
        patch.object(platform, "HostRunner", return_value=FakeRunner()),
        pytest.raises(SystemExit) as exited,
        pytest.warns(RuntimeWarning, match="found in sys.modules"),
    ):
        runpy.run_module("scripts.audit_naming", run_name="__main__")
    assert exited.value.code == platform.EXIT_FAILED
