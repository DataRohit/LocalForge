"""Developer browser import generation for LocalForge services.

Reads generated development credentials and writes portable bookmark and password import files for
every host-published dashboard without printing secret values.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from html import escape
from io import StringIO
from pathlib import Path

from scripts.gen_secrets import GENERATED_PLACEHOLDER, ManifestError, parse_env_text

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
ENVIRONMENT_FILE = ".env.development"
BOOKMARKS_FILE = "bookmarks.html"
PASSWORDS_FILE = "passwords.csv"
EXIT_OK = 0
EXIT_FAILED = 1
BASIC_AUTH_USER = "admin"

REQUIRED_VARIABLES = (
    "PGADMIN_DEFAULT_EMAIL",
    "PGADMIN_DEFAULT_PASSWORD",
    "RABBITMQ_DEFAULT_USER",
    "RABBITMQ_DEFAULT_PASS",
    "FLOWER_BASIC_AUTH",
    "MP_UI_AUTH",
    "GRAFANA_ADMIN_USER",
    "GRAFANA_ADMIN_PASSWORD",
    "TRAEFIK_DASHBOARD_PASSWORD",
)


@dataclass(frozen=True, slots=True)
class Bookmark:
    """One browser bookmark.

    Stores the visible label and absolute local URL rendered into the Netscape bookmark exchange
    format accepted by major browsers.

    Attributes:
        label: Text shown in the browser bookmark menu.
        url: Absolute local service URL.

    Members:
        None.
    """

    label: str
    url: str


@dataclass(frozen=True, slots=True)
class PasswordRecord:
    """One browser password import record.

    Stores the standard four fields accepted by Chromium-derived browser password importers.
    Keeps each dashboard credential associated with its exact login page.

    Attributes:
        name: Visible login record name.
        url: Login origin or page.
        username: Service login name.
        password: Service login password.

    Members:
        None.
    """

    name: str
    url: str
    username: str
    password: str


class ExportError(Exception):
    """Raised when developer access files cannot be generated safely.

    Represents missing or malformed development configuration and filesystem failures with messages
    that name files or variables but never secret values.
    Inherits Exception and carries only the safe message supplied by the caller.

    Attributes:
        None.

    Members:
        None.
    """


BOOKMARK_FOLDERS = (
    (
        "LocalForge Application",
        (
            Bookmark("Django Admin", "http://localhost:8000/admin/"),
            Bookmark(
                "Scheduler Admin",
                "http://localhost:8000/admin/django_celery_beat/",
            ),
            Bookmark("OpenAPI Schema", "http://localhost:8000/api/schema/"),
            Bookmark("Swagger UI", "http://localhost:8000/api/schema/swagger-ui/"),
            Bookmark("ReDoc", "http://localhost:8000/api/schema/redoc/"),
            Bookmark("Readiness", "http://localhost:8000/health/"),
        ),
    ),
    (
        "LocalForge Infrastructure",
        (
            Bookmark("Traefik Dashboard", "http://localhost:8081/dashboard/"),
            Bookmark("pgAdmin", "http://localhost:5050/"),
            Bookmark("RabbitMQ Management", "http://localhost:15672/"),
            Bookmark("Flower", "http://localhost:5555/"),
            Bookmark("Mailpit", "http://localhost:8025/"),
            Bookmark("Grafana", "http://localhost:3000/"),
            Bookmark("Grafana Explore", "http://localhost:3000/explore"),
        ),
    ),
)


def read_environment(root: Path) -> dict[str, str]:
    """Read and validate the generated development environment.

    Requires every credential used by the browser import files to hold a concrete non-placeholder
    value before any output file is written.

    Arguments:
        root: Repository root containing the development environment file.

    Returns:
        Parsed development environment values.

    Raises:
        ExportError: If the file is absent, unreadable, malformed, or incomplete.
    """
    path = root / ENVIRONMENT_FILE
    try:
        values = parse_env_text(path.read_text(encoding="utf-8"))
    except (OSError, ManifestError) as error:
        message = f"{ENVIRONMENT_FILE} could not be read: {error}"
        raise ExportError(message) from error

    missing = sorted(
        name for name in REQUIRED_VARIABLES if values.get(name, "") in {"", GENERATED_PLACEHOLDER}
    )
    if missing:
        message = f"{ENVIRONMENT_FILE} is missing usable values for: {', '.join(missing)}"
        raise ExportError(message)

    return values


def split_basic_auth(values: dict[str, str], name: str) -> tuple[str, str]:
    """Split one generated basic-authentication value.

    Preserves colons inside the password by splitting only the first separator and rejects an empty
    username or password as malformed configuration.

    Arguments:
        values: Parsed development environment values.
        name: Variable holding the plaintext ``username:password`` pair.

    Returns:
        Username and password.

    Raises:
        ExportError: If the configured value is not a complete pair.
    """
    username, separator, password = values[name].partition(":")
    if not separator or not username or not password:
        message = f"{name} must contain a non-empty username and password"
        raise ExportError(message)

    return username, password


def password_records(values: dict[str, str]) -> tuple[PasswordRecord, ...]:
    """Build browser password records from environment values.

    Maps only dashboards whose credentials are generated in the development environment; Django
    admin is omitted because its superuser is created interactively.

    Arguments:
        values: Validated development environment values.

    Returns:
        Password records in stable bookmark order.
    """
    flower_user, flower_password = split_basic_auth(values, "FLOWER_BASIC_AUTH")
    mailpit_user, mailpit_password = split_basic_auth(values, "MP_UI_AUTH")

    return (
        PasswordRecord(
            "LocalForge Traefik Dashboard",
            "http://localhost:8081/dashboard/",
            BASIC_AUTH_USER,
            values["TRAEFIK_DASHBOARD_PASSWORD"],
        ),
        PasswordRecord(
            "LocalForge pgAdmin",
            "http://localhost:5050/",
            values["PGADMIN_DEFAULT_EMAIL"],
            values["PGADMIN_DEFAULT_PASSWORD"],
        ),
        PasswordRecord(
            "LocalForge RabbitMQ",
            "http://localhost:15672/",
            values["RABBITMQ_DEFAULT_USER"],
            values["RABBITMQ_DEFAULT_PASS"],
        ),
        PasswordRecord(
            "LocalForge Flower",
            "http://localhost:5555/",
            flower_user,
            flower_password,
        ),
        PasswordRecord(
            "LocalForge Mailpit",
            "http://localhost:8025/",
            mailpit_user,
            mailpit_password,
        ),
        PasswordRecord(
            "LocalForge Grafana",
            "http://localhost:3000/",
            values["GRAFANA_ADMIN_USER"],
            values["GRAFANA_ADMIN_PASSWORD"],
        ),
    )


def render_bookmarks() -> str:
    """Render LocalForge bookmarks in Netscape exchange format.

    Groups application and infrastructure surfaces while omitting all credentials, making the file
    safe to inspect before browser import.

    Arguments:
        None.

    Returns:
        Complete UTF-8 bookmark document.
    """
    lines = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        "<TITLE>LocalForge Developer Services</TITLE>",
        "<H1>LocalForge Developer Services</H1>",
        "<DL><p>",
    ]
    for folder, bookmarks in BOOKMARK_FOLDERS:
        lines.extend((f"    <DT><H3>{escape(folder)}</H3>", "    <DL><p>"))
        lines.extend(
            f'        <DT><A HREF="{escape(bookmark.url, quote=True)}">{escape(bookmark.label)}</A>'
            for bookmark in bookmarks
        )
        lines.append("    </DL><p>")
    lines.append("</DL><p>")

    return "\n".join(lines) + "\n"


def render_passwords(records: tuple[PasswordRecord, ...]) -> str:
    """Render browser password records as CSV.

    Uses the standard ``name,url,username,password`` header accepted by Chromium-derived browser
    importers and quotes fields through the standard library CSV writer.

    Arguments:
        records: Validated password records.

    Returns:
        Complete CSV text with LF line endings.
    """
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("name", "url", "username", "password"))
    writer.writerows(
        (record.name, record.url, record.username, record.password) for record in records
    )

    return output.getvalue()


def write_output(path: Path, text: str) -> None:
    """Write one generated browser import file.

    Replaces an existing output deterministically and normalizes line endings for portable browser
    imports.

    Arguments:
        path: Destination file.
        text: Complete rendered content.

    Returns:
        None.

    Raises:
        ExportError: If the destination cannot be written.
    """
    try:
        path.write_text(text, encoding="utf-8", newline="\n")
    except OSError as error:
        message = f"{path.name} could not be written: {error}"
        raise ExportError(message) from error


def export_developer_access(root: Path) -> None:
    """Generate both browser import files.

    Resolves and validates all credentials before writing either destination, then writes stable
    bookmark and password formats at the repository root.

    Arguments:
        root: Repository root receiving the generated files.

    Returns:
        None.

    Raises:
        ExportError: If configuration or output generation fails.
    """
    values = read_environment(root)
    records = password_records(values)
    bookmarks = render_bookmarks()
    passwords = render_passwords(records)
    write_output(root / PASSWORDS_FILE, passwords)
    write_output(root / BOOKMARKS_FILE, bookmarks)


def main(*, root: Path = REPOSITORY_ROOT) -> int:
    """Run developer browser import generation.

    Prints only destination names on success and safe diagnostic text on failure, never credential
    values.

    Arguments:
        root: Repository root containing configuration and receiving output.

    Returns:
        Zero on success or one when generation fails.
    """
    try:
        export_developer_access(root)
    except ExportError as error:
        print(f"developer access export failed: {error}")
        return EXIT_FAILED

    print(f"created {BOOKMARKS_FILE} and {PASSWORDS_FILE}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
