"""Unit tests for developer browser import generation.

Exercises the public export command against temporary development environment files so developers
receive complete browser bookmarks and login records without exposing credentials in command output.
"""

import csv
import runpy
import sys
from io import StringIO
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from scripts import export_developer_access as export

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def development_environment() -> str:
    """Build one complete development credential fixture.

    Supplies distinct known values for every browser-authenticated service so generated rows can be
    checked independently without relying on the secret generator implementation.

    Arguments:
        None.

    Returns:
        Dotenv text accepted by the export command.
    """
    return """\
PGADMIN_DEFAULT_EMAIL=dev@localforge.invalid
PGADMIN_DEFAULT_PASSWORD=pgadmin-password
RABBITMQ_DEFAULT_USER=localforge_broker
RABBITMQ_DEFAULT_PASS=rabbitmq-password
FLOWER_BASIC_AUTH=flower-user:flower-password
MP_UI_AUTH=mailpit-user:mailpit-password
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=grafana-password
TRAEFIK_DASHBOARD_PASSWORD=traefik-password
"""


def test_export_writes_complete_browser_import_files_without_printing_secrets(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Generate bookmarks and passwords from the development environment.

    Invokes the public command and requires browser-compatible files covering every host-published
    dashboard while ensuring command output and bookmarks never disclose any password.

    Arguments:
        tmp_path: Temporary repository root.
        capsys: Fixture capturing command output.

    Returns:
        None.

    Raises:
        AssertionError: If either import file is incomplete, malformed, or leaks a password.
    """
    (tmp_path / ".env.development").write_text(
        development_environment(),
        encoding="utf-8",
    )

    code = export.main(root=tmp_path)
    output = capsys.readouterr().out

    assert code == export.EXIT_OK
    assert "bookmarks.html" in output
    assert "passwords.csv" in output
    assert "traefik-password" not in output
    assert "grafana-password" not in output

    bookmarks = (tmp_path / "bookmarks.html").read_text(encoding="utf-8")
    for label, url in (
        ("Traefik Dashboard", "http://localhost:8081/dashboard/"),
        ("Django Admin", "http://localhost:8000/admin/"),
        ("Scheduler Admin", "http://localhost:8000/admin/django_celery_beat/"),
        ("OpenAPI Schema", "http://localhost:8000/api/schema/"),
        ("Swagger UI", "http://localhost:8000/api/schema/swagger-ui/"),
        ("ReDoc", "http://localhost:8000/api/schema/redoc/"),
        ("Readiness", "http://localhost:8000/health/"),
        ("pgAdmin", "http://localhost:5050/"),
        ("RabbitMQ Management", "http://localhost:15672/"),
        ("Flower", "http://localhost:5555/"),
        ("Mailpit", "http://localhost:8025/"),
        ("Grafana", "http://localhost:3000/"),
        ("Grafana Explore", "http://localhost:3000/explore"),
    ):
        assert f'HREF="{url}"' in bookmarks
        assert f">{label}</A>" in bookmarks
    assert "traefik-password" not in bookmarks
    assert "grafana-password" not in bookmarks

    password_rows = list(
        csv.DictReader(StringIO((tmp_path / "passwords.csv").read_text(encoding="utf-8")))
    )
    assert password_rows == [
        {
            "name": "LocalForge Traefik Dashboard",
            "url": "http://localhost:8081/dashboard/",
            "username": "admin",
            "password": "traefik-password",
        },
        {
            "name": "LocalForge pgAdmin",
            "url": "http://localhost:5050/",
            "username": "dev@localforge.invalid",
            "password": "pgadmin-password",
        },
        {
            "name": "LocalForge RabbitMQ",
            "url": "http://localhost:15672/",
            "username": "localforge_broker",
            "password": "rabbitmq-password",
        },
        {
            "name": "LocalForge Flower",
            "url": "http://localhost:5555/",
            "username": "flower-user",
            "password": "flower-password",
        },
        {
            "name": "LocalForge Mailpit",
            "url": "http://localhost:8025/",
            "username": "mailpit-user",
            "password": "mailpit-password",
        },
        {
            "name": "LocalForge Grafana",
            "url": "http://localhost:3000/",
            "username": "admin",
            "password": "grafana-password",
        },
    ]


def test_export_refuses_missing_environment_without_writing_outputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Refuse generation when setup has not created development credentials.

    Invokes the public command without an environment file and requires a safe named failure that
    leaves both import destinations absent.

    Arguments:
        tmp_path: Temporary repository root.
        capsys: Fixture capturing command output.

    Returns:
        None.

    Raises:
        AssertionError: If generation succeeds, writes a partial result, or hides the missing file.
    """
    code = export.main(root=tmp_path)
    output = capsys.readouterr().out

    assert code == export.EXIT_FAILED
    assert export.ENVIRONMENT_FILE in output
    assert not (tmp_path / export.BOOKMARKS_FILE).exists()
    assert not (tmp_path / export.PASSWORDS_FILE).exists()


def test_export_names_every_missing_or_placeholder_credential(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Refuse incomplete generated development configuration.

    Supplies one absent value and one unresolved manifest placeholder, then requires both variable
    names in the diagnostic without writing either output.

    Arguments:
        tmp_path: Temporary repository root.
        capsys: Fixture capturing command output.

    Returns:
        None.

    Raises:
        AssertionError: If incomplete credentials are accepted or reported ambiguously.
    """
    environment = (
        development_environment()
        .replace(
            "PGADMIN_DEFAULT_PASSWORD=pgadmin-password\n",
            "",
        )
        .replace(
            "GRAFANA_ADMIN_PASSWORD=grafana-password",
            "GRAFANA_ADMIN_PASSWORD=<GENERATED>",
        )
    )
    (tmp_path / export.ENVIRONMENT_FILE).write_text(environment, encoding="utf-8")

    code = export.main(root=tmp_path)
    output = capsys.readouterr().out

    assert code == export.EXIT_FAILED
    assert "GRAFANA_ADMIN_PASSWORD" in output
    assert "PGADMIN_DEFAULT_PASSWORD" in output
    assert "<GENERATED>" not in output
    assert not (tmp_path / export.BOOKMARKS_FILE).exists()
    assert not (tmp_path / export.PASSWORDS_FILE).exists()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FLOWER_BASIC_AUTH", "missing-separator"),
        ("FLOWER_BASIC_AUTH", ":missing-user"),
        ("MP_UI_AUTH", "missing-password:"),
    ],
)
def test_export_refuses_malformed_basic_authentication_pairs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    value: str,
) -> None:
    """Refuse dashboard credentials that cannot become browser login records.

    Replaces one generated pair with each incomplete form and requires a variable-named failure
    before either import file is written.

    Arguments:
        tmp_path: Temporary repository root.
        capsys: Fixture capturing command output.
        name: Basic-authentication environment variable under test.
        value: Malformed value replacing its complete fixture.

    Returns:
        None.

    Raises:
        AssertionError: If a malformed pair is accepted or its secret-shaped value is printed.
    """
    original = (
        "flower-user:flower-password"
        if name == "FLOWER_BASIC_AUTH"
        else "mailpit-user:mailpit-password"
    )
    environment = development_environment().replace(f"{name}={original}", f"{name}={value}")
    (tmp_path / export.ENVIRONMENT_FILE).write_text(environment, encoding="utf-8")

    code = export.main(root=tmp_path)
    output = capsys.readouterr().out

    assert code == export.EXIT_FAILED
    assert name in output
    assert value not in output
    assert not (tmp_path / export.BOOKMARKS_FILE).exists()
    assert not (tmp_path / export.PASSWORDS_FILE).exists()


def test_export_reports_output_write_failure_without_printing_passwords(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report a destination filesystem conflict safely.

    Blocks the password destination with a directory and requires a non-zero result that names the
    destination without printing credentials or creating the later bookmark file.

    Arguments:
        tmp_path: Temporary repository root.
        capsys: Fixture capturing command output.

    Returns:
        None.

    Raises:
        AssertionError: If a write error succeeds, leaks a password, or creates a partial bookmark.
    """
    (tmp_path / export.ENVIRONMENT_FILE).write_text(
        development_environment(),
        encoding="utf-8",
    )
    (tmp_path / export.PASSWORDS_FILE).mkdir()

    code = export.main(root=tmp_path)
    output = capsys.readouterr().out

    assert code == export.EXIT_FAILED
    assert export.PASSWORDS_FILE in output
    assert "grafana-password" not in output
    assert not (tmp_path / export.BOOKMARKS_FILE).exists()


def test_module_entry_point_exits_with_main_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Propagate the public command status from module execution.

    Executes the module as a script with its public function replaced, requiring the returned status
    to become the process exit code.

    Arguments:
        monkeypatch: Fixture removing the already-imported module before script execution.

    Returns:
        None.

    Raises:
        AssertionError: If direct module execution ignores the public command result.
    """
    monkeypatch.delitem(sys.modules, "scripts.export_developer_access")

    with (
        patch("pathlib.Path.read_text", side_effect=OSError("blocked")),
        pytest.raises(SystemExit) as failure,
    ):
        runpy.run_module("scripts.export_developer_access", run_name="__main__")

    assert failure.value.code == export.EXIT_FAILED
