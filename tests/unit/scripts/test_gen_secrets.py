"""Unit tests for the environment file generator.

Covers the manifest contract, idempotency, forced regeneration, the refusal paths, every documented
exit code, and the guarantee that no secret is ever printed, using a temporary repository so no test
touches the real environment files.
"""

import re
import runpy
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import bcrypt
import pytest

from scripts import gen_secrets

if TYPE_CHECKING:
    from collections.abc import Iterator

MANIFEST_SOURCE = gen_secrets.REPOSITORY_ROOT / ".env.example"
MINIMUM_BCRYPT_ROUNDS = 4
DOCUMENTED_BCRYPT_ROUNDS = 12
DIVERGENT_VALUE = "not-the-same-value"
DOCUMENTED_EXIT_CODES = {
    "ok": 0,
    "refused": 1,
    "manifest_unusable": 2,
    "tracked_file": 3,
    "force_without": 4,
}
SHIPPED_BCRYPT_ROUNDS = gen_secrets.BCRYPT_ROUNDS
SECRET_VARIABLES = (
    "DJANGO_SECRET_KEY",
    "POSTGRES_PASSWORD",
    "POSTGRES_REPLICATION_PASSWORD",
    "PGADMIN_DEFAULT_PASSWORD",
    "VALKEY_CACHE_PASSWORD",
    "VALKEY_CHANNELS_PASSWORD",
    "RABBITMQ_DEFAULT_PASS",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "GRAFANA_ADMIN_PASSWORD",
    "TRAEFIK_DASHBOARD_PASSWORD",
    "TRAEFIK_DASHBOARD_AUTH",
    "FLOWER_BASIC_AUTH",
)
CONVENTIONS_DOCUMENT = gen_secrets.REPOSITORY_ROOT / "docs" / "platform" / "conventions.md"


@dataclass
class FakeVersionControl:
    """Fabricated version control index.

    Reports whichever paths a test declares tracked, or refuses to answer at all, so both the
    refusal to overwrite a committed file and the refusal to proceed on an unreadable index can be
    exercised without a repository. Inherits nothing; it satisfies the VersionControl protocol
    structurally.

    Attributes:
        tracked: Repository-relative paths to report as tracked.
        available: Whether the index can be read at all.
    """

    tracked: frozenset[str] = field(default_factory=frozenset)
    available: bool = True

    def tracked_files(self) -> frozenset[str]:
        """Report the fabricated tracked paths.

        Returns whatever the fixture was configured with, or raises when the fixture describes an
        index that cannot be read.

        Returns:
            The configured repository-relative paths.

        Raises:
            IndexUnavailableError: If the fixture describes an unreadable index.
        """
        if not self.available:
            message = "fabricated index is unavailable"
            raise gen_secrets.IndexUnavailableError(message)

        return self.tracked


@pytest.fixture(autouse=True)
def fast_bcrypt() -> Iterator[None]:
    """Lower the password hashing cost for the duration of a test.

    Keeps the suite fast without weakening the generator, because the cost factor the platform
    ships is deliberately expensive and every test that generates a file pays it twice.

    Yields:
        None, for the duration of the test.
    """
    with patch.object(gen_secrets, "BCRYPT_ROUNDS", MINIMUM_BCRYPT_ROUNDS):
        yield


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """Build a temporary repository holding the real manifest.

    Copies the committed manifest into an empty directory, so each test generates against the
    authoritative variable list without touching the developer's own environment files.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        The temporary repository root.
    """
    (tmp_path / ".env.example").write_text(
        MANIFEST_SOURCE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    return tmp_path


def values_in(root: Path, relative: str) -> dict[str, str]:
    """Read a generated environment file.

    Parses the file with the generator's own reader, so a test grades the file exactly as Compose
    and a later run would read it.

    Arguments:
        root: Repository root holding the file.
        relative: Repository-relative name of the file.

    Returns:
        The variables the file declares.
    """
    return gen_secrets.parse_env_text((root / relative).read_text(encoding="utf-8"))


def documented_variables() -> set[str]:
    """Read the environment variable inventory out of its document.

    Parses the Section 3.2 table so the tests grade the manifest against the authoritative
    inventory rather than against itself, which is what lets a documented-but-unimplemented
    variable be detected.

    Returns:
        Every variable name the inventory declares.

    Raises:
        AssertionError: If the document carries no parsable inventory rows.
    """
    pattern = re.compile(r"^\|\s*`([A-Z][A-Z0-9_]*)`\s*\|")
    names: set[str] = set()
    for line in CONVENTIONS_DOCUMENT.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match is not None:
            names.add(match.group(1))

    assert names, f"no inventory rows parsed from {CONVENTIONS_DOCUMENT}"

    return names


@pytest.mark.unit
def test_the_manifest_declares_every_inventory_variable() -> None:
    """Cover the documented variable inventory exactly.

    Confirms the manifest and the inventory declare the same variables, parsed independently, so
    neither a documented variable missing from the manifest nor a manifest variable missing from
    the document can pass unnoticed.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the two sets differ.
    """
    manifest = set(gen_secrets.read_manifest(MANIFEST_SOURCE))

    assert documented_variables() == manifest


@pytest.mark.unit
def test_the_manifest_contains_no_real_secret() -> None:
    """Keep the committed manifest free of credentials.

    Confirms every variable the inventory marks secret carries only the placeholder, which is what
    makes the manifest safe to commit.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a secret variable carries anything but the placeholder.
    """
    manifest = gen_secrets.read_manifest(MANIFEST_SOURCE)

    for name in SECRET_VARIABLES:
        assert manifest[name] == gen_secrets.GENERATED_PLACEHOLDER


@pytest.mark.unit
def test_a_first_run_writes_all_three_files(repository: Path) -> None:
    """Produce every environment file from a fresh clone.

    Confirms one run creates the development file, the testing file, and the host-mode variant,
    each carrying the full manifest, which is the whole point of the generator.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If a file is missing or incomplete.
    """
    manifest = gen_secrets.read_manifest(MANIFEST_SOURCE)
    code = gen_secrets.main([], root=repository, version_control=FakeVersionControl())

    assert code == gen_secrets.EXIT_OK
    for relative in gen_secrets.ENVIRONMENT_FILES.values():
        assert set(values_in(repository, relative)) == set(manifest)


@pytest.mark.unit
def test_every_generated_secret_replaces_the_placeholder(repository: Path) -> None:
    """Leave no placeholder in a generated file.

    Confirms each secret variable is filled with a generated value rather than the manifest
    placeholder, since a placeholder reaching a running service is a silent misconfiguration.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If any variable still carries the placeholder.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    values = values_in(repository, ".env.development")

    assert gen_secrets.GENERATED_PLACEHOLDER not in values.values()
    for name in SECRET_VARIABLES:
        assert len(values[name]) > 1


@pytest.mark.unit
def test_no_two_services_share_a_credential(repository: Path) -> None:
    """Give every service its own credential.

    Confirms the generated secrets are all distinct, which is what stops one leaked credential
    unlocking a second service.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If any two secrets are equal.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    values = values_in(repository, ".env.development")
    secrets_written = [values[name] for name in SECRET_VARIABLES]

    assert len(set(secrets_written)) == len(secrets_written)


@pytest.mark.unit
def test_a_repeated_run_changes_nothing(repository: Path) -> None:
    """Leave a working configuration alone.

    Confirms a second default run preserves every value, which is what makes the generator safe to
    run against a stack that is already up.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If any value changes between runs.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    first = values_in(repository, ".env.development")

    gen_secrets.main([], root=repository, version_control=FakeVersionControl())

    assert values_in(repository, ".env.development") == first


@pytest.mark.unit
def test_a_variable_added_later_is_filled_without_touching_the_rest(repository: Path) -> None:
    """Top up a file rather than rewriting it.

    Confirms a variable absent from an existing file is added while every existing value survives,
    which is what lets the inventory grow without invalidating a running stack.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If the new variable is missing, or an existing value changed.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    path = repository / ".env.development"
    before = values_in(repository, ".env.development")

    reduced = {name: value for name, value in before.items() if name != "GRAFANA_ADMIN_PASSWORD"}
    path.write_text(gen_secrets.render_env_text(reduced), encoding="utf-8")

    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    after = values_in(repository, ".env.development")

    assert "GRAFANA_ADMIN_PASSWORD" in after
    assert after["GRAFANA_ADMIN_PASSWORD"] != before["GRAFANA_ADMIN_PASSWORD"]
    for name, value in reduced.items():
        assert after[name] == value


@pytest.mark.unit
def test_a_forced_run_replaces_every_value(repository: Path) -> None:
    """Regenerate everything when told to.

    Confirms a forced run replaces the existing secrets, which is the response to a suspected
    exposure.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If a secret survives a forced run.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    before = values_in(repository, ".env.development")

    code = gen_secrets.main(
        ["--environment", "development", "--force"],
        root=repository,
        version_control=FakeVersionControl(),
    )
    after = values_in(repository, ".env.development")

    assert code == gen_secrets.EXIT_OK
    for name in SECRET_VARIABLES:
        assert after[name] != before[name]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("environment", "expected", "excluded"),
    [
        ("development", "postgres-pg3ka-data", "postgres-tp8vn-data"),
        ("development", "postgres-replica-pg6vy-data", "rabbitmq-tr6mc-data"),
        ("testing", "postgres-tp8vn-data", "postgres-pg3ka-data"),
        ("testing", "rabbitmq-tr6mc-data", "grafana-gf7qv-data"),
    ],
)
def test_a_forced_run_names_the_volumes_for_the_environment_it_touched(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
    environment: str,
    expected: str,
    excluded: str,
) -> None:
    """Warn about the state the run actually invalidated.

    Confirms a forced run names the credential-derived volumes of the environment it regenerated
    and none belonging to the other, since recreating a volume from the wrong stack is wasted work
    and leaves the real one stale.

    Arguments:
        repository: Temporary repository holding the manifest.
        capsys: Fixture capturing standard output.
        environment: Environment to force.
        expected: Volume that must be named.
        excluded: Volume that must not be named.

    Returns:
        None.

    Raises:
        AssertionError: If the wrong volumes are named.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    capsys.readouterr()

    gen_secrets.main(
        ["--environment", environment, "--force"],
        root=repository,
        version_control=FakeVersionControl(),
    )
    printed = capsys.readouterr().out

    assert expected in printed
    assert excluded not in printed


@pytest.mark.unit
def test_every_named_volume_comes_from_the_registry() -> None:
    """Name only volumes the registry declares.

    Confirms each volume the forced-run warning can print appears in the conventions document, so a
    developer is never told to recreate something that does not exist.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a named volume is absent from the registry.
    """
    document = CONVENTIONS_DOCUMENT.read_text(encoding="utf-8")

    for volumes in gen_secrets.CREDENTIAL_DERIVED_VOLUMES.values():
        for volume in volumes:
            assert f"`{volume}`" in document


@pytest.mark.unit
def test_the_tracked_file_query_reads_the_index_it_is_given() -> None:
    """Ask Git what it tracks, and read the answer.

    Confirms the index query parses Git's listing into paths, using a fabricated repository and a
    fabricated Git so the result does not depend on what is installed or checked out.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the listing is not parsed into the expected paths.
    """
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="pyproject.toml\n\n docs/adr/README.md \n",
        stderr="",
    )

    with (
        patch.object(Path, "exists", return_value=True),
        patch("shutil.which", return_value="git"),
        patch.object(subprocess, "run", return_value=completed),
    ):
        tracked = gen_secrets.GitIndex().tracked_files()

    assert tracked == frozenset({"pyproject.toml", "docs/adr/README.md"})


@pytest.mark.unit
@pytest.mark.parametrize(
    "failure",
    [OSError, subprocess.TimeoutExpired("git", 1), subprocess.SubprocessError],
)
def test_a_git_that_cannot_answer_refuses_rather_than_assuming(
    failure: type[Exception] | Exception,
) -> None:
    """Fail closed when the index cannot be read.

    Confirms a Git that fails, hangs, or cannot be started raises rather than reporting an empty
    index, because assuming nothing is tracked is exactly the assumption that would write a secret
    into a committed file.

    Arguments:
        failure: Exception the fabricated execution raises.

    Returns:
        None.

    Raises:
        AssertionError: If the query reports an empty index instead of raising.
    """
    with (
        patch.object(Path, "exists", return_value=True),
        patch("shutil.which", return_value="git"),
        patch.object(subprocess, "run", side_effect=failure),
        pytest.raises(gen_secrets.IndexUnavailableError),
    ):
        gen_secrets.GitIndex().tracked_files()


@pytest.mark.unit
def test_a_failing_git_refuses_rather_than_assuming() -> None:
    """Fail closed when Git exits non-zero.

    Confirms a Git that runs but fails, as it does outside a repository, raises rather than
    reporting an empty index.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the query reports an empty index instead of raising.
    """
    completed = subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="not a repo")

    with (
        patch.object(Path, "exists", return_value=True),
        patch("shutil.which", return_value="git"),
        patch.object(subprocess, "run", return_value=completed),
        pytest.raises(gen_secrets.IndexUnavailableError),
    ):
        gen_secrets.GitIndex().tracked_files()


@pytest.mark.unit
def test_an_unreadable_index_stops_the_run(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Refuse the whole run when tracking cannot be established.

    Confirms an index that cannot be read stops the run with the documented refusal code and writes
    nothing, rather than proceeding on the assumption that no file is tracked.

    Arguments:
        repository: Temporary repository holding the manifest.
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If the run proceeds, or writes a file.
    """
    code = gen_secrets.main(
        [],
        root=repository,
        version_control=FakeVersionControl(available=False),
    )

    assert code == gen_secrets.EXIT_TRACKED_FILE
    assert "cannot determine" in capsys.readouterr().out
    assert not (repository / ".env.development").exists()


@pytest.mark.unit
def test_forcing_one_environment_leaves_the_others_alone(repository: Path) -> None:
    """Scope a forced run to the environment named.

    Confirms forcing development does not disturb the testing files, so recovering one environment
    never invalidates another.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If a testing value changes.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    before = values_in(repository, ".env.testing")

    gen_secrets.main(
        ["--environment", "development", "--force"],
        root=repository,
        version_control=FakeVersionControl(),
    )

    assert values_in(repository, ".env.testing") == before


@pytest.mark.unit
def test_the_two_testing_files_share_their_secrets(repository: Path) -> None:
    """Keep host mode usable against the testing stack.

    Confirms the testing file and its host-mode variant carry identical credentials, since host
    mode talks to the same containers through published ports.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If a credential differs between the two files.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    container = values_in(repository, ".env.testing")
    host = values_in(repository, ".env.testing.host")

    for name in SECRET_VARIABLES:
        assert container[name] == host[name]


@pytest.mark.unit
def test_development_and_testing_never_share_a_credential(repository: Path) -> None:
    """Isolate the two environments from each other.

    Confirms no secret is reused between development and testing, so both stacks can run at once
    without one holding the other's credentials.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If a credential is shared across environments.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    development = values_in(repository, ".env.development")
    testing = values_in(repository, ".env.testing")

    for name in SECRET_VARIABLES:
        assert development[name] != testing[name]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("COMPOSE_PROJECT_NAME", "localforge-test"),
        ("DJANGO_SETTINGS_MODULE", "config.settings.testing"),
        ("DJANGO_DEBUG", "false"),
        ("POSTGRES_HOST", "postgres-tp8vn"),
        ("POSTGRES_REPLICA_HOST", "postgres-tp8vn"),
        ("VALKEY_CACHE_HOST", "valkey-cache-tv4kq"),
        ("VALKEY_CHANNELS_HOST", "valkey-channels-tv9zw"),
        ("RABBITMQ_HOST", "rabbitmq-tr6mc"),
        ("S3_ENDPOINT_URL", "http://seaweedfs-ts3jd:8333"),
        ("EMAIL_BACKEND", "django.core.mail.backends.locmem.EmailBackend"),
        ("CELERY_TASK_ALWAYS_EAGER", "true"),
    ],
)
def test_the_testing_file_carries_its_documented_overrides(
    repository: Path,
    name: str,
    expected: str,
) -> None:
    """Point the testing environment at its own containers.

    Confirms each documented testing override is applied, since a host left naming a development
    container resolves to nothing on the testing networks.

    Arguments:
        repository: Temporary repository holding the manifest.
        name: Variable to inspect.
        expected: Value the testing environment must carry.

    Returns:
        None.

    Raises:
        AssertionError: If the override is not applied.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())

    assert values_in(repository, ".env.testing")[name] == expected


@pytest.mark.unit
def test_host_mode_reaches_every_service_through_the_loopback_interface(repository: Path) -> None:
    """Make the testing stack reachable from the host.

    Confirms every host variable in the host-mode file names the loopback interface and every port
    is the published testing port, which is what lets the suite run outside a container.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If a host still names a container, or a port is not the published one.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    values = values_in(repository, ".env.testing.host")

    hosts = [name for name in values if name.endswith("_HOST") and name != "EMAIL_HOST"]
    for name in [*hosts, "EMAIL_HOST"]:
        assert values[name] == "127.0.0.1"

    assert values["POSTGRES_PORT"] == "25432"
    assert values["VALKEY_CACHE_PORT"] == "26379"
    assert values["VALKEY_CHANNELS_PORT"] == "26380"
    assert values["RABBITMQ_PORT"] == "25672"
    assert values["EMAIL_PORT"] == "21025"
    assert values["S3_ENDPOINT_URL"] == "http://127.0.0.1:28333"


@pytest.mark.unit
def test_the_composed_urls_follow_the_credentials_they_are_built_from(repository: Path) -> None:
    """Keep a composed URL consistent with its parts.

    Confirms the broker and result URLs embed the generated credentials and the environment's own
    hosts, so a URL can never disagree with the variables beside it.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If a composed URL does not match its component variables.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    values = values_in(repository, ".env.testing.host")

    assert values["CELERY_BROKER_URL"] == (
        f"amqp://{values['RABBITMQ_DEFAULT_USER']}:{values['RABBITMQ_DEFAULT_PASS']}"
        f"@127.0.0.1:25672/{values['RABBITMQ_DEFAULT_VHOST']}"
    )
    assert values["CELERY_RESULT_BACKEND"] == (
        f"redis://:{values['VALKEY_CACHE_PASSWORD']}@127.0.0.1:26379/{values['VALKEY_RESULTS_DB']}"
    )


@pytest.mark.unit
def test_the_cache_and_result_databases_differ(repository: Path) -> None:
    """Keep clearing the cache from destroying task results.

    Confirms the cache and result database indexes are distinct, because clearing the cache issues
    a database-wide flush that would otherwise wipe every pending result.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If the two indexes are equal.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    values = values_in(repository, ".env.development")

    assert values["VALKEY_CACHE_DB"] != values["VALKEY_RESULTS_DB"]


@pytest.mark.unit
def test_the_dashboard_hash_is_built_from_the_stored_password(repository: Path) -> None:
    """Keep the entry the proxy reads and the password a developer types in step.

    Confirms the htpasswd entry is the stored password hashed for the expected user at the
    expected cost, because a hash that does not match its password locks everyone out of the
    dashboard while still looking well formed.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If the entry does not verify against the password beside it.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    values = values_in(repository, ".env.development")
    credential = values["TRAEFIK_DASHBOARD_AUTH"]
    user, _, digest = credential.partition(":")

    assert user == gen_secrets.BASIC_AUTH_USER
    assert digest.startswith(f"$2b${gen_secrets.BCRYPT_ROUNDS:02d}$")
    assert bcrypt.checkpw(values["TRAEFIK_DASHBOARD_PASSWORD"].encode(), digest.encode())


@pytest.mark.unit
def test_no_secret_is_ever_printed(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep credentials out of the terminal and the logs.

    Confirms neither a first run nor a forced run prints any generated value, since a printed
    secret reaches scrollback, terminal logs, and continuous integration output.

    Arguments:
        repository: Temporary repository holding the manifest.
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If any generated value appears in the output.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    first = capsys.readouterr().out

    gen_secrets.main(
        ["--environment", "development", "--force"],
        root=repository,
        version_control=FakeVersionControl(),
    )
    printed = first + capsys.readouterr().out

    for relative in gen_secrets.ENVIRONMENT_FILES.values():
        for name, value in values_in(repository, relative).items():
            if name in SECRET_VARIABLES or name in gen_secrets.COMPOSED_VALUES:
                assert value not in printed


@pytest.mark.unit
def test_a_tracked_target_file_is_refused(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Refuse to write a file Git already tracks.

    Confirms the run stops with the documented code and writes nothing, because a generated secret
    committed once stays in history whatever happens to the working tree afterwards.

    Arguments:
        repository: Temporary repository holding the manifest.
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If the run does not refuse, or writes the file anyway.
    """
    version_control = FakeVersionControl(tracked=frozenset({".env.development"}))

    code = gen_secrets.main([], root=repository, version_control=version_control)

    assert code == gen_secrets.EXIT_TRACKED_FILE
    assert "refusing" in capsys.readouterr().out
    assert not (repository / ".env.development").exists()


@pytest.mark.unit
def test_a_tracked_file_outside_the_selection_does_not_block_a_run(repository: Path) -> None:
    """Refuse only what the run would actually write.

    Confirms a tracked testing file does not stop a development-only run, so the refusal is scoped
    to the files at risk rather than to the repository as a whole.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If the run is refused.
    """
    version_control = FakeVersionControl(tracked=frozenset({".env.testing"}))

    code = gen_secrets.main(
        ["--environment", "development"],
        root=repository,
        version_control=version_control,
    )

    assert code == gen_secrets.EXIT_OK


@pytest.mark.unit
def test_forcing_without_an_environment_is_refused(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Require a forced run to name its target.

    Confirms forcing without an environment stops with the documented code, so regenerating every
    credential on the machine cannot happen by a slip of the keyboard.

    Arguments:
        repository: Temporary repository holding the manifest.
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If the run is not refused with the documented code.
    """
    code = gen_secrets.main(["--force"], root=repository, version_control=FakeVersionControl())

    assert code == gen_secrets.EXIT_FORCE_WITHOUT_ENVIRONMENT
    assert "--environment" in capsys.readouterr().out
    assert not (repository / ".env.development").exists()


@pytest.mark.unit
def test_an_absent_manifest_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stop when the variable manifest is missing.

    Confirms an absent manifest stops the run with the documented code, since generating from no
    manifest would silently produce an empty configuration.

    Arguments:
        tmp_path: Temporary directory with no manifest in it.
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If the run does not stop with the documented code.
    """
    code = gen_secrets.main([], root=tmp_path, version_control=FakeVersionControl())

    assert code == gen_secrets.EXIT_MANIFEST_UNUSABLE
    assert "manifest unusable" in capsys.readouterr().out


@pytest.mark.unit
@pytest.mark.parametrize("contents", ["", "# only a comment\n", "NOT_AN_ASSIGNMENT\n"])
def test_an_unusable_manifest_is_refused(tmp_path: Path, contents: str) -> None:
    """Stop when the manifest cannot be read.

    Confirms an empty manifest, a manifest of comments, and a malformed line each stop the run with
    the documented code rather than producing a partial configuration.

    Arguments:
        tmp_path: Temporary directory standing in for a repository.
        contents: Manifest contents to write.

    Returns:
        None.

    Raises:
        AssertionError: If the run does not stop with the documented code.
    """
    (tmp_path / ".env.example").write_text(contents, encoding="utf-8")

    code = gen_secrets.main([], root=tmp_path, version_control=FakeVersionControl())

    assert code == gen_secrets.EXIT_MANIFEST_UNUSABLE


@pytest.mark.unit
def test_an_unusable_existing_file_is_refused(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stop when an existing environment file cannot be read.

    Confirms a corrupted target file stops the run with the documented code, rather than being
    silently overwritten and losing the credentials a running stack is using.

    Arguments:
        repository: Temporary repository holding the manifest.
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If the run does not stop with the documented code.
    """
    (repository / ".env.development").write_text("THIS IS NOT AN ASSIGNMENT\n", encoding="utf-8")

    code = gen_secrets.main([], root=repository, version_control=FakeVersionControl())

    assert code == gen_secrets.EXIT_MANIFEST_UNUSABLE
    assert "refusing to write" in capsys.readouterr().out


@pytest.mark.unit
def test_an_unreadable_manifest_is_reported_rather_than_raised(tmp_path: Path) -> None:
    """Report a manifest the operating system refuses to open.

    Confirms a read failure becomes the documented refusal rather than an unhandled error, so a
    permission problem reads as a configuration message.

    Arguments:
        tmp_path: Temporary directory standing in for a repository.

    Returns:
        None.

    Raises:
        AssertionError: If the failure is not reported as a manifest error.
    """
    manifest = tmp_path / ".env.example"
    manifest.write_text("A=1\n", encoding="utf-8")

    with (
        patch.object(Path, "read_text", side_effect=OSError("denied")),
        pytest.raises(gen_secrets.ManifestError),
    ):
        gen_secrets.read_manifest(manifest)


@pytest.mark.unit
def test_comments_and_blank_lines_are_ignored_when_reading() -> None:
    """Read the form Compose itself accepts.

    Confirms comments and blank lines are skipped and a value containing an equals sign survives
    intact, which matters for the composed URLs.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the parsed variables differ from the expectation.
    """
    parsed = gen_secrets.parse_env_text("# header\n\nA=1\nB=x=y\n")

    assert parsed == {"A": "1", "B": "x=y"}


@pytest.mark.unit
def test_a_generated_file_survives_its_own_reader() -> None:
    """Keep the written form readable by the next run.

    Confirms a generated file parses back to exactly the variables written, which is what makes the
    top-up behaviour reliable across runs.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the file does not round-trip through the reader.
    """
    values = {"A": "1", "B": "two"}
    text = gen_secrets.render_env_text(values)

    assert gen_secrets.parse_env_text(text) == values


@pytest.mark.unit
def test_the_written_file_carries_no_comment_and_no_blank_line(repository: Path) -> None:
    """Write only the assignments the file exists for.

    Confirms the generated file contains no comment and no blank line, because the repository bans
    comments outright and because the dotenv encryption format drops blank lines, which would
    otherwise make a decrypted file differ from the original.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If the file carries a comment or a blank line.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())

    for relative in gen_secrets.ENVIRONMENT_FILES.values():
        lines = (repository / relative).read_text(encoding="utf-8").splitlines()

        assert lines
        assert all(line.strip() for line in lines)
        assert not any(line.lstrip().startswith("#") for line in lines)


@pytest.mark.unit
def test_the_committed_manifest_carries_no_comment() -> None:
    """Keep the committed manifest free of commentary.

    Confirms the manifest is assignments alone, so the repository's ban on comments holds for the
    one environment file that is committed as well as for the generated ones.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the manifest carries a comment.
    """
    lines = MANIFEST_SOURCE.read_text(encoding="utf-8").splitlines()

    assert not any(line.lstrip().startswith("#") for line in lines)


@pytest.mark.unit
def test_the_file_is_written_with_line_feed_endings(repository: Path) -> None:
    """Write the line endings the repository enforces.

    Confirms the generated file uses line feeds alone, so a file generated on Windows is the same
    file a container reads.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If the file carries a carriage return.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())

    assert b"\r" not in (repository / ".env.development").read_bytes()


@pytest.mark.unit
def test_each_generator_produces_a_distinct_value_of_the_right_shape() -> None:
    """Produce unpredictable values of the documented form.

    Confirms each generator yields a different value on every call and the object storage key is
    hexadecimal, which some clients require.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a generator repeats itself or produces the wrong alphabet.
    """
    assert gen_secrets.generate_secret_key() != gen_secrets.generate_secret_key()
    assert gen_secrets.generate_password() != gen_secrets.generate_password()

    key = gen_secrets.generate_access_key()

    assert key != gen_secrets.generate_access_key()
    assert int(key, 16) >= 0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("url", "component"),
    [
        ("CELERY_BROKER_URL", "RABBITMQ_DEFAULT_PASS"),
        ("CELERY_RESULT_BACKEND", "VALKEY_CACHE_PASSWORD"),
    ],
)
def test_a_composed_url_left_disagreeing_with_its_parts_is_refused(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
    url: str,
    component: str,
) -> None:
    """Refuse a composed URL that no longer matches its components.

    Confirms removing a component, which causes it to be refilled with a new value, stops the run
    rather than quietly rewriting the URL, because the generator cannot prove whether that URL is
    stale or a deliberate endpoint change.

    Arguments:
        repository: Temporary repository holding the manifest.
        capsys: Fixture capturing standard output.
        url: Composed variable that would disagree.
        component: Component removed from the existing file.

    Returns:
        None.

    Raises:
        AssertionError: If the run proceeds, or the URL is rewritten.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    values = values_in(repository, ".env.development")
    before = values[url]
    del values[component]
    (repository / ".env.development").write_text(
        gen_secrets.render_env_text(values),
        encoding="utf-8",
    )

    code = gen_secrets.main([], root=repository, version_control=FakeVersionControl())

    assert code == gen_secrets.EXIT_REFUSED
    assert url in capsys.readouterr().out
    assert values_in(repository, ".env.development")[url] == before


@pytest.mark.unit
@pytest.mark.parametrize("component", ["RABBITMQ_HOST", "VALKEY_CACHE_PORT"])
def test_refilling_a_deterministic_component_disturbs_nothing(
    repository: Path,
    component: str,
) -> None:
    """Leave a composed URL alone when a refilled component is unchanged.

    Confirms removing a component whose value comes from the manifest rather than from generation
    refills it with the same value, so the URL still agrees and the run proceeds normally.

    Arguments:
        repository: Temporary repository holding the manifest.
        component: Component removed from the existing file.

    Returns:
        None.

    Raises:
        AssertionError: If the run is refused, or a value changes.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    before = values_in(repository, ".env.development")
    values = dict(before)
    del values[component]
    (repository / ".env.development").write_text(
        gen_secrets.render_env_text(values),
        encoding="utf-8",
    )

    code = gen_secrets.main([], root=repository, version_control=FakeVersionControl())

    assert code == gen_secrets.EXIT_OK
    assert values_in(repository, ".env.development") == before


@pytest.mark.unit
def test_a_composed_url_is_rebuilt_once_it_is_removed(repository: Path) -> None:
    """Rebuild a composed URL the operator asked to have rebuilt.

    Confirms removing the URL itself, which is what the refusal message instructs, produces a URL
    consistent with the credentials beside it.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If the rebuilt URL does not carry the current credentials.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    values = values_in(repository, ".env.development")
    del values["CELERY_BROKER_URL"]
    del values["RABBITMQ_DEFAULT_PASS"]
    (repository / ".env.development").write_text(
        gen_secrets.render_env_text(values),
        encoding="utf-8",
    )

    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    after = values_in(repository, ".env.development")

    assert after["RABBITMQ_DEFAULT_PASS"] in after["CELERY_BROKER_URL"]


@pytest.mark.unit
def test_a_forced_run_rebuilds_a_composed_url(repository: Path) -> None:
    """Rebuild a composed URL when everything is being regenerated.

    Confirms a forced run derives the URL from the new credentials rather than refusing, since
    forcing is the operator saying the existing values no longer matter.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If the URL does not carry the regenerated credentials.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())

    gen_secrets.main(
        ["--environment", "development", "--force"],
        root=repository,
        version_control=FakeVersionControl(),
    )
    after = values_in(repository, ".env.development")

    assert after["RABBITMQ_DEFAULT_PASS"] in after["CELERY_BROKER_URL"]
    assert after["VALKEY_CACHE_PASSWORD"] in after["CELERY_RESULT_BACKEND"]


@pytest.mark.unit
def test_an_edited_url_is_kept_even_when_a_component_is_also_missing(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep a deliberate edit when a component happens to be missing too.

    Confirms an edited URL combined with an absent component still refuses, since a refilled
    component is no evidence that the URL was ever derived from the components beside it.

    Arguments:
        repository: Temporary repository holding the manifest.
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If the edit is discarded.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    values = values_in(repository, ".env.development")
    values["CELERY_BROKER_URL"] = "amqps://deliberate.example:5671/localforge"
    del values["RABBITMQ_PORT"]
    (repository / ".env.development").write_text(
        gen_secrets.render_env_text(values),
        encoding="utf-8",
    )

    code = gen_secrets.main([], root=repository, version_control=FakeVersionControl())

    assert code == gen_secrets.EXIT_REFUSED
    assert "CELERY_BROKER_URL" in capsys.readouterr().out
    assert values_in(repository, ".env.development")["CELERY_BROKER_URL"] == (
        "amqps://deliberate.example:5671/localforge"
    )


@pytest.mark.unit
def test_a_deleted_sibling_inherits_the_credentials_that_remain(repository: Path) -> None:
    """Keep a sharing group together when only one member survives.

    Confirms regenerating the host-mode file after deleting it reuses the credentials the testing
    file already holds, which is the path a fresh clone takes because only the container-mode file
    is committed encrypted.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If the regenerated file holds different credentials.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    container = values_in(repository, ".env.testing")
    (repository / ".env.testing.host").unlink()

    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    host = values_in(repository, ".env.testing.host")

    for name in SECRET_VARIABLES:
        assert host[name] == container[name]


@pytest.mark.unit
def test_a_secret_missing_from_one_sibling_is_filled_from_the_other(repository: Path) -> None:
    """Fill a gap in one member from the value its sibling holds.

    Confirms a credential absent from only one file of a sharing group is taken from the other
    rather than generated afresh, which is what stops the pair drifting apart one variable at a
    time.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If the gap is filled with a different value.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    container = values_in(repository, ".env.testing")

    reduced = {
        name: value
        for name, value in values_in(repository, ".env.testing.host").items()
        if name != "POSTGRES_PASSWORD"
    }
    (repository / ".env.testing.host").write_text(
        gen_secrets.render_env_text(reduced),
        encoding="utf-8",
    )

    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    host = values_in(repository, ".env.testing.host")

    assert host["POSTGRES_PASSWORD"] == container["POSTGRES_PASSWORD"]


@pytest.mark.unit
def test_a_variable_the_generator_does_not_own_is_preserved(repository: Path) -> None:
    """Leave a developer's own addition alone.

    Confirms a variable absent from the manifest survives a run, because the generator owns the
    variables it knows about rather than the file as a whole.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If the unknown variable is discarded.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    values = values_in(repository, ".env.development")
    values["MY_LOCAL_FLAG"] = "keep-me"
    (repository / ".env.development").write_text(
        gen_secrets.render_env_text(values),
        encoding="utf-8",
    )

    gen_secrets.main([], root=repository, version_control=FakeVersionControl())

    assert values_in(repository, ".env.development")["MY_LOCAL_FLAG"] == "keep-me"


@pytest.mark.unit
def test_a_value_carrying_a_dollar_sign_is_quoted(repository: Path) -> None:
    """Stop Compose expanding a credential it should read literally.

    Confirms a value containing a dollar sign is written single-quoted, because Compose expands
    unquoted values in both env_file and --env-file and would truncate a bcrypt hash at its third
    dollar, yielding a credential that cannot authenticate.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If the value is written unquoted.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    raw = (repository / ".env.development").read_text(encoding="utf-8")
    credential = values_in(repository, ".env.development")["TRAEFIK_DASHBOARD_AUTH"]

    assert "$" in credential
    assert f"TRAEFIK_DASHBOARD_AUTH='{credential}'" in raw


@pytest.mark.unit
def test_quoting_is_applied_only_where_expansion_would_occur() -> None:
    """Quote only what needs quoting.

    Confirms an ordinary value is written as-is while a value carrying a dollar sign is quoted, so
    the files stay readable and only the values at risk carry quotes.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If quoting is applied to the wrong values.
    """
    assert gen_secrets.render_value("NAME", "plain") == "plain"
    assert gen_secrets.render_value("NAME", "has$dollar") == "'has$dollar'"
    assert gen_secrets.unquote("'has$dollar'") == "has$dollar"
    assert gen_secrets.unquote("plain") == "plain"


@pytest.mark.unit
def test_a_value_that_cannot_be_quoted_is_refused() -> None:
    """Refuse a value no quoting scheme can carry.

    Confirms a value holding both a dollar sign and a single quote is rejected rather than written
    in a form Compose would misread.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the value is not refused.
    """
    with pytest.raises(gen_secrets.ManifestError):
        gen_secrets.render_value("NAME", "has$dollar'and'quote")


@pytest.mark.unit
def test_the_task_dashboard_credential_is_plaintext(repository: Path) -> None:
    """Give the task dashboard a password it can actually compare.

    Confirms the task dashboard credential is a plaintext pair rather than a hash, because that
    dashboard compares its configured value literally, so a hash would make the digest itself the
    password while pretending otherwise.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If the credential carries a hash.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    user, _, password = values_in(repository, ".env.development")["FLOWER_BASIC_AUTH"].partition(
        ":"
    )

    assert user == "admin"
    assert not password.startswith("$")
    assert len(password) > 1


@pytest.mark.unit
def test_the_shipped_password_hashing_cost_is_deliberate() -> None:
    """Keep the shipped work factor from drifting.

    Confirms the cost factor the generator ships matches the documented one, since every test
    lowers it for speed and would otherwise leave the real number unguarded.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the shipped cost factor is not the documented one.
    """
    document = CONVENTIONS_DOCUMENT.read_text(encoding="utf-8")

    assert SHIPPED_BCRYPT_ROUNDS == DOCUMENTED_BCRYPT_ROUNDS
    assert f"bcrypt at cost {SHIPPED_BCRYPT_ROUNDS}" in document


@pytest.mark.unit
def test_a_manifest_without_a_composed_variable_is_left_alone() -> None:
    """Skip a composed value the manifest does not declare.

    Confirms resolution does not invent a composed variable that is absent from the manifest, so a
    reduced manifest produces exactly the variables it asked for.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a composed variable appears without being declared.
    """
    manifest = {"POSTGRES_USER": "localforge_app"}

    resolved, added, regenerated = gen_secrets.resolve_values(
        manifest,
        gen_secrets.NO_OVERRIDES,
        {},
        {},
        force=False,
    )

    assert resolved == manifest
    assert added == ["POSTGRES_USER"]
    assert regenerated == []


@pytest.mark.unit
def test_a_directory_that_is_not_a_repository_tracks_nothing(tmp_path: Path) -> None:
    """Answer an empty index only where it is provable.

    Confirms a directory with no repository in it reports nothing tracked without consulting Git at
    all, because a file cannot be committed from a tree that is not a checkout.

    Arguments:
        tmp_path: Temporary directory that is not a repository.

    Returns:
        None.

    Raises:
        AssertionError: If Git is consulted, or the index is not empty.
    """
    with patch("shutil.which") as which:
        assert gen_secrets.GitIndex(tmp_path).tracked_files() == frozenset()

    which.assert_not_called()


@pytest.mark.unit
def test_a_repository_without_git_installed_refuses_rather_than_assuming(tmp_path: Path) -> None:
    """Refuse when a checkout's index cannot be read at all.

    Confirms a real checkout on a machine without Git raises rather than reporting an empty index,
    because the file can still be committed later by another client and an unreadable index is no
    evidence that writing a secret there is safe.

    Arguments:
        tmp_path: Temporary directory standing in for a checkout.

    Returns:
        None.

    Raises:
        AssertionError: If the query reports an empty index instead of raising.
    """
    (tmp_path / ".git").mkdir()

    with (
        patch("shutil.which", return_value=None),
        pytest.raises(gen_secrets.IndexUnavailableError),
    ):
        gen_secrets.GitIndex(tmp_path).tracked_files()


@pytest.mark.unit
@pytest.mark.parametrize("invalid", ["", gen_secrets.GENERATED_PLACEHOLDER])
def test_an_empty_or_placeholder_secret_is_replaced_rather_than_kept(
    repository: Path,
    invalid: str,
) -> None:
    """Treat a value that is not a credential as absent.

    Confirms an empty value and the manifest placeholder are both filled with a real secret, since
    a line that exists but carries no credential would otherwise be preserved forever and handed to
    a service that cannot authenticate with it.

    Arguments:
        repository: Temporary repository holding the manifest.
        invalid: Value standing in for an absent credential.

    Returns:
        None.

    Raises:
        AssertionError: If the unusable value survives the run.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    values = values_in(repository, ".env.development")
    values["POSTGRES_PASSWORD"] = invalid
    (repository / ".env.development").write_text(
        gen_secrets.render_env_text(values),
        encoding="utf-8",
    )

    gen_secrets.main([], root=repository, version_control=FakeVersionControl())

    assert values_in(repository, ".env.development")["POSTGRES_PASSWORD"] not in (
        gen_secrets.INVALID_VALUES
    )


@pytest.mark.unit
@pytest.mark.parametrize("invalid", ["", gen_secrets.GENERATED_PLACEHOLDER])
def test_an_unusable_secret_is_never_propagated_to_a_sibling(
    repository: Path,
    invalid: str,
) -> None:
    """Refuse to share a value that is not a credential.

    Confirms an empty or placeholder value in one member of a sharing group is not copied to the
    other, so a broken credential cannot spread across the pair.

    Arguments:
        repository: Temporary repository holding the manifest.
        invalid: Value standing in for an absent credential.

    Returns:
        None.

    Raises:
        AssertionError: If the unusable value reaches either file.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    values = values_in(repository, ".env.testing")
    values["POSTGRES_PASSWORD"] = invalid
    (repository / ".env.testing").write_text(
        gen_secrets.render_env_text(values),
        encoding="utf-8",
    )
    (repository / ".env.testing.host").unlink()

    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    container = values_in(repository, ".env.testing")["POSTGRES_PASSWORD"]
    host = values_in(repository, ".env.testing.host")["POSTGRES_PASSWORD"]

    assert container not in gen_secrets.INVALID_VALUES
    assert container == host


@pytest.mark.unit
def test_siblings_holding_different_credentials_are_refused(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Refuse to choose between two credentials.

    Confirms a sharing group whose members hold different values for one secret stops the run and
    leaves both files untouched, because silently preferring one would lock the other's services
    out without saying so.

    Arguments:
        repository: Temporary repository holding the manifest.
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If the run proceeds, or either file changes.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    values = values_in(repository, ".env.testing.host")
    values["POSTGRES_PASSWORD"] = DIVERGENT_VALUE
    (repository / ".env.testing.host").write_text(
        gen_secrets.render_env_text(values),
        encoding="utf-8",
    )
    before = {
        relative: (repository / relative).read_bytes()
        for relative in gen_secrets.ENVIRONMENT_FILES.values()
    }

    code = gen_secrets.main([], root=repository, version_control=FakeVersionControl())

    assert code == gen_secrets.EXIT_REFUSED
    assert "differs between files" in capsys.readouterr().out
    for relative, contents in before.items():
        assert (repository / relative).read_bytes() == contents


@pytest.mark.unit
def test_a_hand_edited_composed_value_is_not_discarded(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Leave a deliberate edit alone.

    Confirms a composed URL edited away from its components, with none of those components changed,
    stops the run rather than being silently rewritten, since the generator cannot tell a
    deliberate endpoint change from a stale value on its own.

    Arguments:
        repository: Temporary repository holding the manifest.
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If the edit is discarded, or the run proceeds.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    values = values_in(repository, ".env.development")
    values["CELERY_BROKER_URL"] = "amqps://deliberate.example:5671/localforge"
    (repository / ".env.development").write_text(
        gen_secrets.render_env_text(values),
        encoding="utf-8",
    )

    code = gen_secrets.main([], root=repository, version_control=FakeVersionControl())

    assert code == gen_secrets.EXIT_REFUSED
    assert "does not match the variables it is built from" in capsys.readouterr().out
    assert values_in(repository, ".env.development")["CELERY_BROKER_URL"] == (
        "amqps://deliberate.example:5671/localforge"
    )


@pytest.mark.unit
def test_a_refused_run_leaves_every_file_untouched(
    repository: Path,
) -> None:
    """Write nothing at all when any file is refused.

    Confirms a problem discovered in one environment stops the whole run before anything is
    written, so a rotation can never be applied to half a stack.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If any file changes.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    values = values_in(repository, ".env.testing.host")
    values["VALKEY_CACHE_PASSWORD"] = DIVERGENT_VALUE
    (repository / ".env.testing.host").write_text(
        gen_secrets.render_env_text(values),
        encoding="utf-8",
    )
    before = {
        relative: (repository / relative).read_bytes()
        for relative in gen_secrets.ENVIRONMENT_FILES.values()
    }

    code = gen_secrets.main([], root=repository, version_control=FakeVersionControl())

    assert code == gen_secrets.EXIT_REFUSED
    for relative, contents in before.items():
        assert (repository / relative).read_bytes() == contents

    assert not list(repository.glob("*.partial"))


@pytest.mark.unit
def test_an_unreadable_existing_file_is_reported_rather_than_raised(repository: Path) -> None:
    """Report a file the operating system refuses to open.

    Confirms a read failure becomes the documented refusal rather than an unhandled error, so a
    permission problem reads as a configuration message.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If the failure is not reported as a manifest error.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())

    with (
        patch.object(Path, "read_text", side_effect=OSError("denied")),
        pytest.raises(gen_secrets.ManifestError),
    ):
        gen_secrets.read_existing(repository / ".env.development")


@pytest.mark.unit
def test_a_staging_failure_leaves_every_file_untouched(repository: Path) -> None:
    """Write nothing when the new files cannot be staged.

    Confirms a failure while staging leaves every destination as it was and removes the partial
    files, so a full disk cannot leave the stack half-configured.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If a file changes, or a partial file survives.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    before = {
        relative: (repository / relative).read_bytes()
        for relative in gen_secrets.ENVIRONMENT_FILES.values()
    }
    original_write = Path.write_text
    attempts = 0

    def fail_on_second(self: Path, data: str, **kwargs: object) -> int:
        """Stage the first file and refuse the second.

        Stands in for a volume that fills partway through staging, which is the case the cleanup
        of already-staged files exists to handle.

        Arguments:
            self: File being written.
            data: Contents being written.
            kwargs: Keyword arguments passed through to the real call.

        Returns:
            The number of characters written, as the real call does.

        Raises:
            OSError: On every call after the first.
        """
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            message = "disk full"
            raise OSError(message)

        del kwargs

        return original_write(self, data, encoding="utf-8", newline="\n")

    with patch.object(Path, "write_text", fail_on_second):
        code = gen_secrets.main([], root=repository, version_control=FakeVersionControl())

    assert code == gen_secrets.EXIT_MANIFEST_UNUSABLE
    for relative, contents in before.items():
        assert (repository / relative).read_bytes() == contents

    assert not list(repository.glob("*.partial"))


@pytest.mark.unit
def test_a_replacement_failure_restores_what_was_already_replaced(repository: Path) -> None:
    """Undo a rotation that could not be completed.

    Confirms a failure partway through replacing the destinations restores the files already
    replaced, so a sharing group cannot be left half-rotated and disagreeing with itself.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If any file differs afterwards, or a partial file survives.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    before = {
        relative: (repository / relative).read_bytes()
        for relative in gen_secrets.ENVIRONMENT_FILES.values()
    }
    replacements = 0

    def fail_on_second(self: Path, target: Path) -> Path:
        """Replace the first destination and refuse the second.

        Stands in for a replacement that succeeds once and then fails, which is the case the
        rollback exists to handle.

        Arguments:
            self: Staged file being moved into place.
            target: Destination being replaced.

        Returns:
            The destination path, as the real call does.

        Raises:
            OSError: On every call after the first.
        """
        nonlocal replacements
        replacements += 1
        if replacements > 1:
            message = "replacement refused"
            raise OSError(message)

        return Path(shutil.move(str(self), str(target)))

    with patch.object(Path, "replace", fail_on_second):
        code = gen_secrets.main(
            ["--environment", "testing", "--force"],
            root=repository,
            version_control=FakeVersionControl(),
        )

    assert code == gen_secrets.EXIT_MANIFEST_UNUSABLE
    for relative, contents in before.items():
        assert (repository / relative).read_bytes() == contents

    assert not list(repository.glob("*.partial"))


@pytest.mark.unit
def test_a_replacement_failure_removes_a_file_that_did_not_exist(repository: Path) -> None:
    """Undo the creation of a file that had no previous contents.

    Confirms rollback deletes a destination that was created by this run rather than restoring
    contents it never had, so a failed first run leaves no half-written file behind.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If a file survives the rollback.
    """
    replacements = 0

    def fail_on_second(self: Path, target: Path) -> Path:
        """Replace the first destination and refuse the second.

        Stands in for a replacement that succeeds once and then fails, against destinations that
        did not previously exist.

        Arguments:
            self: Staged file being moved into place.
            target: Destination being replaced.

        Returns:
            The destination path, as the real call does.

        Raises:
            OSError: On every call after the first.
        """
        nonlocal replacements
        replacements += 1
        if replacements > 1:
            message = "replacement refused"
            raise OSError(message)

        return Path(shutil.move(str(self), str(target)))

    with patch.object(Path, "replace", fail_on_second):
        code = gen_secrets.main(
            ["--environment", "testing"],
            root=repository,
            version_control=FakeVersionControl(),
        )

    assert code == gen_secrets.EXIT_MANIFEST_UNUSABLE
    assert not (repository / ".env.testing").exists()
    assert not list(repository.glob("*.partial"))


@pytest.mark.unit
def test_the_documented_exit_codes_are_the_ones_implemented() -> None:
    """Keep the exit codes in step with the document.

    Confirms each code the generator returns is the one the conventions document assigns it, and
    that a refusal over existing state is distinguishable from an unusable manifest, so a caller
    branching on an exit code reads the same contract the generator implements.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an implemented code differs from the documented one.
    """
    document = CONVENTIONS_DOCUMENT.read_text(encoding="utf-8")

    assert DOCUMENTED_EXIT_CODES["ok"] == gen_secrets.EXIT_OK
    assert DOCUMENTED_EXIT_CODES["refused"] == gen_secrets.EXIT_REFUSED
    assert DOCUMENTED_EXIT_CODES["manifest_unusable"] == gen_secrets.EXIT_MANIFEST_UNUSABLE
    assert DOCUMENTED_EXIT_CODES["tracked_file"] == gen_secrets.EXIT_TRACKED_FILE
    assert DOCUMENTED_EXIT_CODES["force_without"] == gen_secrets.EXIT_FORCE_WITHOUT_ENVIRONMENT
    assert "`1` refused because the existing files" in document
    assert "`2` `.env.example` missing or unparsable" in document


@pytest.mark.unit
@pytest.mark.parametrize("url", ["CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"])
@pytest.mark.parametrize("placeholder", ["", gen_secrets.GENERATED_PLACEHOLDER])
def test_a_composed_value_holding_no_real_value_is_derived_rather_than_refused(
    repository: Path,
    url: str,
    placeholder: str,
) -> None:
    """Fill a composed value that was never written rather than refusing it.

    Confirms a composed URL left empty or still carrying the manifest placeholder is derived, since
    neither is an endpoint anyone chose, and refusing would block the plausible first run where a
    developer copied the manifest into place before generating.

    Arguments:
        repository: Temporary repository holding the manifest.
        url: Composed variable to inspect.
        placeholder: Value standing in for a composed value nobody wrote.

    Returns:
        None.

    Raises:
        AssertionError: If the run is refused, or the value is left unusable.
    """
    gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    values = values_in(repository, ".env.development")
    values[url] = placeholder
    (repository / ".env.development").write_text(
        gen_secrets.render_env_text(values),
        encoding="utf-8",
    )

    code = gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    after = values_in(repository, ".env.development")

    assert code == gen_secrets.EXIT_OK
    assert after[url] not in gen_secrets.INVALID_VALUES
    assert after["RABBITMQ_DEFAULT_PASS"] in after["CELERY_BROKER_URL"]


@pytest.mark.unit
def test_the_manifest_can_be_copied_into_place_and_generated_over(repository: Path) -> None:
    """Survive the manifest being used as a starting file.

    Confirms copying the committed manifest to an environment file and generating fills every
    placeholder rather than refusing, because the manifest reads like a template and doing so is
    the obvious first move.

    Arguments:
        repository: Temporary repository holding the manifest.

    Returns:
        None.

    Raises:
        AssertionError: If the run is refused, or a placeholder survives.
    """
    (repository / ".env.development").write_text(
        (repository / ".env.example").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    code = gen_secrets.main([], root=repository, version_control=FakeVersionControl())
    after = values_in(repository, ".env.development")

    assert code == gen_secrets.EXIT_OK
    assert gen_secrets.GENERATED_PLACEHOLDER not in after.values()


@pytest.mark.unit
@pytest.mark.parametrize("environment", ["development", "testing"])
def test_the_committed_encrypted_file_declares_the_same_variables(environment: str) -> None:
    """Keep the committed secrets in step with the manifest.

    Confirms each encrypted file declares exactly the manifest's variables, because the dotenv
    encryption format leaves names in the clear and a rename applied only to the manifest leaves a
    fresh clone recovering a configuration the code no longer reads.

    Arguments:
        environment: Environment whose encrypted file is inspected.

    Returns:
        None.

    Raises:
        AssertionError: If the encrypted file and the manifest declare different variables.
    """
    manifest = set(gen_secrets.read_manifest(MANIFEST_SOURCE))
    encrypted = gen_secrets.REPOSITORY_ROOT / f".env.{environment}.sops"
    declared = {
        line.partition("=")[0]
        for line in encrypted.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith(("#", "sops_"))
    }

    assert manifest - declared == set()


@pytest.mark.unit
def test_the_script_guard_runs_the_generator(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the generator through its script guard.

    Executes the module under the name Python assigns to a directly executed script, asking for a
    forced run with no environment so the guard is exercised without writing a file anywhere.

    Arguments:
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If the script does not exit with the documented code.
    """
    module_path = Path(gen_secrets.__file__)

    with (
        patch.object(sys, "argv", ["gen_secrets", "--force"]),
        pytest.raises(SystemExit) as raised,
    ):
        runpy.run_path(str(module_path), run_name="__main__")

    capsys.readouterr()

    assert raised.value.code == gen_secrets.EXIT_FORCE_WITHOUT_ENVIRONMENT


@pytest.mark.unit
@pytest.mark.parametrize(
    ("values", "current"),
    [
        ({"TRAEFIK_DASHBOARD_PASSWORD": "secret"}, "no-colon-here"),
        ({}, "admin:$2b$12$abcdefghijklmnopqrstuv"),
        ({"TRAEFIK_DASHBOARD_PASSWORD": "secret"}, "admin:not-a-bcrypt-digest"),
    ],
)
def test_an_unreadable_dashboard_entry_is_treated_as_absent(
    values: dict[str, str],
    current: str,
) -> None:
    """Rebuild an entry that cannot be checked.

    Confirms a malformed entry, an absent password, and a digest bcrypt refuses to parse are each
    reported as not matching, so the value is rebuilt rather than raising out of the generator.

    Arguments:
        values: Variables resolved so far.
        current: The entry already written to the file.

    Returns:
        None.

    Raises:
        AssertionError: If an unreadable entry is treated as valid.
    """
    assert gen_secrets.verify_dashboard_auth(values, current) is False


@pytest.mark.unit
def test_a_composed_value_missing_its_components_is_left_alone() -> None:
    """Skip a value this group cannot build.

    Confirms a composed value whose inputs are absent is passed over rather than raising, so a
    partial manifest cannot abort the whole run.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the value is invented from nothing.
    """
    shared: dict[str, str] = {}
    gen_secrets.share_composed(shared, {})

    assert "TRAEFIK_DASHBOARD_AUTH" not in shared


@pytest.mark.unit
def test_a_password_too_long_to_hash_is_refused() -> None:
    """Refuse a password the hash cannot carry.

    Confirms an over-long password ends in the module's own refusal rather than an unhandled error
    from the hashing library, so the run reports a documented exit code.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the over-long password is not refused by name.
    """
    values = {"TRAEFIK_DASHBOARD_PASSWORD": "x" * (gen_secrets.BCRYPT_MAXIMUM_BYTES + 1)}

    with pytest.raises(gen_secrets.RefusalError) as raised:
        gen_secrets.compose_dashboard_auth(values)

    assert "TRAEFIK_DASHBOARD_PASSWORD" in str(raised.value)


@pytest.mark.unit
@pytest.mark.parametrize("user", ["not-admin", ""])
def test_an_entry_naming_another_user_is_rejected(user: str) -> None:
    """Rebuild an entry that names someone else.

    Confirms only the registered user is accepted, so a hand-edited entry granting another account
    is replaced rather than preserved. The digest is correct for the password, so the user is the
    only thing that can decide the outcome.

    Arguments:
        user: Account name carried by the entry.

    Returns:
        None.

    Raises:
        AssertionError: If an entry for another user is accepted.
    """
    sample = "correct-horse"
    salt = bcrypt.gensalt(rounds=gen_secrets.BCRYPT_ROUNDS)
    digest = bcrypt.hashpw(sample.encode(), salt).decode()
    values = {"TRAEFIK_DASHBOARD_PASSWORD": sample}

    assert gen_secrets.verify_dashboard_auth(values, f"{user}:{digest}") is False
    assert gen_secrets.verify_dashboard_auth(values, f"admin:{digest}") is True


@pytest.mark.unit
def test_an_entry_hashed_at_a_weaker_cost_is_rejected() -> None:
    """Rebuild an entry hashed below the required cost.

    Confirms a digest computed at a lower bcrypt cost is not kept, because the registry fixes the
    cost and a weakened entry would otherwise survive every future run.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a weaker digest is accepted.
    """
    sample = "correct-horse"
    other = bcrypt.hashpw(
        sample.encode(), bcrypt.gensalt(rounds=gen_secrets.BCRYPT_ROUNDS + 1)
    ).decode()
    values = {"TRAEFIK_DASHBOARD_PASSWORD": sample}

    assert gen_secrets.verify_dashboard_auth(values, f"admin:{other}") is False


@pytest.mark.unit
def test_siblings_holding_different_dashboard_entries_are_refused() -> None:
    """Refuse to choose between two valid entries.

    Confirms a group whose members carry different correct hashes is refused rather than silently
    reconciled, which is how the generator already treats a disagreeing shared credential.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the divergence is accepted.
    """
    sample = "shared-password"
    shared = {"TRAEFIK_DASHBOARD_PASSWORD": sample}

    def entry_for(value: str) -> str:
        salt = bcrypt.gensalt(rounds=gen_secrets.BCRYPT_ROUNDS)

        return f"admin:{bcrypt.hashpw(value.encode(), salt).decode()}"

    entries = {
        name: {"TRAEFIK_DASHBOARD_AUTH": entry_for(sample)} for name in ("testing", "testing-host")
    }

    with pytest.raises(gen_secrets.RefusalError):
        gen_secrets.share_composed(shared, entries)


@pytest.mark.unit
def test_a_surviving_sibling_entry_is_adopted_rather_than_rebuilt() -> None:
    """Keep the entry the remaining file already holds.

    Confirms a group regenerating one deleted sibling adopts the survivor's hash, so both files end
    up carrying the identical credential.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the surviving entry is replaced.
    """
    sample = "shared-password"
    digest = bcrypt.hashpw(sample.encode(), bcrypt.gensalt(rounds=gen_secrets.BCRYPT_ROUNDS))
    entry = f"admin:{digest.decode()}"
    shared = {"TRAEFIK_DASHBOARD_PASSWORD": sample}
    gen_secrets.share_composed(shared, {"testing": {"TRAEFIK_DASHBOARD_AUTH": entry}})

    assert shared["TRAEFIK_DASHBOARD_AUTH"] == entry


@pytest.mark.unit
def test_an_entry_already_shared_is_left_untouched() -> None:
    """Leave a correct shared entry alone.

    Confirms a value already held in the shared set and still matching its password is kept, so a
    rerun neither rebuilds it nor reports the group as changed.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the shared entry is replaced.
    """
    sample = "shared-password"
    salt = bcrypt.gensalt(rounds=gen_secrets.BCRYPT_ROUNDS)
    entry = f"admin:{bcrypt.hashpw(sample.encode(), salt).decode()}"
    shared = {"TRAEFIK_DASHBOARD_PASSWORD": sample, "TRAEFIK_DASHBOARD_AUTH": entry}
    gen_secrets.share_composed(shared, {})

    assert shared["TRAEFIK_DASHBOARD_AUTH"] == entry


@pytest.mark.unit
def test_a_digest_the_library_cannot_parse_is_treated_as_absent() -> None:
    """Rebuild an entry the hashing library refuses to read.

    Confirms a digest carrying the expected prefix but a malformed body is reported as not
    matching, rather than raising out of the generator from inside the library.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the malformed digest is not handled.
    """
    entry = f"admin:$2b${gen_secrets.BCRYPT_ROUNDS:02d}$short"

    assert (
        gen_secrets.verify_dashboard_auth({"TRAEFIK_DASHBOARD_PASSWORD": "secret"}, entry) is False
    )
