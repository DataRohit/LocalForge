"""Unit tests for the Compose foundation.

Grades the Compose files against the naming registry in the conventions document, so a container,
volume, or network can never be declared under a name the registry does not carry, and the two
environments stay able to run at the same time.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONVENTIONS_DOCUMENT = REPOSITORY_ROOT / "docs" / "platform" / "conventions.md"
SHARED_FILE = REPOSITORY_ROOT / "compose.yaml"
DEVELOPMENT_FILE = REPOSITORY_ROOT / "compose.development.yaml"
TESTING_FILE = REPOSITORY_ROOT / "compose.testing.yaml"

NAME_PATTERN = re.compile(r"^[a-z][a-z-]*-[a-z2-9]{5}$")
VOLUME_PATTERN = re.compile(r"^[a-z][a-z-]*-[a-z2-9]{5}-[a-z0-9-]+$")
PROJECT_NAMES = {"development": "localforge-dev", "testing": "localforge-test"}
POSTGRES_VOLUME_TARGET = "/var/lib/postgresql"


def load(path: Path) -> dict[str, Any]:
    """Read a Compose file into a mapping.

    Parses the file directly rather than through the Compose command line, so the tests run
    unchanged inside the test container where no Docker client is available.

    Arguments:
        path: Compose file to read.

    Returns:
        The parsed document.
    """
    parsed: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))

    return parsed


def merged(overlay: Path) -> dict[str, Any]:
    """Merge the shared file with one environment overlay.

    Combines the two documents the way Compose does for the top-level sections this suite grades,
    so a service declared in the shared file is judged against the networks and volumes its overlay
    supplies rather than failing for being in the wrong file.

    Arguments:
        overlay: Environment overlay to merge onto the shared file.

    Returns:
        The merged project document.
    """
    shared = load(SHARED_FILE)
    environment = load(overlay)
    document: dict[str, Any] = dict(shared)
    for section in ("services", "networks", "volumes"):
        combined = {**(shared.get(section) or {}), **(environment.get(section) or {})}
        if combined:
            document[section] = combined

    return document


MERGED_PROJECTS = [merged(DEVELOPMENT_FILE), merged(TESTING_FILE)]


def registry_rows(heading: str) -> set[str]:
    """Read one registry table out of the conventions document.

    Collects the first backticked cell of every row under the given heading, which is where the
    registry records the name of each container, network, and volume.

    Arguments:
        heading: Section heading introducing the table.

    Returns:
        Every name the table declares.

    Raises:
        AssertionError: If the heading is absent or introduces no rows.
    """
    text = CONVENTIONS_DOCUMENT.read_text(encoding="utf-8")
    start = text.find(heading)

    assert start != -1, f"{heading} not found in {CONVENTIONS_DOCUMENT}"

    section = text[start:]
    end = section.find("\n### ", 1)
    if end != -1:
        section = section[:end]

    names: set[str] = set()
    for line in section.splitlines():
        match = re.match(r"^\|\s*`([a-z0-9-]+)`\s*\|", line)
        if match is not None:
            names.add(match.group(1))

    assert names, f"no rows parsed under {heading}"

    return names


def registry_volumes(environment: str) -> set[str]:
    """Read the volume registry for one environment.

    Filters the volume table by its environment column, because both environments share one table
    and only their own volumes belong in their own Compose file.

    Arguments:
        environment: Environment column value to select.

    Returns:
        Every volume the registry assigns to that environment.

    Raises:
        AssertionError: If the table declares no volume for the environment.
    """
    text = CONVENTIONS_DOCUMENT.read_text(encoding="utf-8")
    start = text.find("### 2.5 Volumes")

    assert start != -1, "volume registry not found"

    names: set[str] = set()
    section = text[start:]
    end = section.find("\n### ", 1)
    if end != -1:
        section = section[:end]

    for line in section.splitlines():
        match = re.match(r"^\|\s*`([a-z0-9-]+)`\s*\|[^|]+\|[^|]+\|\s*(\w+)\s*\|", line)
        if match is not None and match.group(2) == environment:
            names.add(match.group(1))

    assert names, f"no {environment} volumes parsed"

    return names


def registry_networks(environment: str, *, internal: bool | None = None) -> set[str]:
    """Read the network registry for one environment.

    Filters the network table by its environment column, and optionally by whether the row marks
    the zone internal, so each Compose file is graded against only the zones its own stack runs and
    the internal flag is read from the document rather than restated in the test.

    Arguments:
        environment: Environment column value to select.
        internal: Select only rows whose internal column matches, or None for every row.

    Returns:
        Every network the registry assigns to that environment.

    Raises:
        AssertionError: If the table declares no network for the environment.
    """
    text = CONVENTIONS_DOCUMENT.read_text(encoding="utf-8")
    start = text.find("### 2.4 Networks")

    assert start != -1, "network registry not found"

    section = text[start:]
    end = section.find("\n### ", 1)
    if end != -1:
        section = section[:end]

    names: set[str] = set()
    for line in section.splitlines():
        match = re.match(
            r"^\|\s*`([a-z0-9-]+)`\s*\|[^|]+\|\s*(\w+)\s*\|[^|]*\|\s*([^|]+?)\s*\|", line
        )
        if match is None or match.group(2) != environment:
            continue

        marked = "yes" in match.group(3).lower()
        if internal is None or marked is internal:
            names.add(match.group(1))

    assert names, f"no {environment} networks parsed"

    return names


@pytest.mark.unit
def test_the_shared_file_takes_its_project_name_from_the_environment() -> None:
    """Namespace each stack from its own environment file.

    Confirms the project name is set by the supported top-level key and read from the environment
    rather than written twice, which is what keeps the two stacks in separate namespaces.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the project name is not taken from the environment.
    """
    shared = load(SHARED_FILE)

    assert shared["name"] == "${COMPOSE_PROJECT_NAME}"


@pytest.mark.unit
@pytest.mark.parametrize("path", [SHARED_FILE, DEVELOPMENT_FILE, TESTING_FILE])
def test_the_obsolete_version_key_is_absent(path: Path) -> None:
    """Leave out the key Compose no longer honours.

    Confirms no file carries the obsolete top-level version key, which current Compose warns about
    while validating against the newest schema regardless.

    Arguments:
        path: Compose file to inspect.

    Returns:
        None.

    Raises:
        AssertionError: If the file declares a version.
    """
    assert "version" not in load(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "environment"),
    [(DEVELOPMENT_FILE, "development"), (TESTING_FILE, "testing")],
)
def test_every_registry_network_is_declared(path: Path, environment: str) -> None:
    """Declare exactly the networks the registry assigns to the environment.

    Confirms each file declares its own zones and no others, so neither stack can reach a zone
    belonging to the other or quietly gain one that was never registered.

    Arguments:
        project: Merged project to inspect.
        environment: Environment the file configures.

    Returns:
        None.

    Raises:
        AssertionError: If the declared networks differ from the registry.
    """
    declared = load(path)["networks"]

    assert set(declared) == registry_networks(environment)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "environment"),
    [(DEVELOPMENT_FILE, "development"), (TESTING_FILE, "testing")],
)
def test_every_registry_volume_is_declared(path: Path, environment: str) -> None:
    """Declare exactly the volumes the registry assigns to the environment.

    Confirms each file declares its own volumes and no others, which is what stops one stack
    mounting state belonging to the other.

    Arguments:
        project: Merged project to inspect.
        environment: Environment the file configures.

    Returns:
        None.

    Raises:
        AssertionError: If the declared volumes differ from the registry.
    """
    declared = load(path)["volumes"]

    assert set(declared) == registry_volumes(environment)


@pytest.mark.unit
@pytest.mark.parametrize("path", [DEVELOPMENT_FILE, TESTING_FILE])
def test_every_network_carries_its_registry_name(path: Path) -> None:
    """Name each network explicitly rather than letting Compose prefix it.

    Confirms every network sets its own name, without which Compose prefixes the project name and
    the live object no longer matches the registry the audits compare against.

    Arguments:
        path: Compose file to inspect.

    Returns:
        None.

    Raises:
        AssertionError: If a network does not name itself after its key.
    """
    for key, definition in load(path)["networks"].items():
        assert definition["name"] == key


@pytest.mark.unit
@pytest.mark.parametrize("path", [DEVELOPMENT_FILE, TESTING_FILE])
def test_every_volume_carries_its_registry_name(path: Path) -> None:
    """Name each volume explicitly rather than letting Compose prefix it.

    Confirms every volume sets its own name, so the live object matches the registry entry that
    names its owning container.

    Arguments:
        path: Compose file to inspect.

    Returns:
        None.

    Raises:
        AssertionError: If a volume does not name itself after its key.
    """
    for key, definition in load(path)["volumes"].items():
        assert definition["name"] == key


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "environment"),
    [(DEVELOPMENT_FILE, "development"), (TESTING_FILE, "testing")],
)
def test_the_internal_flag_matches_the_registry(path: Path, environment: str) -> None:
    """Cut every zone off from the internet except the ones that must publish.

    Confirms the internal flag on each zone matches the column the registry records, read from the
    document rather than restated here, so relaxing a zone in one place and not the other fails.

    Arguments:
        project: Merged project to inspect.
        environment: Environment the file configures.

    Returns:
        None.

    Raises:
        AssertionError: If a zone's internal flag differs from the registry.
    """
    internal = registry_networks(environment, internal=True)
    reachable = registry_networks(environment, internal=False)
    declared = load(path)["networks"]

    assert set(declared) == internal | reachable
    for name, definition in declared.items():
        assert definition.get("internal", False) is (name in internal), name


@pytest.mark.unit
@pytest.mark.parametrize("environment", ["development", "testing"])
def test_each_environment_has_an_access_zone_that_is_not_internal(environment: str) -> None:
    """Keep a route by which published ports can work.

    Confirms every environment registers at least one non-internal zone, because Docker drops a
    published port silently when every network a container is on is internal.

    Arguments:
        environment: Environment to inspect.

    Returns:
        None.

    Raises:
        AssertionError: If the environment registers no reachable zone.
    """
    reachable = registry_networks(environment, internal=False)

    assert reachable
    assert any(name.startswith("access-net-") for name in reachable)


@pytest.mark.unit
@pytest.mark.parametrize("path", [DEVELOPMENT_FILE, TESTING_FILE])
def test_every_network_name_follows_the_zone_scheme(path: Path) -> None:
    """Keep every network name in the documented shape.

    Confirms each network is a zone name followed by a five-character identifier, which is the
    pattern the naming audit enforces.

    Arguments:
        path: Compose file to inspect.

    Returns:
        None.

    Raises:
        AssertionError: If a network name does not match the pattern.
    """
    for name in load(path)["networks"]:
        assert NAME_PATTERN.match(name), name
        assert "-net-" in name


@pytest.mark.unit
@pytest.mark.parametrize("path", [DEVELOPMENT_FILE, TESTING_FILE])
def test_every_volume_name_traces_to_an_owning_container(path: Path) -> None:
    """Keep every volume traceable to exactly one container.

    Confirms each volume name begins with an owning container name and ends with what it holds, so
    a stray volume can always be attributed.

    Arguments:
        path: Compose file to inspect.

    Returns:
        None.

    Raises:
        AssertionError: If a volume name does not match the pattern.
    """
    containers = registry_rows("### 2.2 Development") | registry_rows("### 2.3 Testing")

    for name in load(path)["volumes"]:
        assert VOLUME_PATTERN.match(name), name
        assert any(name.startswith(f"{container}-") for container in containers), name


@pytest.mark.unit
def test_the_two_environments_share_no_network_or_volume() -> None:
    """Keep the two stacks able to run at the same time.

    Confirms no network or volume name appears in both environments, which is what lets the test
    suite run while the development stack is up.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the two environments collide on a name.
    """
    development = load(DEVELOPMENT_FILE)
    testing = load(TESTING_FILE)

    assert not set(development["networks"]) & set(testing["networks"])
    assert not set(development["volumes"]) & set(testing["volumes"])


@pytest.mark.unit
@pytest.mark.parametrize("project", MERGED_PROJECTS)
def test_no_service_relies_on_the_default_network(project: dict[str, Any]) -> None:
    """Attach every service to a declared zone.

    Confirms no service omits its networks key, because a service left without one joins a
    Compose-generated default network that the registry does not carry.

    Arguments:
        path: Compose file to inspect.

    Returns:
        None.

    Raises:
        AssertionError: If a service declares no network.
    """
    for name, definition in (project.get("services") or {}).items():
        assert definition.get("networks"), name


def inline_literals(definition: dict[str, Any]) -> list[str]:
    """List the variables a service sets to a literal value inline.

    Distinguishes a pass-through, which names a variable and takes its value from the environment,
    from a literal, which silently overrides the generated env file. Handles both the mapping and
    the list form Compose accepts, because the list form carries bare names that have no value at
    all and would otherwise fail to unpack.

    Arguments:
        definition: Service definition to inspect.

    Returns:
        The variables set to a literal value, which should be empty.
    """
    inline = definition.get("environment") or {}
    pairs: list[tuple[str, str | None]] = []
    if isinstance(inline, dict):
        pairs = list(inline.items())
    else:
        for item in inline:
            name, separator, value = str(item).partition("=")
            pairs.append((name, value if separator else None))

    return [name for name, value in pairs if value is not None and value != f"${{{name}}}"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("inline", "expected"),
    [
        ({}, []),
        ({"POSTGRES_PASSWORD": "${POSTGRES_PASSWORD}"}, []),
        ({"POSTGRES_PASSWORD": "hunter2"}, ["POSTGRES_PASSWORD"]),
        (["POSTGRES_PASSWORD"], []),
        (["POSTGRES_PASSWORD=${POSTGRES_PASSWORD}"], []),
        (["POSTGRES_PASSWORD=hunter2"], ["POSTGRES_PASSWORD"]),
    ],
)
def test_a_pass_through_is_told_apart_from_a_literal(
    inline: dict[str, str] | list[str],
    expected: list[str],
) -> None:
    """Tell a pass-through apart from a value written inline.

    Confirms both forms Compose accepts are classified correctly, including the bare name in the
    list form, which carries no value and must not be mistaken for one.

    Arguments:
        inline: Environment section to classify.
        expected: Variables that should be reported as literals.

    Returns:
        None.

    Raises:
        AssertionError: If the classification differs from the expectation.
    """
    assert inline_literals({"environment": inline}) == expected


@pytest.mark.unit
@pytest.mark.parametrize("project", MERGED_PROJECTS)
def test_no_service_sets_a_value_inline(project: dict[str, Any]) -> None:
    """Load configuration only from the per-environment files.

    Confirms no service carries an inline literal, because such a value silently overrides the
    generated env file and produces a stack that ignores its own configuration.

    Arguments:
        path: Compose file to inspect.

    Returns:
        None.

    Raises:
        AssertionError: If a service sets a literal value inline.
    """
    for name, definition in (project.get("services") or {}).items():
        assert inline_literals(definition) == [], name


@pytest.mark.unit
@pytest.mark.parametrize("project", MERGED_PROJECTS)
def test_every_service_names_itself_after_its_compose_key(project: dict[str, Any]) -> None:
    """Give every container the name the registry assigns it.

    Confirms each service declares a container name identical to its key, so the live container
    matches the registry rather than a Compose-generated name.

    Arguments:
        path: Compose file to inspect.

    Returns:
        None.

    Raises:
        AssertionError: If a service omits its container name or uses a different one.
    """
    for name, definition in (project.get("services") or {}).items():
        assert definition.get("container_name") == name
        assert NAME_PATTERN.match(name), name


def mount_source(mount: str | dict[str, Any]) -> str | None:
    """Report the source a mount declares, if it declares one.

    Distinguishes the mount forms Compose accepts, because a short-form mount naming only a target
    creates an anonymous volume while looking exactly like a mount that names a source, and a
    long-form volume mount can omit its source entirely.

    Arguments:
        mount: Mount entry in either the short string form or the long mapping form.

    Returns:
        The declared source, or None when the mount would create an anonymous volume.
    """
    if isinstance(mount, dict):
        key = "target" if mount.get("type") == "tmpfs" else "source"
        declared = mount.get(key)

        return str(declared) if isinstance(declared, str) else None

    if ":" not in mount:
        return None

    return mount.split(":")[0]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mount", "expected"),
    [
        ("/var/lib/postgresql/data", None),
        ("postgres-pg3ka-data:/var/lib/postgresql/data", "postgres-pg3ka-data"),
        ("./docker/postgres:/etc/postgres:ro", "./docker/postgres"),
        ({"type": "volume", "target": "/data"}, None),
        ({"type": "volume", "source": "loki-lk3ny-data", "target": "/data"}, "loki-lk3ny-data"),
        ({"type": "bind", "source": "./docker/loki", "target": "/etc/loki"}, "./docker/loki"),
        ({"type": "tmpfs", "target": "/run/scratch"}, "/run/scratch"),
    ],
)
def test_a_mount_naming_only_a_target_is_recognised_as_anonymous(
    mount: str | dict[str, Any],
    expected: str | None,
) -> None:
    """Tell an anonymous mount apart from one that names a source.

    Confirms every mount form Compose accepts is classified correctly, since the short form naming
    only a target is the one that silently creates an anonymous volume and is otherwise
    indistinguishable from a well-formed mount.

    Arguments:
        mount: Mount entry to classify.
        expected: Source the mount declares, or None when it is anonymous.

    Returns:
        None.

    Raises:
        AssertionError: If the mount is not classified as expected.
    """
    assert mount_source(mount) == expected


@pytest.mark.unit
@pytest.mark.parametrize("project", MERGED_PROJECTS)
def test_no_service_mounts_an_anonymous_volume(project: dict[str, Any]) -> None:
    """Map every mount to a declared volume or a repository path.

    Confirms no mount is left anonymous and every named source is a volume the file declares, since
    an anonymous volume carries a generated hexadecimal name that traces to nothing and fails the
    naming audit.

    Arguments:
        path: Compose file to inspect.

    Returns:
        None.

    Raises:
        AssertionError: If a mount has no source, or names a volume that is not declared.
    """
    declared = set(project.get("volumes") or {})

    for name, definition in (project.get("services") or {}).items():
        for mount in definition.get("volumes") or []:
            source = mount_source(mount)

            assert source, f"{name} mounts an anonymous volume"
            assert source.startswith((".", "/")) or source in declared, f"{name} mounts {source}"


@pytest.mark.unit
@pytest.mark.parametrize("project", MERGED_PROJECTS)
def test_every_published_service_joins_a_reachable_zone(project: dict[str, Any]) -> None:
    """Keep a published port from being dropped silently.

    Confirms any service publishing a host port also joins a non-internal zone, without which
    Docker discards the publication with no warning and the container runs healthily while the port
    is unreachable.

    Arguments:
        project: Merged project to inspect.

    Returns:
        None.

    Raises:
        AssertionError: If a publishing service is on internal zones alone.
    """
    for name, definition in (project.get("services") or {}).items():
        if not definition.get("ports"):
            continue

        assert any(
            zone.startswith(("access-net-", "edge-net-")) for zone in definition["networks"]
        ), name


@pytest.mark.unit
@pytest.mark.parametrize("project", MERGED_PROJECTS)
def test_a_service_is_only_awaited_when_it_reports_health(project: dict[str, Any]) -> None:
    """Wait on readiness rather than on a process existing.

    Confirms every dependency is awaited on health and that the awaited service defines a check,
    because waiting on one that does not is a configuration error reported only at start time.

    Arguments:
        project: Merged project to inspect.

    Returns:
        None.

    Raises:
        AssertionError: If a dependency is awaited without a health check.
    """
    services = project.get("services") or {}
    for name, definition in services.items():
        for dependency, condition in (definition.get("depends_on") or {}).items():
            assert condition.get("condition") == "service_healthy", f"{name} -> {dependency}"
            assert services[dependency].get("healthcheck"), dependency


@pytest.mark.unit
@pytest.mark.parametrize("project", MERGED_PROJECTS)
def test_every_bind_mount_is_read_only(project: dict[str, Any]) -> None:
    """Keep a container from writing into the working tree.

    Confirms every bind mount from the repository is mounted read-only, so a container cannot
    modify the source it was configured from.

    Arguments:
        project: Merged project to inspect.

    Returns:
        None.

    Raises:
        AssertionError: If a bind mount is writable.
    """
    for name, definition in (project.get("services") or {}).items():
        for mount in definition.get("volumes") or []:
            if isinstance(mount, str) and mount.startswith("./"):
                assert mount.endswith(":ro"), f"{name} mounts {mount} writable"


@pytest.mark.unit
def test_the_standby_boots_from_a_script_it_mounts() -> None:
    """Give the standby the bootstrap script it is told to run.

    Confirms the standby's entrypoint names a path the service also mounts, since an entrypoint
    pointing at a file that is not there fails only once the container starts.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the entrypoint is not a mounted path.
    """
    standby = merged(DEVELOPMENT_FILE)["services"]["postgres-replica-pg6vy"]
    targets = {mount.split(":")[1] for mount in standby["volumes"] if mount.startswith("./")}

    assert standby["entrypoint"][0] in targets


@pytest.mark.unit
def test_the_standby_waits_for_the_primary_it_seeds_from() -> None:
    """Order the standby behind the primary it copies.

    Confirms the standby depends on the primary reporting healthy, because a base backup taken
    before the primary accepts connections cannot succeed.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the standby does not wait on the primary.
    """
    standby = merged(DEVELOPMENT_FILE)["services"]["postgres-replica-pg6vy"]

    assert standby["depends_on"]["postgres-pg3ka"]["condition"] == "service_healthy"


@pytest.mark.unit
def test_the_standby_health_check_tells_a_standby_from_a_primary() -> None:
    """Report the standby healthy only while it is in recovery.

    Confirms the standby's check asserts recovery state rather than mere liveness, because a
    standby promoted to a primary would otherwise still report healthy.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the check does not assert recovery state.
    """
    standby = merged(DEVELOPMENT_FILE)["services"]["postgres-replica-pg6vy"]
    check = " ".join(standby["healthcheck"]["test"])

    assert standby["healthcheck"]["test"][0] == "CMD-SHELL"
    assert "pg_isready" in check
    assert "psql" in check
    assert "pg_is_in_recovery()" in check
    assert "= t" in check


@pytest.mark.unit
@pytest.mark.parametrize("project", MERGED_PROJECTS)
def test_every_database_volume_is_mounted_where_the_image_expects(
    project: dict[str, Any],
) -> None:
    """Mount the database volume at the path the image declares.

    Confirms every PostgreSQL service mounts its data volume at the image's own volume path. The
    18 series moved the data directory under a version subdirectory, so a volume mounted at the
    older path is reported as an unused mount and the server refuses to start, printing a long
    advisory rather than a short error.

    Arguments:
        project: Merged project to inspect.

    Returns:
        None.

    Raises:
        AssertionError: If a database volume is mounted anywhere else.
    """
    for name, definition in (project.get("services") or {}).items():
        if not str(definition.get("image", "")).startswith("docker.io/library/postgres"):
            continue

        targets = {
            mount.split(":")[1]
            for mount in definition["volumes"]
            if isinstance(mount, str) and mount.split(":")[0].endswith("-data")
        }

        assert targets == {POSTGRES_VOLUME_TARGET}, name


@pytest.mark.unit
def test_the_standby_reaches_the_primary_over_the_data_zone_alone() -> None:
    """Keep replication off the zone that has internet egress.

    Confirms the primary carries an alias scoped to the data zone and the standby is pointed at a
    name rather than the primary's container name, because a container name resolves to every zone
    it joins and Docker may hand back the access-zone address instead.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the data-zone alias is missing.
    """
    project = merged(DEVELOPMENT_FILE)
    primary = project["services"]["postgres-pg3ka"]
    aliases = primary["networks"]["data-net-nd9pc"]["aliases"]

    assert aliases
    assert "access-net-ha4mz" not in aliases


@pytest.mark.unit
def test_the_replication_rule_is_scoped_to_a_fixed_data_zone_range() -> None:
    """Pin the zone replication may arrive from.

    Confirms the data zone declares an explicit address range, without which the host-based
    authentication rule cannot name a stable range and would have to accept every network the
    primary happens to join.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the data zone has no declared range.
    """
    data_zone = load(DEVELOPMENT_FILE)["networks"]["data-net-nd9pc"]

    assert data_zone["ipam"]["config"][0]["subnet"]


@pytest.mark.unit
def test_the_primary_and_standby_configurations_are_separate_files() -> None:
    """Keep a primary-only setting from reaching the standby.

    Confirms the two nodes mount different configuration directories, because the base backup
    copies the primary's configuration file and a shared directory would silently apply archiving
    settings to the standby as well.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If both nodes mount the same configuration directory.
    """
    services = merged(DEVELOPMENT_FILE)["services"]

    def configuration_source(name: str) -> str:
        """Report the configuration directory a service mounts.

        Finds the bind mount targeting the server's configuration directory, which is the one that
        must differ between the two nodes.

        Arguments:
            name: Service to inspect.

        Returns:
            The repository path mounted as the configuration directory.
        """
        mounts = [str(mount) for mount in services[name]["volumes"]]

        return next(
            mount.split(":")[0]
            for mount in mounts
            if mount.split(":")[1] == "/etc/postgresql/conf.d"
        )

    assert configuration_source("postgres-pg3ka") != configuration_source("postgres-replica-pg6vy")


@pytest.mark.unit
def test_a_service_overriding_its_entrypoint_states_its_command() -> None:
    """Restore the command that overriding an entrypoint discards.

    Confirms any service replacing the image entrypoint also states its command, because Compose
    clears the image's own command when the entrypoint is overridden and the container would
    otherwise start the entrypoint with no arguments.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a service overrides its entrypoint without stating a command.
    """
    for project in MERGED_PROJECTS:
        for name, definition in (project.get("services") or {}).items():
            if definition.get("entrypoint"):
                assert definition.get("command"), name


@pytest.mark.unit
@pytest.mark.parametrize("project", MERGED_PROJECTS)
def test_every_image_is_pinned_to_a_tag_the_inventory_records(
    project: dict[str, Any],
) -> None:
    """Pin every image to a version the inventory carries.

    Confirms each image reference appears in the pinned-image table, so a tag cannot drift from the
    one the platform recorded and no floating tag slips in.

    Arguments:
        project: Merged project to inspect.

    Returns:
        None.

    Raises:
        AssertionError: If an image is absent from the pinned table.
    """
    document = (REPOSITORY_ROOT / "docs" / "platform" / "service-inventory.md").read_text(
        encoding="utf-8",
    )

    for name, definition in (project.get("services") or {}).items():
        image = definition.get("image")
        if image is None:
            assert definition.get("build"), name
            continue

        repository, _, tag = image.rpartition(":")

        assert f"| `{repository}` | `{tag}`" in document, image


@pytest.mark.unit
@pytest.mark.parametrize("environment", ["development", "testing"])
def test_the_documented_project_names_are_distinct(environment: str) -> None:
    """Namespace the two stacks apart.

    Confirms the conventions document still assigns each environment its own project name, which
    is what keeps container, volume, network, and port names from colliding.

    Arguments:
        environment: Environment to look up.

    Returns:
        None.

    Raises:
        AssertionError: If the document does not carry the expected project name.
    """
    text = CONVENTIONS_DOCUMENT.read_text(encoding="utf-8")

    assert f"| {environment} | `{PROJECT_NAMES[environment]}` |" in text
    assert PROJECT_NAMES["development"] != PROJECT_NAMES["testing"]
