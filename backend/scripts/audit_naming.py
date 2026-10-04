"""Live Docker convention audit for LocalForge environments.

Compares project-scoped containers, ports, mounts, networks, volumes, and offline enforcement
against the frozen registries without inspecting unrelated resources on the shared engine.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path
from typing import Protocol

from scripts import manage_platform as platform

_REPOSITORY_ROOT_CANDIDATE = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = (
    _REPOSITORY_ROOT_CANDIDATE
    if (_REPOSITORY_ROOT_CANDIDATE / ".env.example").is_file()
    else Path(__file__).resolve().parents[1]
)
NAME_PATTERN = re.compile(r"^[a-z][a-z-]*-[a-z2-9]{5}$")
GETENT_NOT_FOUND_EXIT = 2
WINDOWS_DRIVE_PREFIX_LENGTH = 2
WILDCARD_HOSTS = frozenset({str(IPv4Address(0)), str(IPv6Address(0))})
LOOPBACK_HOSTS = frozenset({"127.0.0.1"})
DOCKER_SOCKET_SOURCES = frozenset({"/var/run/docker.sock", "/run/host-services/docker.proxy.sock"})
EXPECTED_BIND_MOUNTS: dict[str, dict[str, frozenset[str]]] = {
    "traefik-tk2jp": {
        "/etc/traefik/traefik.yaml": frozenset({"docker/traefik/traefik.yaml"}),
        "/var/run/docker.sock": DOCKER_SOCKET_SOURCES,
    },
    "postgres-pg3ka": {
        "/docker-entrypoint-initdb.d": frozenset({"docker/postgres/primary/initdb"}),
        "/etc/postgresql/conf.d": frozenset({"docker/postgres/primary/conf.d"}),
    },
    "postgres-replica-pg6vy": {
        "/etc/postgresql/conf.d": frozenset({"docker/postgres/standby/conf.d"}),
        "/usr/local/bin/pg_replica_bootstrap.sh": frozenset(
            {"backend/scripts/pg_replica_bootstrap.sh"}
        ),
    },
    "pgadmin-pa7fe": {"/pgadmin4/servers.json": frozenset({"docker/pgadmin/servers.json"})},
    "seaweedfs-sw9cr": {"/etc/seaweedfs/s3.json.template": frozenset({"docker/seaweedfs/s3.json"})},
    "cadvisor-cv8mh": {
        "/etc/machine-id": frozenset({"docker/cadvisor/machine-id"}),
        "/dev/disk": frozenset({"/dev/disk"}),
        "/rootfs": frozenset({"/"}),
        "/rootfs/etc/machine-id": frozenset({"docker/cadvisor/machine-id"}),
        "/sys": frozenset({"/sys"}),
        "/var/lib/docker": frozenset({"/var/lib/docker"}),
    },
    "postgres-exporter-pe4rk": {
        "/etc/pe/auth_modules.yaml.template": frozenset(
            {"docker/postgres-exporter/auth_modules.yaml"}
        )
    },
    "loki-lk3ny": {"/etc/loki/loki.yaml": frozenset({"docker/loki/loki.yaml"})},
    "alloy-al6wz": {
        "/etc/alloy/config.alloy": frozenset({"docker/alloy/config.alloy"}),
        "/var/run/docker.sock": DOCKER_SOCKET_SOURCES,
    },
    "prometheus-pm5db": {
        "/etc/prometheus/prometheus.yml": frozenset({"docker/prometheus/prometheus.yml"})
    },
    "grafana-gf7qv": {"/etc/grafana/provisioning": frozenset({"docker/grafana/provisioning"})},
    "seaweedfs-ts3jd": {"/etc/seaweedfs/s3.json.template": frozenset({"docker/seaweedfs/s3.json"})},
    "django-test-dt5qx": {
        "/app/.env.development": frozenset({".env.development"}),
        "/app/.env.testing": frozenset({".env.testing"}),
    },
}
EXPECTED_DEVICES = {
    "cadvisor-cv8mh": frozenset({("/dev/kmsg", "/dev/kmsg", "r")}),
}


@dataclass(frozen=True, slots=True)
class PortBinding:
    """Expected publication for one container port.

    Carries the host port and accepted host interfaces for exact live comparison. Inherits
    nothing and has no behavior.

    Attributes:
        host_port: Published host port.
        host_ips: Exact accepted host interfaces.

    Members:
        None.
    """

    host_port: str
    host_ips: frozenset[str]


@dataclass(frozen=True, slots=True)
class LivePortBinding:
    """Observed publication set for one container port.

    Preserves every host port and interface Docker reports so ambiguous multi-port publications
    cannot disappear during normalization. Inherits nothing.

    Attributes:
        host_ports: Published host ports.
        host_ips: Published host interfaces.

    Members:
        None.
    """

    host_ports: frozenset[str]
    host_ips: frozenset[str]


@dataclass(frozen=True, slots=True)
class VolumeMount:
    """Expected named-volume attachment.

    Binds one registry volume to its owning container destination and read/write policy. Inherits
    nothing.

    Attributes:
        name: Registered volume name.
        destination: Container mount destination.
        read_write: Expected writable state.

    Members:
        None.
    """

    name: str
    destination: str
    read_write: bool


@dataclass(frozen=True, slots=True)
class ConventionSpec:
    """Frozen convention registry for one environment.

    Groups the names, ports, networks, volumes, binds, and offline probes that belong together.
    Inherits nothing and has no behavior.

    Attributes:
        environment: Stable environment name.
        platform_spec: Shared Compose environment definition.
        containers: Required container names.
        networks: Network name to expected internal flag.
        volumes: Allowed named volumes.
        required_volumes: Volumes required without an optional profile.
        ports: Container port publication registry.
        memberships: Container to exact network attachment set.
        volume_mounts: Container to exact named-volume attachments.
        offline_probes: Internal network to probe image.

    Members:
        None.
    """

    environment: str
    platform_spec: platform.EnvironmentSpec
    containers: frozenset[str]
    networks: Mapping[str, bool]
    volumes: frozenset[str]
    required_volumes: frozenset[str]
    ports: Mapping[str, Mapping[str, PortBinding]]
    memberships: Mapping[str, frozenset[str]]
    volume_mounts: Mapping[str, frozenset[VolumeMount]]
    offline_probes: Mapping[str, str]


class AuditRunner(Protocol):
    """External command surface required by the convention audit.

    Allows live Docker execution and deterministic unit tests through the same structural
    interface. Inherits from ``Protocol``.

    Members:
        run: Execute one command with optional captured output.
    """

    def run(
        self,
        command: tuple[str, ...],
        environment: dict[str, str] | None = None,
        *,
        capture: bool = False,
    ) -> platform.CommandResult:
        """Execute one command.

        Preserves live output by default and captures combined output only when an audit must
        interpret Docker state.

        Arguments:
            command: Argument vector to execute.
            environment: Optional complete child environment.
            capture: Whether combined process output is captured.

        Returns:
            Process result.
        """


def ports(
    entries: Mapping[str, Mapping[str, tuple[str, frozenset[str]]]],
) -> dict[str, dict[str, PortBinding]]:
    """Build typed port bindings from compact registry entries.

    Expands immutable host-interface tuples into value objects so later comparisons remain typed
    and independent of the literal declaration shape.

    Arguments:
        entries: Container to internal port and host binding tuples.

    Returns:
        Typed nested port registry.
    """
    return {
        container: {
            internal: PortBinding(host_port=host_port, host_ips=host_ips)
            for internal, (host_port, host_ips) in bindings.items()
        }
        for container, bindings in entries.items()
    }


DEVELOPMENT_PORTS = ports(
    {
        "traefik-tk2jp": {
            "80/tcp": ("8080", LOOPBACK_HOSTS),
            "8080/tcp": ("8081", LOOPBACK_HOSTS),
        },
        "django-uv5n2": {"8000/tcp": ("8000", LOOPBACK_HOSTS)},
        "postgres-pg3ka": {"5432/tcp": ("5432", LOOPBACK_HOSTS)},
        "postgres-replica-pg6vy": {"5432/tcp": ("5433", LOOPBACK_HOSTS)},
        "pgadmin-pa7fe": {"80/tcp": ("5050", LOOPBACK_HOSTS)},
        "valkey-cache-vc5tn": {"6379/tcp": ("6379", LOOPBACK_HOSTS)},
        "valkey-channels-vh8dm": {"6379/tcp": ("6380", LOOPBACK_HOSTS)},
        "rabbitmq-rq4sx": {
            "5672/tcp": ("5672", LOOPBACK_HOSTS),
            "15672/tcp": ("15672", LOOPBACK_HOSTS),
        },
        "flower-fl9zd": {"5555/tcp": ("5555", LOOPBACK_HOSTS)},
        "mailpit-mp6gb": {
            "1025/tcp": ("1025", LOOPBACK_HOSTS),
            "8025/tcp": ("8025", LOOPBACK_HOSTS),
        },
        "seaweedfs-sw9cr": {"8333/tcp": ("8333", LOOPBACK_HOSTS)},
        "grafana-gf7qv": {"3000/tcp": ("3000", LOOPBACK_HOSTS)},
    }
)
TESTING_PORTS = ports(
    {
        "postgres-tp8vn": {"5432/tcp": ("25432", LOOPBACK_HOSTS)},
        "valkey-cache-tv4kq": {"6379/tcp": ("26379", LOOPBACK_HOSTS)},
        "valkey-channels-tv9zw": {"6379/tcp": ("26380", LOOPBACK_HOSTS)},
        "rabbitmq-tr6mc": {"5672/tcp": ("25672", LOOPBACK_HOSTS)},
        "seaweedfs-ts3jd": {"8333/tcp": ("28333", LOOPBACK_HOSTS)},
    }
)
COMPOSE_CONFIG_FILES_LABEL = "com.docker.compose.project.config_files"
DEVELOPMENT_CONFIG_FILES = frozenset({"compose.yaml", "compose.development.yaml"})
PROXY_ONLY_CONFIG_FILE = "compose.proxy-only.yaml"
DEVELOPMENT_MEMBERSHIPS = {
    "cloudflared-cf7q2": frozenset({"edge-net-ne2vk"}),
    "traefik-tk2jp": frozenset({"edge-net-ne2vk"}),
    "django-uv5n2": frozenset(
        {
            "edge-net-ne2vk",
            "app-net-na6hy",
            "data-net-nd9pc",
            "obsv-net-nb4xt",
            "access-net-ha4mz",
        }
    ),
    "postgres-pg3ka": frozenset({"data-net-nd9pc", "access-net-ha4mz"}),
    "postgres-replica-pg6vy": frozenset({"data-net-nd9pc", "access-net-ha4mz"}),
    "pgbackrest-pb2wj": frozenset({"data-net-nd9pc"}),
    "pgadmin-pa7fe": frozenset({"data-net-nd9pc", "access-net-ha4mz"}),
    "valkey-cache-vc5tn": frozenset({"app-net-na6hy", "access-net-ha4mz"}),
    "valkey-channels-vh8dm": frozenset({"app-net-na6hy", "access-net-ha4mz"}),
    "rabbitmq-rq4sx": frozenset({"app-net-na6hy", "access-net-ha4mz"}),
    "celery-worker-cw8rt": frozenset({"app-net-na6hy", "data-net-nd9pc", "edge-net-ne2vk"}),
    "celery-beat-cb4hq": frozenset({"app-net-na6hy", "data-net-nd9pc"}),
    "flower-fl9zd": frozenset({"app-net-na6hy", "access-net-ha4mz"}),
    "mailpit-mp6gb": frozenset({"app-net-na6hy", "access-net-ha4mz"}),
    "seaweedfs-sw9cr": frozenset({"app-net-na6hy", "access-net-ha4mz"}),
    "prometheus-pm5db": frozenset({"obsv-net-nb4xt"}),
    "grafana-gf7qv": frozenset({"obsv-net-nb4xt", "access-net-ha4mz"}),
    "loki-lk3ny": frozenset({"obsv-net-nb4xt"}),
    "alloy-al6wz": frozenset({"obsv-net-nb4xt"}),
    "cadvisor-cv8mh": frozenset({"obsv-net-nb4xt"}),
    "postgres-exporter-pe4rk": frozenset({"data-net-nd9pc", "obsv-net-nb4xt"}),
    "valkey-cache-exporter-ve7ts": frozenset({"app-net-na6hy", "obsv-net-nb4xt"}),
    "valkey-channels-exporter-vx4nq": frozenset({"app-net-na6hy", "obsv-net-nb4xt"}),
}
TESTING_MEMBERSHIPS = {
    "django-test-dt5qx": frozenset({"app-net-nt5rk", "data-net-nt8fq"}),
    "postgres-tp8vn": frozenset({"data-net-nt8fq", "access-net-ht6pn"}),
    "valkey-cache-tv4kq": frozenset({"app-net-nt5rk", "access-net-ht6pn"}),
    "valkey-channels-tv9zw": frozenset({"app-net-nt5rk", "access-net-ht6pn"}),
    "rabbitmq-tr6mc": frozenset({"app-net-nt5rk", "access-net-ht6pn"}),
    "seaweedfs-ts3jd": frozenset({"app-net-nt5rk", "access-net-ht6pn"}),
}
DEVELOPMENT_VOLUME_MOUNTS = {
    "postgres-pg3ka": frozenset(
        {
            VolumeMount("postgres-pg3ka-data", "/var/lib/postgresql", read_write=True),
            VolumeMount("postgres-pg3ka-socket", "/var/run/pgsocket", read_write=True),
            VolumeMount("pgbackrest-pb2wj-repo", "/var/lib/pgbackrest", read_write=True),
        }
    ),
    "postgres-replica-pg6vy": frozenset(
        {VolumeMount("postgres-replica-pg6vy-data", "/var/lib/postgresql", read_write=True)}
    ),
    "pgbackrest-pb2wj": frozenset(
        {
            VolumeMount("postgres-pg3ka-data", "/var/lib/postgresql", read_write=False),
            VolumeMount("postgres-pg3ka-socket", "/var/run/pgsocket", read_write=True),
            VolumeMount("pgbackrest-pb2wj-repo", "/var/lib/pgbackrest", read_write=True),
        }
    ),
    "pgadmin-pa7fe": frozenset(
        {VolumeMount("pgadmin-pa7fe-data", "/var/lib/pgadmin", read_write=True)}
    ),
    "valkey-cache-vc5tn": frozenset(
        {VolumeMount("valkey-cache-vc5tn-data", "/data", read_write=True)}
    ),
    "valkey-channels-vh8dm": frozenset(
        {VolumeMount("valkey-channels-vh8dm-data", "/data", read_write=True)}
    ),
    "rabbitmq-rq4sx": frozenset(
        {VolumeMount("rabbitmq-rq4sx-data", "/var/lib/rabbitmq", read_write=True)}
    ),
    "mailpit-mp6gb": frozenset({VolumeMount("mailpit-mp6gb-data", "/data", read_write=True)}),
    "seaweedfs-sw9cr": frozenset({VolumeMount("seaweedfs-sw9cr-data", "/data", read_write=True)}),
    "prometheus-pm5db": frozenset(
        {VolumeMount("prometheus-pm5db-data", "/prometheus", read_write=True)}
    ),
    "grafana-gf7qv": frozenset(
        {VolumeMount("grafana-gf7qv-data", "/var/lib/grafana", read_write=True)}
    ),
    "loki-lk3ny": frozenset({VolumeMount("loki-lk3ny-data", "/loki", read_write=True)}),
    "alloy-al6wz": frozenset(
        {VolumeMount("alloy-al6wz-data", "/var/lib/alloy/data", read_write=True)}
    ),
    "django-uv5n2": frozenset(
        {VolumeMount("django-uv5n2-static", "/app/staticfiles", read_write=True)}
    ),
}
TESTING_VOLUME_MOUNTS = {
    "postgres-tp8vn": frozenset(
        {VolumeMount("postgres-tp8vn-data", "/var/lib/postgresql", read_write=True)}
    ),
    "valkey-cache-tv4kq": frozenset(
        {VolumeMount("valkey-cache-tv4kq-data", "/data", read_write=True)}
    ),
    "valkey-channels-tv9zw": frozenset(
        {VolumeMount("valkey-channels-tv9zw-data", "/data", read_write=True)}
    ),
    "rabbitmq-tr6mc": frozenset(
        {VolumeMount("rabbitmq-tr6mc-data", "/var/lib/rabbitmq", read_write=True)}
    ),
    "seaweedfs-ts3jd": frozenset({VolumeMount("seaweedfs-ts3jd-data", "/data", read_write=True)}),
}
DEVELOPMENT_SPEC = ConventionSpec(
    environment="development",
    platform_spec=platform.DEVELOPMENT,
    containers=platform.DEVELOPMENT_CONTAINERS,
    networks={
        "edge-net-ne2vk": False,
        "access-net-ha4mz": False,
        "app-net-na6hy": True,
        "data-net-nd9pc": True,
        "obsv-net-nb4xt": True,
    },
    volumes=platform.DEVELOPMENT_VOLUMES,
    required_volumes=platform.DEVELOPMENT_VOLUMES,
    ports=DEVELOPMENT_PORTS,
    memberships=DEVELOPMENT_MEMBERSHIPS,
    volume_mounts=DEVELOPMENT_VOLUME_MOUNTS,
    offline_probes={
        "app-net-na6hy": "postgres:18.6",
        "data-net-nd9pc": "postgres:18.6",
        "obsv-net-nb4xt": "postgres:18.6",
    },
)
TESTING_SPEC = ConventionSpec(
    environment="testing",
    platform_spec=platform.TESTING,
    containers=platform.TESTING_CONTAINERS,
    networks={
        "access-net-ht6pn": False,
        "app-net-nt5rk": True,
        "data-net-nt8fq": True,
    },
    volumes=platform.TESTING_VOLUMES | platform.TESTING_OPTIONAL_VOLUMES,
    required_volumes=platform.TESTING_VOLUMES,
    ports=TESTING_PORTS,
    memberships=TESTING_MEMBERSHIPS,
    volume_mounts=TESTING_VOLUME_MOUNTS,
    offline_probes={
        "app-net-nt5rk": "postgres:18.6",
        "data-net-nt8fq": "postgres:18.6",
    },
)
SPECS = {
    "development": DEVELOPMENT_SPEC,
    "testing": TESTING_SPEC,
}


def inspect_names(
    runner: AuditRunner,
    command: tuple[str, ...],
    inspect_command: tuple[str, ...],
) -> tuple[int, list[dict[str, object]]]:
    """List and inspect one project-scoped Docker object kind.

    Delegates to the shared strict Docker decoder while retaining explicit project-filtered list
    commands at each convention-audit call site.

    Arguments:
        runner: External command adapter.
        command: Project-scoped ID listing.
        inspect_command: Inspect command prefix.

    Returns:
        Exit code and decoded records.
    """
    return platform.inspect_docker_objects(runner, command, inspect_command)


def container_name_failures(
    records: Sequence[dict[str, object]],
    spec: ConventionSpec,
) -> list[str]:
    """Validate exact container names and required running state.

    Combines the frozen environment set and naming pattern with shared image, label, health, and
    one-off-container checks.

    Arguments:
        records: Project-scoped container inspect records.
        spec: Environment convention registry.

    Returns:
        Stable convention failures.
    """
    names = {platform.record_name(record) for record in records}
    failures = [
        *(f"container missing {name}" for name in sorted(spec.containers - names)),
        *(f"container unexpected {name}" for name in sorted(names - spec.containers)),
    ]
    failures.extend(
        f"container pattern {name}"
        for name in sorted(names)
        if NAME_PATTERN.fullmatch(name) is None
    )
    for record in records:
        name = platform.record_name(record)
        network_settings = record.get("NetworkSettings")
        raw_networks = (
            network_settings.get("Networks") if isinstance(network_settings, dict) else None
        )
        actual_networks = (
            frozenset(str(network) for network in raw_networks)
            if isinstance(raw_networks, dict)
            else frozenset()
        )
        expected_networks = spec.memberships.get(name, frozenset())
        if actual_networks != expected_networks:
            failures.append(
                f"container-networks {name} expected={sorted(expected_networks)} "
                f"actual={sorted(actual_networks)}"
            )
    failures.extend(platform.evaluate_container_inventory(records, require_complete=False))
    failures.extend(platform.evaluate_environment_health(records, spec.platform_spec))
    return failures


def live_port_bindings(record: dict[str, object]) -> dict[str, LivePortBinding]:
    """Read normalized live port bindings from one container inspect record.

    Collapses Docker's duplicate IPv4 and IPv6 bindings into one internal-port entry while refusing
    ambiguous multiple host ports.

    Arguments:
        record: Docker container inspect record.

    Returns:
        Internal port to host port and interface set.
    """
    network = record.get("NetworkSettings")
    source = network.get("Ports") if isinstance(network, dict) else None
    if not isinstance(source, dict):
        return {}
    normalized: dict[str, LivePortBinding] = {}
    for internal, raw_bindings in source.items():
        if raw_bindings is None:
            continue
        bindings = (
            [binding for binding in raw_bindings if isinstance(binding, dict)]
            if isinstance(raw_bindings, list)
            else []
        )
        normalized[str(internal)] = LivePortBinding(
            host_ports=frozenset(str(binding.get("HostPort", "")) for binding in bindings),
            host_ips=frozenset(str(binding.get("HostIp", "")) for binding in bindings),
        )
    return normalized


def development_uses_proxy_only(
    records: Sequence[dict[str, object]],
) -> tuple[bool | None, list[str]]:
    """Read the active development publication mode from Compose labels.

    Requires the Django container to name the shared and development configuration files, then
    reports whether the documented proxy-only overlay also created the running container.

    Arguments:
        records: Project-scoped development container inspect records.

    Returns:
        Proxy-only state when unambiguous plus stable label failures.
    """
    django = next(
        (record for record in records if platform.record_name(record) == "django-uv5n2"),
        None,
    )
    if django is None:
        return None, []

    raw = platform.record_labels(django).get(COMPOSE_CONFIG_FILES_LABEL, "")
    files = {
        entry.strip().replace("\\", "/").rsplit("/", maxsplit=1)[-1]
        for entry in raw.split(",")
        if entry.strip()
    }
    missing = DEVELOPMENT_CONFIG_FILES - files
    if missing:
        return None, [
            f"compose-config-files django-uv5n2 missing={sorted(missing)} actual={sorted(files)}"
        ]

    return PROXY_ONLY_CONFIG_FILE in files, []


def port_failures(
    records: Sequence[dict[str, object]],
    spec: ConventionSpec,
) -> list[str]:
    """Compare every live host publication with the inventory.

    Requires exact internal ports, host ports, and host-interface sets, so both missing and extra
    publications become explicit failures.

    Arguments:
        records: Project-scoped container inspect records.
        spec: Environment convention registry.

    Returns:
        Stable port failures.
    """
    failures: list[str] = []
    proxy_only = False
    if spec is DEVELOPMENT_SPEC:
        detected, mode_failures = development_uses_proxy_only(records)
        failures.extend(mode_failures)
        if detected is None and mode_failures:
            return failures
        proxy_only = detected is True

    for record in records:
        name = platform.record_name(record)
        actual = live_port_bindings(record)
        expected = spec.ports.get(name, {})
        if proxy_only and name == "django-uv5n2":
            expected = {}
        if set(actual) != set(expected):
            failures.append(f"ports {name} expected={sorted(expected)} actual={sorted(actual)}")
            continue
        for internal, binding in expected.items():
            live = actual[internal]
            if (
                live.host_ports != frozenset({binding.host_port})
                or live.host_ips != binding.host_ips
            ):
                failures.append(
                    f"port {name} {internal} "
                    f"expected={binding.host_port}/{sorted(binding.host_ips)} "
                    f"actual={sorted(live.host_ports)}/{sorted(live.host_ips)}"
                )
    return failures


def normalize_bind_source(source: str) -> str:
    """Normalize repository and documented system bind sources.

    Converts native Windows and Docker Desktop host paths into repository-relative forward-slash
    paths while retaining exact documented system sources and marking every foreign path.

    Arguments:
        source: Docker bind source.

    Returns:
        Repository-relative path, documented system path, or ``<foreign>``.
    """
    normalized = source.replace("\\", "/").rstrip("/")
    if source == "/":
        return "/"
    if normalized in {
        "/dev/disk",
        "/sys",
        "/var/lib/docker",
        *DOCKER_SOCKET_SOURCES,
    }:
        return normalized
    repository = str(REPOSITORY_ROOT).replace("\\", "/").rstrip("/")
    if normalized.casefold().startswith(f"{repository.casefold()}/"):
        return normalized[len(repository) + 1 :]
    drive = repository[0].casefold()
    docker_repository = (
        f"/run/desktop/mnt/host/{drive}{repository[2:]}"
        if (len(repository) > WINDOWS_DRIVE_PREFIX_LENGTH and repository[1] == ":")
        else ""
    )
    if docker_repository and normalized.casefold().startswith(f"{docker_repository.casefold()}/"):
        return normalized[len(docker_repository) + 1 :]
    return "<foreign>"


def mount_failures(
    records: Sequence[dict[str, object]],
    spec: ConventionSpec,
) -> list[str]:
    """Validate named volumes and read-only bind destinations.

    Rejects anonymous or foreign volumes, writable binds, and bind destinations not registered for
    the owning container.

    Arguments:
        records: Project-scoped container inspect records.
        spec: Environment convention registry.

    Returns:
        Stable mount failures.
    """
    failures: list[str] = []
    for record in records:
        name = platform.record_name(record)
        raw_mounts = record.get("Mounts")
        mounts = raw_mounts if isinstance(raw_mounts, list) else []
        observed_binds: dict[str, tuple[str, bool]] = {}
        observed_volumes: set[VolumeMount] = set()
        for mount in mounts:
            if not isinstance(mount, dict):
                continue
            kind = str(mount.get("Type", ""))
            destination = str(mount.get("Destination", ""))
            if kind == "volume":
                observed_volumes.add(
                    VolumeMount(
                        name=str(mount.get("Name", "")),
                        destination=destination,
                        read_write=bool(mount.get("RW", False)),
                    )
                )
            elif kind == "bind":
                observed_binds[destination] = (
                    normalize_bind_source(str(mount.get("Source", ""))),
                    bool(mount.get("RW", True)),
                )
        expected_volumes = spec.volume_mounts.get(name, frozenset())
        if observed_volumes != set(expected_volumes):
            failures.append(
                f"volume-mounts {name} expected={sorted(map(str, expected_volumes))} "
                f"actual={sorted(map(str, observed_volumes))}"
            )
        expected_binds = EXPECTED_BIND_MOUNTS.get(name, {})
        if set(observed_binds) != set(expected_binds):
            failures.append(
                f"binds {name} expected={sorted(expected_binds)} actual={sorted(observed_binds)}"
            )
            continue
        for destination, allowed_sources in expected_binds.items():
            source, read_write = observed_binds[destination]
            if source not in allowed_sources or read_write:
                failures.append(
                    f"bind {name} {destination} expected={sorted(allowed_sources)}/ro "
                    f"actual={source}/{'rw' if read_write else 'ro'}"
                )
    return failures


def device_failures(records: Sequence[dict[str, object]]) -> list[str]:
    """Validate exact host-device grants for registered containers.

    Rejects missing, extra, or permission-widened devices so cAdvisor receives only the kernel
    message device required for OOM-event collection.

    Arguments:
        records: Project-scoped container inspect records.

    Returns:
        Stable device-grant failures.
    """
    failures: list[str] = []
    for record in records:
        name = platform.record_name(record)
        raw_host_config = record.get("HostConfig")
        host_config = raw_host_config if isinstance(raw_host_config, dict) else {}
        raw_devices = host_config.get("Devices")
        devices = raw_devices if isinstance(raw_devices, list) else []
        observed = {
            (
                str(device.get("PathOnHost", "")),
                str(device.get("PathInContainer", "")),
                str(device.get("CgroupPermissions", "")),
            )
            for device in devices
            if isinstance(device, dict)
        }
        expected = EXPECTED_DEVICES.get(name, frozenset())
        if observed != set(expected):
            failures.append(f"devices {name} expected={sorted(expected)} actual={sorted(observed)}")
    return failures


def network_failures(
    records: Sequence[dict[str, object]],
    spec: ConventionSpec,
) -> list[str]:
    """Validate exact network names, labels, and internal flags.

    Combines the environment network set with the naming pattern, default-network prohibition,
    project ownership, and each zone's expected internal flag.

    Arguments:
        records: Project-scoped network inspect records.
        spec: Environment convention registry.

    Returns:
        Stable network failures.
    """
    names = {platform.record_name(record) for record in records}
    failures = [
        *(f"network missing {name}" for name in sorted(set(spec.networks) - names)),
        *(f"network unexpected {name}" for name in sorted(names - set(spec.networks))),
    ]
    failures.extend(
        f"network default {name}" for name in sorted(names) if name.endswith("_default")
    )
    failures.extend(
        f"network pattern {name}" for name in sorted(names) if NAME_PATTERN.fullmatch(name) is None
    )
    for record in records:
        name = platform.record_name(record)
        if name in spec.networks and bool(record.get("Internal")) != spec.networks[name]:
            failures.append(
                f"network-internal {name} expected={spec.networks[name]} "
                f"actual={bool(record.get('Internal'))}"
            )
    failures.extend(
        platform.evaluate_resource_inventory(
            records,
            dict.fromkeys(spec.networks, spec.platform_spec.project),
            "network",
            require_complete=True,
        )
    )
    return failures


def volume_failures(
    records: Sequence[dict[str, object]],
    spec: ConventionSpec,
) -> list[str]:
    """Validate exact project volume ownership and required presence.

    Allows the profile-gated testing mail volume while requiring every ordinary environment volume
    and rejecting stale project-labelled names.

    Arguments:
        records: Project-scoped volume inspect records.
        spec: Environment convention registry.

    Returns:
        Stable volume failures.
    """
    return platform.evaluate_resource_inventory(
        records,
        dict.fromkeys(spec.volumes, spec.platform_spec.project),
        "volume",
        require_complete=True,
        required_names=spec.required_volumes,
    )


def offline_failures(runner: AuditRunner, spec: ConventionSpec) -> list[str]:
    """Prove every registered internal network blocks external DNS.

    Runs a disposable pinned PostgreSQL image on each internal network and requires external name
    resolution to fail with no output; ``--rm`` guarantees no audit container survives.

    Arguments:
        runner: External command adapter.
        spec: Environment convention registry.

    Returns:
        Stable offline-enforcement failures.
    """
    failures: list[str] = []
    for network, image in spec.offline_probes.items():
        result = runner.run(
            (
                "docker",
                "run",
                "--rm",
                "--network",
                network,
                image,
                "getent",
                "hosts",
                "example.com",
            ),
            capture=True,
        )
        if result.code != GETENT_NOT_FOUND_EXIT or result.output:
            output_state = "present" if result.output else "empty"
            failures.append(f"offline {network} exit={result.code} output={output_state}")
    return failures


def audit_environment(runner: AuditRunner, spec: ConventionSpec) -> int:
    """Run the complete convention audit for one environment.

    Runs shared ownership and health checks first, then evaluates names, ports, mounts, networks,
    volumes, and active offline enforcement as one indivisible gate.

    Arguments:
        runner: External command adapter.
        spec: Environment convention registry.

    Returns:
        Zero when every convention passes, otherwise failure.
    """
    baseline = platform.docker_environment_audit(runner, spec.platform_spec)
    if baseline != platform.EXIT_OK:
        return baseline
    code, containers = inspect_names(
        runner,
        (
            "docker",
            "container",
            "ls",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={spec.platform_spec.project}",
        ),
        ("docker", "container", "inspect"),
    )
    if code != platform.EXIT_OK:
        return code
    code, networks = inspect_names(
        runner,
        (
            "docker",
            "network",
            "ls",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={spec.platform_spec.project}",
        ),
        ("docker", "network", "inspect"),
    )
    if code != platform.EXIT_OK:
        return code
    code, volumes = inspect_names(
        runner,
        (
            "docker",
            "volume",
            "ls",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={spec.platform_spec.project}",
        ),
        ("docker", "volume", "inspect"),
    )
    if code != platform.EXIT_OK:
        return code

    failures = [
        *container_name_failures(containers, spec),
        *port_failures(containers, spec),
        *mount_failures(containers, spec),
        *device_failures(containers),
        *network_failures(networks, spec),
        *volume_failures(volumes, spec),
        *offline_failures(runner, spec),
    ]
    if failures:
        for failure in sorted(set(failures)):
            print(f"FAIL convention {failure}")
        return platform.EXIT_FAILED

    print(f"PASS convention {spec.environment}")
    return platform.EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """Build the convention-audit command parser.

    Exposes only the two registered environments and their combined audit, preventing accidental
    free-form project selection on the shared Docker engine.

    Arguments:
        None.

    Returns:
        Configured parser.
    """
    parser = argparse.ArgumentParser(description="Audit LocalForge live Docker conventions.")
    parser.add_argument(
        "--environment",
        choices=("development", "testing", "all"),
        required=True,
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: AuditRunner | None = None,
) -> int:
    """Run one or both environment convention audits.

    Preserves environment order and stops on the first failed gate so command status remains
    attributable without weakening or skipping later checks after a known violation.

    Arguments:
        argv: Command arguments, or None for process arguments.
        runner: External command adapter, or the host implementation.

    Returns:
        Zero when every requested environment passes, otherwise the first failure.
    """
    arguments = build_parser().parse_args(argv)
    active_runner = runner if runner is not None else platform.HostRunner(REPOSITORY_ROOT)
    selected = (
        (DEVELOPMENT_SPEC, TESTING_SPEC)
        if arguments.environment == "all"
        else (SPECS[arguments.environment],)
    )
    for spec in selected:
        code = audit_environment(active_runner, spec)
        if code != platform.EXIT_OK:
            return code
    return platform.EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
