"""Unit tests for the declared dependency baseline.

Covers the distributions the platform depends on at runtime and in development, so a dependency
dropped from the manifest or missing from the environment fails here rather than at the first
import in a container.
"""

import tomllib
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import cast

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST_PATH = REPOSITORY_ROOT / "pyproject.toml"

RUNTIME_DISTRIBUTIONS = (
    "asgiref",
    "boto3",
    "celery",
    "channels",
    "channels-redis",
    "django",
    "django-celery-beat",
    "django-environ",
    "django-health-check",
    "django-prometheus",
    "django-storages",
    "djangorestframework",
    "djangorestframework-simplejwt",
    "drf-spectacular",
    "drf-spectacular-sidecar",
    "psycopg",
    "redis",
    "uvicorn",
)

DEVELOPMENT_DISTRIBUTIONS = (
    "daphne",
    "pytest",
    "pytest-asyncio",
    "pytest-cov",
    "pytest-django",
    "pytest-xdist",
)

RUNTIME_MODULES = (
    "asgiref",
    "boto3",
    "celery",
    "channels",
    "channels_redis",
    "django_celery_beat",
    "django_prometheus",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "environ",
    "health_check",
    "psycopg",
    "psycopg_pool",
    "redis",
    "rest_framework",
    "rest_framework_simplejwt",
    "storages",
    "uvicorn",
    "websockets",
)


def _distribution_name(requirement: str) -> str:
    """Reduce one requirement string to its distribution name.

    Strips any environment marker, extras, and version specifier, then normalises the separator
    characters the way the packaging specification does, so a declaration and a lookup compare
    equal however either was written.

    Arguments:
        requirement: A requirement string as it appears in the project manifest.

    Returns:
        The normalised distribution name.

    Raises:
        None.
    """
    name = requirement.split(";", maxsplit=1)[0].split("[", maxsplit=1)[0]
    for separator in (">", "<", "=", "!", "~"):
        name = name.split(separator, maxsplit=1)[0]

    return name.strip().replace("_", "-").replace(".", "-").lower()


def _declared_requirements(table: str) -> set[str]:
    """Read one requirement list from the project manifest.

    Parses the manifest and reduces each requirement string to its distribution name, so a
    comparison ignores the version specifier and any extras the requirement carries.

    Arguments:
        table: Either ``project`` for the runtime dependencies or ``dev`` for the development
            dependency group.

    Returns:
        The set of distribution names declared in that list, normalised.

    Raises:
        KeyError: If the manifest carries no list under the requested name.
    """
    manifest = tomllib.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    requirements = (
        manifest["project"]["dependencies"]
        if table == "project"
        else manifest["dependency-groups"][table]
    )

    return {_distribution_name(requirement) for requirement in requirements}


@pytest.mark.unit
@pytest.mark.parametrize("distribution", RUNTIME_DISTRIBUTIONS)
def test_runtime_distribution_is_declared_and_installed(distribution: str) -> None:
    """Carry every runtime distribution the platform needs.

    Confirms each distribution appears in the manifest's runtime list and resolves in the
    environment, because a runtime dependency left in the development group is absent from the
    application image.

    Arguments:
        distribution: The distribution name under test.

    Returns:
        None.

    Raises:
        AssertionError: If the distribution is absent from the runtime list.
        PackageNotFoundError: If the distribution is not installed.
    """
    assert distribution in _declared_requirements("project")
    assert version(distribution)


@pytest.mark.unit
@pytest.mark.parametrize("distribution", DEVELOPMENT_DISTRIBUTIONS)
def test_development_distribution_is_declared_and_installed(distribution: str) -> None:
    """Carry the test tooling the suite needs.

    Confirms each development distribution appears in the development group and resolves in the
    environment, covering the async support the WebSocket consumer tests depend on.

    Arguments:
        distribution: The distribution name under test.

    Returns:
        None.

    Raises:
        AssertionError: If the distribution is absent from the development group.
        PackageNotFoundError: If the distribution is not installed.
    """
    assert distribution in _declared_requirements("dev")
    assert version(distribution)


@pytest.mark.unit
@pytest.mark.parametrize("module", RUNTIME_MODULES)
def test_runtime_module_imports(module: str) -> None:
    """Import every runtime package under the project interpreter.

    Confirms each installed distribution imports on this Python version, which is the check that
    catches a dependency whose published artifact predates support for this interpreter.

    Arguments:
        module: The top-level module name under test.

    Returns:
        None.

    Raises:
        ImportError: If the module cannot be imported.
    """
    assert import_module(module) is not None


@pytest.mark.unit
def test_simplejwt_is_exactly_pinned_without_the_crypto_extra() -> None:
    """Use the checked SimpleJWT release without asymmetric-signing dependencies.

    Reads the runtime manifest directly and proves the selected release is exact while no crypto
    extra is requested for the HS256-only configuration.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the dependency can drift or installs the unused crypto extra.
    """
    manifest = tomllib.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    requirements = cast("list[str]", manifest["project"]["dependencies"])
    simplejwt = [
        requirement
        for requirement in requirements
        if _distribution_name(requirement) == "djangorestframework-simplejwt"
    ]

    assert simplejwt == ["djangorestframework-simplejwt==5.5.1"]
    assert "[crypto]" not in simplejwt[0]


@pytest.mark.unit
def test_the_project_declares_no_requirements_file() -> None:
    """Keep dependency declaration in the manifest alone.

    Confirms no requirements file has been reintroduced beside the manifest, because a second
    declaration drifts from the lockfile and the image would then install a different set.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a requirements file exists at the repository root.
    """
    assert not list(REPOSITORY_ROOT.glob("requirements*.txt"))


@pytest.mark.unit
def test_the_s3_client_is_not_a_development_dependency() -> None:
    """Keep the S3 client in the runtime group.

    Confirms the client the application uploads through is a runtime dependency rather than a
    development one, since the seeding step that introduced it early runs from the host.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the client is still declared in the development group.
    """
    assert "boto3" not in _declared_requirements("dev")


@pytest.mark.unit
def test_the_asgi_server_carries_websocket_support() -> None:
    """Serve WebSocket upgrades from the ASGI server.

    Confirms the WebSocket protocol implementation the ASGI server needs is installed, because
    without it the server accepts no upgrade and Channels fails at runtime rather than at startup.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        PackageNotFoundError: If the protocol implementation is not installed.
    """
    assert version("websockets")


@pytest.mark.unit
def test_consumer_test_support_is_importable() -> None:
    """Reach the consumer testing helpers.

    Confirms the Channels testing package imports, which it does not on Channels alone because its
    live-server helper pulls in the alternative ASGI server even when that server is never run.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        ImportError: If the testing package or its dependency is missing.
    """
    assert import_module("channels.testing").WebsocketCommunicator is not None


@pytest.mark.unit
def test_the_broker_health_backend_is_importable() -> None:
    """Probe the broker from the health endpoint.

    Confirms the broker health backend imports, since the health-check distribution gates it behind
    an extra and the aggregate endpoint must report the broker individually.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        ImportError: If the backend or its client library is missing.
    """
    assert import_module("health_check.contrib.rabbitmq") is not None


@pytest.mark.unit
def test_a_missing_distribution_reports_itself() -> None:
    """Fail loudly on an absent distribution.

    Confirms the metadata lookup these tests rely on raises for a distribution that is not
    installed, so a passing assertion above cannot be an artefact of a silent lookup.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the lookup does not raise for an absent distribution.
    """
    with pytest.raises(PackageNotFoundError):
        version("localforge-not-a-real-distribution")


@pytest.mark.unit
@pytest.mark.parametrize("compose_file", ["compose.development.yaml", "compose.testing.yaml"])
def test_the_built_image_tags_match_the_project_version(compose_file: str) -> None:
    """Tag the built images with the version the project declares.

    Confirms every image this repository builds carries the manifest's version, because three
    copies of a version string with nothing holding them together drift at the first bump.

    Arguments:
        compose_file: Compose file to inspect.

    Returns:
        None.

    Raises:
        AssertionError: If a built image carries another version.
    """
    version = tomllib.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["project"]["version"]
    source = (REPOSITORY_ROOT / compose_file).read_text(encoding="utf-8")

    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("image: localforge/django"):
            assert stripped.endswith(f":{version}"), stripped
