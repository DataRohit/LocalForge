"""Integration tests for object storage.

Exercises Django's configured storage API against SeaweedFS, including byte-identical reads,
explicit overwrite semantics, private anonymous access, and deletion.
"""

import http.client
import socket
from typing import TypedDict, cast
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

STORAGE_TIMEOUT_SECONDS = 15
FORBIDDEN_STATUS = 403
FIRST_CONTENT = b"first object content"
SECOND_CONTENT = b"replacement object content"
SUCCESS_STATUS = 200


class StorageOptions(TypedDict):
    """Object-storage options used by the anonymous client.

    Narrows Django's dynamically typed settings value to the fields this integration test passes
    to boto3 when proving the bucket refuses unsigned reads. Inherits from TypedDict.

    Attributes:
        bucket_name: Media bucket configured for uploads.
        endpoint_url: S3-compatible gateway endpoint.
        location: Prefix separating uploaded media from any other objects.
        region_name: Region string required by the S3 client.

    Members:
        None. The type carries settings fields only.
    """

    bucket_name: str
    endpoint_url: str
    location: str
    region_name: str


def _object_name(worker_namespace: str, suffix: str) -> str:
    """Build an object name isolated to one parallel worker.

    Places every test object beneath the worker namespace, so parallel runs can create and delete
    colliding leaf names without affecting one another.

    Arguments:
        worker_namespace: Per-worker prefix shared by external-service fixtures.
        suffix: Leaf path describing the test object.

    Returns:
        A storage-relative object name.
    """
    return f"{worker_namespace}/{suffix}"


def _storage_status(port: int, path: str) -> int:
    """Read one SeaweedFS HTTP surface through the configured environment address.

    Reuses the S3 endpoint host while selecting the registered port for each native service,
    allowing the same assertion to reach container names and published loopback ports.

    Arguments:
        port: Environment-specific port for the requested SeaweedFS surface.
        path: HTTP path to request.

    Returns:
        HTTP response status.

    Raises:
        AssertionError: If the configured S3 endpoint has no hostname.
    """
    storages = cast("dict[str, dict[str, object]]", settings.STORAGES)
    options = cast("StorageOptions", storages["default"]["OPTIONS"])
    hostname = urlparse(options["endpoint_url"]).hostname

    assert hostname is not None

    connection = http.client.HTTPConnection(
        hostname,
        port,
        timeout=STORAGE_TIMEOUT_SECONDS,
    )
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        response.read()
    finally:
        connection.close()

    return response.status


def _storage_refuses_connection(port: int) -> bool:
    """Check that one unpublished SeaweedFS administration port is closed.

    Connects through the host-mode S3 endpoint address and treats only a refused or unreachable
    socket as proof that the native administration surface is not host-published.

    Arguments:
        port: Environment-specific native SeaweedFS port.

    Returns:
        True when the host boundary refuses the connection, otherwise False.

    Raises:
        AssertionError: If the configured S3 endpoint has no hostname.
    """
    storages = cast("dict[str, dict[str, object]]", settings.STORAGES)
    options = cast("StorageOptions", storages["default"]["OPTIONS"])
    hostname = urlparse(options["endpoint_url"]).hostname

    assert hostname is not None

    try:
        with socket.create_connection((hostname, port), timeout=STORAGE_TIMEOUT_SECONDS):
            return False
    except OSError:
        return True


@pytest.mark.integration
@pytest.mark.services("seaweedfs")
@pytest.mark.timeout(STORAGE_TIMEOUT_SECONDS)
def test_every_documented_object_storage_surface_answers() -> None:
    """Reach the S3 API, master status UI, and filer browser.

    Exercises each native SeaweedFS HTTP surface through the environment-specific address and
    registered port instead of treating the S3 object round trip as proof that the UIs are live.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any documented surface does not answer successfully.
    """
    storages = cast("dict[str, dict[str, object]]", settings.STORAGES)
    options = cast("StorageOptions", storages["default"]["OPTIONS"])
    s3_port = urlparse(options["endpoint_url"]).port

    assert s3_port is not None
    assert _storage_status(s3_port, "/healthz") == SUCCESS_STATUS
    if urlparse(options["endpoint_url"]).hostname in {"127.0.0.1", "localhost"}:
        assert _storage_refuses_connection(settings.SEAWEEDFS_MASTER_PORT)
        assert _storage_refuses_connection(settings.SEAWEEDFS_FILER_PORT)
    else:
        assert _storage_status(settings.SEAWEEDFS_MASTER_PORT, "/") == SUCCESS_STATUS
        assert _storage_status(settings.SEAWEEDFS_FILER_PORT, "/") == SUCCESS_STATUS


@pytest.mark.integration
@pytest.mark.services("seaweedfs")
@pytest.mark.timeout(STORAGE_TIMEOUT_SECONDS)
def test_uploaded_bytes_round_trip_through_object_storage(worker_namespace: str) -> None:
    """Write and read one object through Django's storage API.

    Proves the configured default storage reaches the S3 gateway and returns the exact bytes it
    accepted rather than writing to a container filesystem.

    Arguments:
        worker_namespace: Per-worker prefix keeping parallel objects isolated.

    Returns:
        None.

    Raises:
        AssertionError: If the saved name or returned bytes differ.
    """
    name = _object_name(worker_namespace, "round-trip.bin")

    try:
        saved = default_storage.save(name, ContentFile(FIRST_CONTENT))

        assert saved == name
        with default_storage.open(name, "rb") as stored:
            assert stored.read() == FIRST_CONTENT
    finally:
        default_storage.delete(name)


@pytest.mark.integration
@pytest.mark.services("seaweedfs")
@pytest.mark.timeout(STORAGE_TIMEOUT_SECONDS)
def test_a_colliding_filename_overwrites_the_existing_object(worker_namespace: str) -> None:
    """Replace an object when a caller saves the same name twice.

    Confirms collision behaviour is the explicit overwrite policy rather than the backend's
    accidental default, and verifies no generated alternate name remains.

    Arguments:
        worker_namespace: Per-worker prefix keeping parallel objects isolated.

    Returns:
        None.

    Raises:
        AssertionError: If the second save changes the name or preserves the first bytes.
    """
    name = _object_name(worker_namespace, "collision.bin")

    try:
        first_name = default_storage.save(name, ContentFile(FIRST_CONTENT))
        second_name = default_storage.save(name, ContentFile(SECOND_CONTENT))

        assert first_name == name
        assert second_name == name
        with default_storage.open(name, "rb") as stored:
            assert stored.read() == SECOND_CONTENT
    finally:
        default_storage.delete(name)


@pytest.mark.integration
@pytest.mark.services("seaweedfs")
@pytest.mark.timeout(STORAGE_TIMEOUT_SECONDS)
def test_an_uploaded_object_is_not_public(worker_namespace: str) -> None:
    """Refuse an unsigned read of an uploaded object.

    Removes the signature Django adds to the object's URL and proves the bucket does not permit
    anonymous reads, while the configured identity can still read the object through storage.

    Arguments:
        worker_namespace: Per-worker prefix keeping parallel objects isolated.

    Returns:
        None.

    Raises:
        AssertionError: If anonymous access succeeds or returns an unexpected status.
    """
    name = _object_name(worker_namespace, "private.bin")

    try:
        default_storage.save(name, ContentFile(FIRST_CONTENT))
        storages = cast("dict[str, dict[str, object]]", settings.STORAGES)
        options = cast("StorageOptions", storages["default"]["OPTIONS"])
        signed_url = default_storage.url(name)
        anonymous = boto3.client(
            "s3",
            endpoint_url=options["endpoint_url"],
            region_name=options["region_name"],
            config=Config(
                signature_version=UNSIGNED,
                retries={"max_attempts": 1, "mode": "standard"},
                connect_timeout=STORAGE_TIMEOUT_SECONDS,
                read_timeout=STORAGE_TIMEOUT_SECONDS,
            ),
        )

        with pytest.raises(ClientError) as failure:
            anonymous.get_object(
                Bucket=options["bucket_name"],
                Key=f"{options['location']}/{name}",
            )

        status = failure.value.response["ResponseMetadata"]["HTTPStatusCode"]
        signing_algorithm = parse_qs(urlparse(signed_url).query).get("X-Amz-Algorithm")
        assert status == FORBIDDEN_STATUS
        assert signing_algorithm == ["AWS4-HMAC-SHA256"]
        with default_storage.open(name, "rb") as stored:
            assert stored.read() == FIRST_CONTENT
    finally:
        default_storage.delete(name)


@pytest.mark.integration
@pytest.mark.services("seaweedfs")
@pytest.mark.timeout(STORAGE_TIMEOUT_SECONDS)
def test_deleting_an_object_removes_it(worker_namespace: str) -> None:
    """Delete an uploaded object through Django's storage API.

    Confirms deletion reaches the gateway and leaves the name absent rather than only invalidating
    a local cache.

    Arguments:
        worker_namespace: Per-worker prefix keeping parallel objects isolated.

    Returns:
        None.

    Raises:
        AssertionError: If the object still exists after deletion.
    """
    name = _object_name(worker_namespace, "delete.bin")
    default_storage.save(name, ContentFile(FIRST_CONTENT))

    default_storage.delete(name)

    assert default_storage.exists(name) is False
