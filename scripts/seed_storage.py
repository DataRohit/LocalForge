"""Create the media bucket in the object store.

Applies the configured access key against the S3 gateway and creates the bucket the application
uploads into, treating an existing bucket as success so the step can run on every start.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Protocol, cast

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


class BucketClient(Protocol):
    """The two bucket operations this script performs.

    Describes the client surface the seeding step needs, so the functions below depend on the
    operations they call rather than on the whole generated client type. Inherits from Protocol.

    Attributes:
        None. The protocol declares behaviour only.

    Members:
        create_bucket: Create a bucket.
        head_bucket: Read a bucket back.
    """

    def create_bucket(self, **kwargs: str) -> object:
        """Create a bucket.

        Mirrors the operation the SDK exposes, whose parameters are named in the service's own
        casing and are therefore passed by keyword.

        Arguments:
            kwargs: Operation parameters, of which this script passes only the bucket name.

        Returns:
            The service response.
        """
        ...

    def head_bucket(self, **kwargs: str) -> object:
        """Read a bucket back.

        Mirrors the operation the SDK exposes, whose parameters are named in the service's own
        casing and are therefore passed by keyword.

        Arguments:
            kwargs: Operation parameters, of which this script passes only the bucket name.

        Returns:
            The service response.
        """
        ...


EXIT_OK: Final = 0
EXIT_UNREACHABLE: Final = 1
EXIT_REJECTED: Final = 2

REPOSITORY_ROOT: Final = Path(__file__).resolve().parent.parent
ENVIRONMENT_FILES: Final = {
    "development": ".env.development",
    "testing": ".env.testing.host",
}

SETTING_NAMES: Final = (
    "S3_ENDPOINT_URL",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "S3_REGION_NAME",
    "S3_BUCKET_NAME",
)

ALREADY_OWNED: Final = frozenset({"BucketAlreadyOwnedByYou", "BucketAlreadyExists"})
REJECTED: Final = frozenset(
    {
        "AccessDenied",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "InvalidSecurityToken",
    }
)
CONNECT_TIMEOUT_SECONDS: Final = 5
READ_TIMEOUT_SECONDS: Final = 15


class SeedError(Exception):
    """A seeding attempt that ended in a known exit code.

    Raised when the gateway cannot be reached or refuses the configured credentials, so the caller
    can map the failure onto the documented exit code. Inherits from Exception.

    Attributes:
        code: Exit code the contract assigns to this failure.

    Members:
        __init__: Record the failure and the code it maps to.
    """

    def __init__(self, message: str, code: int) -> None:
        """Record the failure and the code it maps to.

        Stores the exit code alongside the message so the entry point reports the outcome without
        re-inspecting the underlying client error.

        Arguments:
            message: Human-readable description of the failure.
            code: Exit code the contract assigns to this failure.

        Returns:
            None.
        """
        super().__init__(message)
        self.code = code


def build_client(endpoint: str, access_key: str, secret_key: str, region: str) -> BucketClient:
    """Construct a client aimed at the local gateway.

    Disables retries and shortens the timeouts, so an unreachable gateway fails in seconds rather
    than blocking a container start behind the default retry schedule.

    Arguments:
        endpoint: Base URL of the S3 gateway.
        access_key: Access key identifying the configured identity.
        secret_key: Secret paired with the access key.
        region: Region string the SDK requires but the gateway ignores.

    Returns:
        A client bound to the gateway.
    """
    return cast(
        "BucketClient",
        boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(
                retries={"max_attempts": 1, "mode": "standard"},
                connect_timeout=CONNECT_TIMEOUT_SECONDS,
                read_timeout=READ_TIMEOUT_SECONDS,
                s3={"addressing_style": "path"},
            ),
        ),
    )


def ensure_bucket(client: BucketClient, bucket: str) -> bool:
    """Create the bucket unless it is already there.

    Treats an existing bucket as success, because the step runs on every start and a second run
    must not turn a healthy stack into a failed one.

    Arguments:
        client: Client bound to the gateway.
        bucket: Name of the bucket to create.

    Returns:
        True when this call created the bucket, False when it already existed.

    Raises:
        SeedError: If the gateway is unreachable or rejects the credentials.
    """
    try:
        client.create_bucket(Bucket=bucket)
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code", ""))
        if code in ALREADY_OWNED:
            return False
        if code in REJECTED:
            message = f"the gateway rejected the configured credentials: {code}"
            raise SeedError(message, EXIT_REJECTED) from error
        message = f"the gateway refused to create {bucket}: {code}"
        raise SeedError(message, EXIT_UNREACHABLE) from error
    except BotoCoreError as error:
        message = f"the gateway is unreachable: {type(error).__name__}"
        raise SeedError(message, EXIT_UNREACHABLE) from error

    return True


def confirm_bucket(client: BucketClient, bucket: str) -> None:
    """Prove the bucket is usable by the configured identity.

    Reads the bucket back after creating it, so a gateway that accepted the call but stored nothing
    is reported rather than being counted as success.

    Arguments:
        client: Client bound to the gateway.
        bucket: Name of the bucket to confirm.

    Returns:
        None.

    Raises:
        SeedError: If the bucket cannot be read back.
    """
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code", ""))
        if code in REJECTED:
            message = f"the gateway rejected the configured credentials: {code}"
            raise SeedError(message, EXIT_REJECTED) from error
        message = f"{bucket} was not readable after creation: {code}"
        raise SeedError(message, EXIT_UNREACHABLE) from error
    except BotoCoreError as error:
        message = f"the gateway is unreachable: {type(error).__name__}"
        raise SeedError(message, EXIT_UNREACHABLE) from error


def read_environment_file(environment: str) -> dict[str, str]:
    """Load the settings an environment was generated with.

    Reads the file the generator wrote for this environment, so the step is told which stack to
    seed rather than inheriting whatever a shell happens to export.

    Arguments:
        environment: Environment whose file should be read.

    Returns:
        The variables the file declares.

    Raises:
        SeedError: If the file has not been generated.
    """
    source = REPOSITORY_ROOT / ENVIRONMENT_FILES[environment]
    if not source.is_file():
        message = f"{source.name} has not been generated; run gen_secrets first"
        raise SeedError(message, EXIT_UNREACHABLE)

    values: dict[str, str] = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and not name.startswith("#"):
            values[name.strip()] = value.strip()

    return values


def read_settings(environ: dict[str, str]) -> tuple[str, str, str, str, str]:
    """Collect the gateway location and credentials.

    Reads the five values the registry assigns to object storage, so nothing is defaulted to a
    literal that would disagree with the generated environment file.

    Arguments:
        environ: Mapping to read the configuration from.

    Returns:
        The endpoint, access key, secret key, region, and bucket name.

    Raises:
        SeedError: If any required variable is missing or empty.
    """
    missing = [name for name in SETTING_NAMES if not environ.get(name)]
    if missing:
        message = f"missing configuration: {', '.join(missing)}"
        raise SeedError(message, EXIT_UNREACHABLE)

    endpoint, access_key, secret_key, region, bucket = (environ[name] for name in SETTING_NAMES)

    return endpoint, access_key, secret_key, region, bucket


def main(argv: Sequence[str] | None = None) -> int:
    """Seed the object store and report the outcome.

    Creates the media bucket in the named environment and prints what happened, mapping any
    failure onto the exit code the script contract assigns to it.

    Arguments:
        argv: Command-line arguments, or None to read them from the process.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(prog="seed_storage")
    parser.add_argument(
        "--environment",
        choices=sorted(ENVIRONMENT_FILES),
        default="development",
        help="environment whose stack should be seeded",
    )
    parser.add_argument(
        "--endpoint",
        default=None,
        help="gateway URL to use instead of the one the environment file carries",
    )
    arguments = parser.parse_args(argv)

    try:
        values = read_environment_file(arguments.environment)
        if arguments.endpoint:
            values["S3_ENDPOINT_URL"] = arguments.endpoint
        endpoint, access_key, secret_key, region, bucket = read_settings(values)
        client = build_client(endpoint, access_key, secret_key, region)
        created = ensure_bucket(client, bucket)
        confirm_bucket(client, bucket)
    except SeedError as error:
        print(f"seed_storage: {error}", file=sys.stderr)

        return error.code
    except ValueError as error:
        print(f"seed_storage: {error}", file=sys.stderr)

        return EXIT_UNREACHABLE

    print(
        f"seed_storage: {arguments.environment} {bucket} "
        f"{'created' if created else 'already present'}",
    )

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
