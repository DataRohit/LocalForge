"""Unit tests for the object-storage seeding script.

Grades the script against the contract in the conventions document, so the exit code an operator
sees distinguishes an unreachable gateway from one that refused the configured credentials.
"""

from __future__ import annotations

import runpy
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from scripts import seed_storage

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

SETTINGS = {
    "S3_ENDPOINT_URL": "http://seaweedfs-sw9cr:8333",
    "S3_ACCESS_KEY_ID": "accesskey",
    "S3_SECRET_ACCESS_KEY": "secretkey",
    "S3_REGION_NAME": "us-east-1",
    "S3_BUCKET_NAME": "localforge-media",
}


def client_error(code: str) -> ClientError:
    """Build a client error carrying one service code.

    Produces the error shape the SDK raises, so the tests exercise the same branch the script takes
    against a live gateway.

    Arguments:
        code: Service error code the gateway would return.

    Returns:
        The constructed error.
    """
    return ClientError({"Error": {"Code": code, "Message": code}}, "CreateBucket")


class FakeClient:
    """A gateway that fails on demand.

    Stands in for the SDK client so each documented failure can be provoked without a running
    gateway. Inherits from object.

    Attributes:
        create_error: Error to raise from create_bucket, or None to succeed.
        head_error: Error to raise from head_bucket, or None to succeed.
        calls: Names of the operations that were invoked, in order.

    Members:
        __init__: Record which operations should fail.
        create_bucket: Record bucket creation and fail if configured.
        head_bucket: Record bucket read-back and fail if configured.
    """

    def __init__(
        self,
        create_error: Exception | None = None,
        head_error: Exception | None = None,
    ) -> None:
        """Record which operations should fail.

        Stores the errors so a test can make creation succeed while the read-back fails, which is
        the case that separates a stored bucket from an accepted call.

        Arguments:
            create_error: Error to raise from create_bucket, or None to succeed.
            head_error: Error to raise from head_bucket, or None to succeed.

        Returns:
            None.
        """
        self.create_error = create_error
        self.head_error = head_error
        self.calls: list[str] = []

    def create_bucket(self, **_: object) -> None:
        """Record the call and fail if configured to.

        Mimics the SDK operation by recording the call first, then raising whichever error the test
        supplied for the creation path.

        Arguments:
            _: Keyword arguments the SDK accepts and this stand-in ignores.

        Returns:
            None.

        Raises:
            Exception: Whatever error the test configured.
        """
        self.calls.append("create_bucket")
        if self.create_error is not None:
            raise self.create_error

    def head_bucket(self, **_: object) -> None:
        """Record the call and fail if configured to.

        Mimics the SDK operation by recording the call first, then raising whichever error the test
        supplied for the read-back path.

        Arguments:
            _: Keyword arguments the SDK accepts and this stand-in ignores.

        Returns:
            None.

        Raises:
            Exception: Whatever error the test configured.
        """
        self.calls.append("head_bucket")
        if self.head_error is not None:
            raise self.head_error


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the environment file resolve to a complete configuration.

    Replaces the file read, so a test that cares about one failure does not also depend on which
    environment files this checkout happens to have generated.

    Arguments:
        monkeypatch: Fixture used to replace the file read.

    Returns:
        None.
    """
    monkeypatch.setattr(seed_storage, "read_environment_file", lambda _: dict(SETTINGS))


@pytest.mark.unit
def test_a_complete_configuration_is_read_in_the_documented_order() -> None:
    """Read every value the registry assigns to storage.

    Confirms the endpoint, credentials, region, and bucket are returned in the order the caller
    unpacks them, because transposing two of them would authenticate against the wrong gateway.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any value is misplaced.
    """
    assert seed_storage.read_settings(dict(SETTINGS)) == (
        SETTINGS["S3_ENDPOINT_URL"],
        SETTINGS["S3_ACCESS_KEY_ID"],
        SETTINGS["S3_SECRET_ACCESS_KEY"],
        SETTINGS["S3_REGION_NAME"],
        SETTINGS["S3_BUCKET_NAME"],
    )


@pytest.mark.unit
def test_process_environment_can_seed_from_inside_the_application_container(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Read storage configuration from the container process environment.

    Supplies every registry value without an environment file and verifies startup seeding reaches
    the normal client path with the configured internal endpoint.

    Arguments:
        monkeypatch: Fixture setting process variables and replacing the client builder.
        capsys: Fixture capturing the success summary.

    Returns:
        None.

    Raises:
        AssertionError: If process settings are ignored or reordered.
    """
    seen: list[tuple[str, str, str, str]] = []
    for name, value in SETTINGS.items():
        monkeypatch.setenv(name, value)

    def record(endpoint: str, access_key: str, secret_key: str, region: str) -> FakeClient:
        """Record process-derived client settings.

        Captures the four connection values and returns a successful gateway stand-in.
        Avoids network access while preserving the production call boundary.

        Arguments:
            endpoint: S3 endpoint selected by the command.
            access_key: Configured access key.
            secret_key: Configured secret key.
            region: Configured signing region.

        Returns:
            Successful fake storage client.

        Raises:
            None.
        """
        seen.append((endpoint, access_key, secret_key, region))
        return FakeClient()

    monkeypatch.setattr(seed_storage, "build_client", record)

    assert seed_storage.main(["--process-environment"]) == seed_storage.EXIT_OK
    assert seen == [
        (
            SETTINGS["S3_ENDPOINT_URL"],
            SETTINGS["S3_ACCESS_KEY_ID"],
            SETTINGS["S3_SECRET_ACCESS_KEY"],
            SETTINGS["S3_REGION_NAME"],
        )
    ]
    assert "created" in capsys.readouterr().out


@pytest.mark.unit
@pytest.mark.parametrize("missing", sorted(SETTINGS))
def test_an_incomplete_configuration_names_what_is_missing(missing: str) -> None:
    """Say which value was absent.

    Confirms the failure names the missing variable rather than surfacing a connection error later,
    so an operator fixes the environment file instead of investigating the gateway.

    Arguments:
        missing: Variable to remove from the configuration.

    Returns:
        None.

    Raises:
        AssertionError: If the variable is not named, or the exit code is wrong.
    """
    environ = dict(SETTINGS)
    environ[missing] = ""

    with pytest.raises(seed_storage.SeedError) as raised:
        seed_storage.read_settings(environ)

    assert missing in str(raised.value)
    assert raised.value.code == seed_storage.EXIT_UNREACHABLE


@pytest.mark.unit
def test_the_client_is_aimed_at_the_configured_gateway() -> None:
    """Point the client at the local gateway.

    Confirms the client is built against the configured endpoint using path addressing, because the
    virtual-host style the SDK prefers resolves a bucket name that does not exist locally.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the client is not bound to the configured endpoint.
    """
    client = cast(
        "S3Client",
        seed_storage.build_client(
            SETTINGS["S3_ENDPOINT_URL"],
            SETTINGS["S3_ACCESS_KEY_ID"],
            SETTINGS["S3_SECRET_ACCESS_KEY"],
            SETTINGS["S3_REGION_NAME"],
        ),
    )

    assert client.meta.endpoint_url == SETTINGS["S3_ENDPOINT_URL"]


@pytest.mark.unit
def test_a_missing_bucket_is_created() -> None:
    """Create the bucket the application uploads into.

    Confirms a first run reports the bucket as created, which is the only run that changes the
    gateway.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If creation is not reported.
    """
    client = FakeClient()

    assert seed_storage.ensure_bucket(client, SETTINGS["S3_BUCKET_NAME"]) is True


@pytest.mark.unit
@pytest.mark.parametrize("code", sorted(seed_storage.ALREADY_OWNED))
def test_an_existing_bucket_is_success(code: str) -> None:
    """Let the step run on every start.

    Confirms a bucket that already exists is reported as present rather than raising, because the
    contract makes creating an existing bucket a success.

    Arguments:
        code: Service code a gateway returns for an existing bucket.

    Returns:
        None.

    Raises:
        AssertionError: If an existing bucket is treated as a failure.
    """
    client = FakeClient(create_error=client_error(code))

    assert seed_storage.ensure_bucket(client, SETTINGS["S3_BUCKET_NAME"]) is False


@pytest.mark.unit
@pytest.mark.parametrize("code", sorted(seed_storage.REJECTED))
def test_refused_credentials_exit_separately_from_an_absent_gateway(code: str) -> None:
    """Separate a wrong key from an unreachable host.

    Confirms a rejected credential exits two rather than one, because the two failures have
    different remedies and collapsing them sends an operator to the wrong file.

    Arguments:
        code: Service code a gateway returns for a refused credential.

    Returns:
        None.

    Raises:
        AssertionError: If the exit code is not the one assigned to refusal.
    """
    client = FakeClient(create_error=client_error(code))

    with pytest.raises(seed_storage.SeedError) as raised:
        seed_storage.ensure_bucket(client, SETTINGS["S3_BUCKET_NAME"])

    assert raised.value.code == seed_storage.EXIT_REJECTED


@pytest.mark.unit
def test_an_unexpected_service_code_is_reported_as_unreachable() -> None:
    """Fail closed on a code the script does not know.

    Confirms an unrecognised service code still ends in a documented exit rather than escaping as
    an unhandled error.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the failure is not reported as unreachable.
    """
    client = FakeClient(create_error=client_error("InternalError"))

    with pytest.raises(seed_storage.SeedError) as raised:
        seed_storage.ensure_bucket(client, SETTINGS["S3_BUCKET_NAME"])

    assert raised.value.code == seed_storage.EXIT_UNREACHABLE


@pytest.mark.unit
def test_a_transport_failure_during_creation_is_reported_as_unreachable() -> None:
    """Report a gateway that never answered.

    Confirms a transport failure is mapped onto the unreachable exit, which is the case a container
    hits when it starts before the gateway is listening.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the failure is not reported as unreachable.
    """
    failure = EndpointConnectionError(endpoint_url=SETTINGS["S3_ENDPOINT_URL"])
    client = FakeClient(create_error=failure)

    with pytest.raises(seed_storage.SeedError) as raised:
        seed_storage.ensure_bucket(client, SETTINGS["S3_BUCKET_NAME"])

    assert raised.value.code == seed_storage.EXIT_UNREACHABLE


@pytest.mark.unit
def test_a_created_bucket_is_read_back() -> None:
    """Prove the bucket exists rather than that a call succeeded.

    Confirms the read-back runs after creation, because a gateway that accepted the call without
    storing anything would otherwise be counted as success.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the bucket is not read back.
    """
    client = FakeClient()
    seed_storage.confirm_bucket(client, SETTINGS["S3_BUCKET_NAME"])

    assert client.calls == ["head_bucket"]


@pytest.mark.unit
@pytest.mark.parametrize("code", sorted(seed_storage.REJECTED))
def test_a_read_back_refused_for_credentials_exits_as_refused(code: str) -> None:
    """Keep the refusal code on the read-back path too.

    Confirms a credential refused during the read-back exits two, so the code does not depend on
    which of the two calls the gateway rejected.

    Arguments:
        code: Service code a gateway returns for a refused credential.

    Returns:
        None.

    Raises:
        AssertionError: If the exit code is not the one assigned to refusal.
    """
    client = FakeClient(head_error=client_error(code))

    with pytest.raises(seed_storage.SeedError) as raised:
        seed_storage.confirm_bucket(client, SETTINGS["S3_BUCKET_NAME"])

    assert raised.value.code == seed_storage.EXIT_REJECTED


@pytest.mark.unit
def test_a_bucket_that_does_not_read_back_is_a_failure() -> None:
    """Refuse to call an absent bucket success.

    Confirms a bucket missing immediately after creation is reported, which is the shape a gateway
    takes when it accepts writes it does not persist.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the missing bucket is not reported.
    """
    client = FakeClient(head_error=client_error("NoSuchBucket"))

    with pytest.raises(seed_storage.SeedError) as raised:
        seed_storage.confirm_bucket(client, SETTINGS["S3_BUCKET_NAME"])

    assert raised.value.code == seed_storage.EXIT_UNREACHABLE


@pytest.mark.unit
def test_a_transport_failure_during_the_read_back_is_reported_as_unreachable() -> None:
    """Report a gateway that stopped answering midway.

    Confirms a transport failure on the read-back is mapped onto the unreachable exit rather than
    escaping.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the failure is not reported as unreachable.
    """
    failure = EndpointConnectionError(endpoint_url=SETTINGS["S3_ENDPOINT_URL"])
    client = FakeClient(head_error=failure)

    with pytest.raises(seed_storage.SeedError) as raised:
        seed_storage.confirm_bucket(client, SETTINGS["S3_BUCKET_NAME"])

    assert raised.value.code == seed_storage.EXIT_UNREACHABLE


@pytest.mark.unit
@pytest.mark.usefixtures("configured")
@pytest.mark.parametrize(
    ("create_error", "expected"),
    [(None, "created"), (client_error("BucketAlreadyOwnedByYou"), "already present")],
)
def test_a_successful_run_reports_what_it_did(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    create_error: ClientError | None,
    expected: str,
) -> None:
    """Say whether this run changed anything.

    Confirms the summary distinguishes a first run from a repeat, so a log shows whether the bucket
    was created now or had been created earlier.

    Arguments:
        monkeypatch: Fixture used to replace the client factory.
        capsys: Fixture capturing the printed summary.
        create_error: Error the gateway raises on creation, or None.
        expected: Word the summary is expected to carry.

    Returns:
        None.

    Raises:
        AssertionError: If the run fails or misreports what it did.
    """
    monkeypatch.setattr(
        seed_storage,
        "build_client",
        lambda *_, **__: FakeClient(create_error=create_error),
    )

    assert seed_storage.main([]) == seed_storage.EXIT_OK
    assert expected in capsys.readouterr().out


@pytest.mark.unit
@pytest.mark.usefixtures("configured")
def test_a_failed_run_exits_with_the_documented_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report the failure on the error stream.

    Confirms a refusal exits two and writes to standard error, so a container start fails visibly
    rather than printing a success line.

    Arguments:
        monkeypatch: Fixture used to replace the client factory.
        capsys: Fixture capturing the printed failure.

    Returns:
        None.

    Raises:
        AssertionError: If the exit code or the stream is wrong.
    """
    monkeypatch.setattr(
        seed_storage,
        "build_client",
        lambda *_, **__: FakeClient(create_error=client_error("AccessDenied")),
    )

    assert seed_storage.main([]) == seed_storage.EXIT_REJECTED
    assert "rejected" in capsys.readouterr().err


@pytest.mark.unit
def test_the_script_guard_runs_the_helper(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the helper through its script guard.

    Executes the module under the name Python assigns to a directly executed script with nothing
    configured, which exercises the guard an ordinary import never reaches.

    Arguments:
        monkeypatch: Fixture used to clear the configuration and the arguments.
        capsys: Fixture capturing the printed failure.

    Returns:
        None.

    Raises:
        AssertionError: If the script does not exit with the documented code.
    """
    module_path = Path(seed_storage.__file__)
    absent = Path(tempfile.gettempdir()) / "localforge-absent-root"
    monkeypatch.setattr(
        sys,
        "argv",
        ["seed_storage", "--environment", "development", "--endpoint", str(absent)],
    )
    monkeypatch.chdir(absent.parent)

    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(module_path), run_name="__main__")

    capsys.readouterr()

    assert raised.value.code == seed_storage.EXIT_UNREACHABLE


@pytest.mark.unit
def test_an_unconfigured_run_fails_before_reaching_the_gateway(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Refuse to start without a configuration.

    Confirms a missing variable ends the run before a client is built, so the failure names the
    variable rather than a connection that was never attempted correctly.

    Arguments:
        monkeypatch: Fixture used to empty the configuration.
        capsys: Fixture capturing the printed failure.

    Returns:
        None.

    Raises:
        AssertionError: If the run does not fail, or fails for the wrong reason.
    """
    monkeypatch.setattr(seed_storage, "read_environment_file", lambda _: {})

    assert seed_storage.main([]) == seed_storage.EXIT_UNREACHABLE
    assert "missing configuration" in capsys.readouterr().err


@pytest.mark.unit
@pytest.mark.parametrize("environment", sorted(seed_storage.ENVIRONMENT_FILES))
def test_each_environment_reads_the_file_generated_for_it(environment: str) -> None:
    """Seed the stack that was asked for.

    Confirms each environment maps to the file the host can actually reach, so naming one
    environment cannot quietly seed the other.

    Arguments:
        environment: Environment whose file mapping is checked.

    Returns:
        None.

    Raises:
        AssertionError: If an environment maps to the wrong file.
    """
    expected = {"development": ".env.development", "testing": ".env.testing.host"}

    assert seed_storage.ENVIRONMENT_FILES[environment] == expected[environment]


@pytest.mark.unit
def test_an_ungenerated_environment_file_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Say the file is absent rather than that nothing is configured.

    Confirms a checkout that has not run the generator is told so, because the remedy is to
    generate secrets rather than to investigate the gateway.

    Arguments:
        monkeypatch: Fixture used to point the lookup at an empty directory.

    Returns:
        None.

    Raises:
        AssertionError: If the absent file is not named.
    """
    monkeypatch.setattr(seed_storage, "REPOSITORY_ROOT", Path(tempfile.gettempdir()) / "absent")

    with pytest.raises(seed_storage.SeedError) as raised:
        seed_storage.read_environment_file("development")

    assert "has not been generated" in str(raised.value)
    assert raised.value.code == seed_storage.EXIT_UNREACHABLE


@pytest.mark.unit
def test_an_environment_file_is_parsed_into_its_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read the values and ignore everything else.

    Confirms a generated file is parsed into its variables, so a blank line cannot be mistaken for
    a setting.

    Arguments:
        tmp_path: Directory standing in for the repository root.
        monkeypatch: Fixture used to point the lookup at that directory.

    Returns:
        None.

    Raises:
        AssertionError: If the values are not read as written.
    """
    (tmp_path / ".env.development").write_text("A=1\n\nB=two\n", encoding="utf-8")
    monkeypatch.setattr(seed_storage, "REPOSITORY_ROOT", tmp_path)

    assert seed_storage.read_environment_file("development") == {"A": "1", "B": "two"}


@pytest.mark.unit
@pytest.mark.usefixtures("configured")
def test_an_explicit_endpoint_overrides_the_environment_file(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Let the host reach a gateway the file names by container.

    Confirms the override replaces the endpoint, because the generated file names the container,
    which does not resolve from the host running the step.

    Arguments:
        monkeypatch: Fixture used to capture the endpoint the client was built with.
        capsys: Fixture capturing the printed summary.

    Returns:
        None.

    Raises:
        AssertionError: If the override is ignored.
    """
    seen: list[str] = []

    def record(endpoint: str, *_: str) -> FakeClient:
        """Record the endpoint used to build the client.

        Captures the override value before returning a successful fake client, so the test can
        assert on selection without contacting the gateway.

        Arguments:
            endpoint: Endpoint the entry point passed into the client builder.
            _: Other client-builder arguments the test does not inspect.

        Returns:
            A successful fake client.
        """
        seen.append(endpoint)

        return FakeClient()

    monkeypatch.setattr(seed_storage, "build_client", record)
    capsys.readouterr()

    assert seed_storage.main(["--endpoint", "http://127.0.0.1:8333"]) == seed_storage.EXIT_OK
    assert seen == ["http://127.0.0.1:8333"]
