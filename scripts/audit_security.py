"""Security audit commands for the completed LocalForge platform.

Runs deployment, repository-history, dependency, image, runtime exposure, and runtime secret
checks without printing credential values or delegating trust decisions to a scanner exit code.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urlparse

from scripts.gen_secrets import parse_env_text
from scripts.manage_platform import CONTAINER_IMAGES, REQUIRED_CONTAINERS

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
IMAGE_POLICY_RELATIVE_PATH = Path("docs/security/image-vulnerability-policy.json")
COMMAND_TIMEOUT_SECONDS = 1800
HTTP_TIMEOUT_SECONDS = 15
MIN_SECRET_LENGTH = 8
TRIVY_SCHEMA_VERSION = 2
TRIVY_IMAGE = "aquasec/trivy:0.68.2"
GITLEAKS_IMAGE = "zricethezav/gitleaks:v8.28.0"
PIP_AUDIT_VERSION = "2.10.1"
HTTP_BODY_LIMIT_BYTES = 4096
EXPECTED_DEPLOYMENT_WARNINGS = frozenset(
    {"security.W004", "security.W008", "security.W012", "security.W016"}
)
DEPLOYMENT_WARNING_PATTERN = re.compile(r"\(([a-z][a-z0-9_]*\.[A-Z]\d{3})\)")
IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
PROTECTED_ENDPOINTS: Mapping[str, tuple[str, frozenset[int]]] = {
    "traefik": ("http://127.0.0.1:8081/api/overview", frozenset({HTTPStatus.UNAUTHORIZED})),
    "rabbitmq": ("http://127.0.0.1:15672/api/overview", frozenset({HTTPStatus.UNAUTHORIZED})),
    "flower": ("http://127.0.0.1:5555/api/workers", frozenset({HTTPStatus.UNAUTHORIZED})),
    "mailpit": ("http://127.0.0.1:8025/api/v1/messages", frozenset({HTTPStatus.UNAUTHORIZED})),
    "grafana": ("http://127.0.0.1:3000/api/search", frozenset({HTTPStatus.UNAUTHORIZED})),
}
PGADMIN_URL = "http://127.0.0.1:5050/browser/"
PGADMIN_LOGIN_LOCATION = "/login?next=/browser/"
PRIVATE_HOST_PORTS = (
    8082,
    8888,
    9333,
    9090,
    3100,
    12345,
    8090,
    9187,
    9121,
    9122,
    28888,
    29333,
)
REFUSED_SOCKET_ERRORS = frozenset(
    {
        errno.ECONNREFUSED,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        10051,
        10061,
        10065,
    }
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Represent one captured command result.

    Carries only the exit status and combined output needed by audit parsers.
    Inherits nothing and exposes no behavior beyond immutable storage.

    Attributes:
        code: Process exit status.
        output: Combined standard output and standard error.
    """

    code: int
    output: str


@dataclass(frozen=True, slots=True)
class HttpObservation:
    """Represent one bounded local HTTP response.

    Carries the status, redirect location, and limited response bytes needed by exposure checks.
    Inherits nothing and stores no authentication material.

    Attributes:
        status: HTTP response status.
        location: Redirect destination, or an empty string.
        body: Response prefix bounded by the audit limit.
    """

    status: int
    location: str
    body: bytes


class AuditRunner(Protocol):
    """Describe the command boundary used by the security audit.

    Allows unit tests to provide deterministic scanner and Docker results.
    Inherits Protocol so real and fabricated runners share one structural contract.

    Members:
        run: Execute one command and capture its result.
    """

    def run(
        self,
        command: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Execute one command.

        Defines the captured subprocess boundary required by each audit.
        Leaves execution policy to the concrete runner.

        Arguments:
            command: Argument vector to execute.
            environment: Optional complete child environment.

        Returns:
            Captured process result.
        """


class HostRunner:
    """Run audit commands on the current host.

    Uses bounded non-shell subprocesses so arguments cannot be reinterpreted by a command shell.
    Inherits nothing and satisfies AuditRunner structurally.

    Members:
        run: Execute one bounded command.
    """

    def run(
        self,
        command: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Execute one bounded command.

        Captures both output streams without invoking a command shell.
        Applies the repository root and shared audit timeout.

        Arguments:
            command: Argument vector to execute.
            environment: Optional complete child environment.

        Returns:
            Captured process result.
        """
        completed = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            env=dict(environment) if environment is not None else None,
            check=False,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        return CommandResult(
            code=completed.returncode,
            output=f"{completed.stdout}{completed.stderr}",
        )


def load_testing_environment(root: Path = REPOSITORY_ROOT) -> dict[str, str]:
    """Load the production-shaped host testing environment.

    Prefers the generated host file and falls back to the mounted container testing file.
    Merges values over the current process environment without printing them.

    Arguments:
        root: Repository containing the generated host environment.

    Returns:
        Complete child environment for Django deployment checks.

    Raises:
        FileNotFoundError: If the generated host environment is absent.
    """
    host_path = root / ".env.testing.host"
    path = host_path if host_path.is_file() else root / ".env.testing"
    values = parse_env_text(path.read_text(encoding="utf-8"))
    return {**os.environ, **values, "DJANGO_SETTINGS_MODULE": "config.settings.testing"}


def deployment_check(runner: AuditRunner, root: Path = REPOSITORY_ROOT) -> bool:
    """Require only the documented local-transport deployment warnings.

    Runs Django's deployment checker in testing settings and rejects any added or missing warning,
    keeping the plaintext-only exceptions explicit rather than silencing them.

    Arguments:
        runner: Command execution boundary.
        root: Repository containing the generated testing environment.

    Returns:
        True when the check succeeds with exactly the accepted warning identifiers.
    """
    result = runner.run(
        (sys.executable, "src/manage.py", "check", "--deploy"),
        environment=load_testing_environment(root),
    )
    warnings = frozenset(DEPLOYMENT_WARNING_PATTERN.findall(result.output))
    return result.code == 0 and warnings == EXPECTED_DEPLOYMENT_WARNINGS


def docker_bind_path(path: Path) -> str:
    """Render one host path for a Docker bind mount.

    Normalizes Windows separators while retaining the drive prefix Docker Desktop accepts.
    Leaves POSIX paths unchanged.

    Arguments:
        path: Host path to mount.

    Returns:
        Docker-compatible path text.
    """
    return str(path.resolve()).replace("\\", "/")


def history_secret_check(
    runner: AuditRunner,
    root: Path = REPOSITORY_ROOT,
) -> bool:
    """Scan reachable history plus current tracked and untracked project files.

    Uses pinned Gitleaks with redaction for full Git history, then copies the current non-ignored
    project file set into an isolated snapshot so worktree and untracked changes are also scanned.

    Arguments:
        runner: Command execution boundary.
        root: Repository whose history and current files are scanned.

    Returns:
        True when both pinned scans complete without a finding.
    """
    repository = docker_bind_path(root)
    history = runner.run(
        (
            "docker",
            "run",
            "--rm",
            "-v",
            f"{repository}:/repo:ro",
            GITLEAKS_IMAGE,
            "git",
            "--log-opts=--all",
            "--config",
            "/repo/.gitleaks.toml",
            "--redact",
            "--no-banner",
            "--no-color",
            "--log-level",
            "error",
            "/repo",
        )
    )
    if history.code != 0:
        return False

    staged = runner.run(
        (
            "docker",
            "run",
            "--rm",
            "-v",
            f"{repository}:/repo:ro",
            GITLEAKS_IMAGE,
            "git",
            "--staged",
            "--config",
            "/repo/.gitleaks.toml",
            "--redact",
            "--no-banner",
            "--no-color",
            "--log-level",
            "error",
            "/repo",
        )
    )
    if staged.code != 0:
        return False

    listed = runner.run(("git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"))
    if listed.code != 0:
        return False
    names = tuple(name for name in listed.output.split("\0") if name)
    if not names:
        return False

    with tempfile.TemporaryDirectory(prefix="localforge-gitleaks-") as temporary:
        snapshot = Path(temporary)
        for name in names:
            source = (root / name).resolve()
            try:
                relative = source.relative_to(root.resolve())
            except ValueError:
                return False
            if not source.is_file():
                continue
            destination = snapshot / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        current = runner.run(
            (
                "docker",
                "run",
                "--rm",
                "-v",
                f"{docker_bind_path(snapshot)}:/repo:ro",
                GITLEAKS_IMAGE,
                "dir",
                "--config",
                "/repo/.gitleaks.toml",
                "--redact",
                "--no-banner",
                "--no-color",
                "--log-level",
                "error",
                "/repo",
            )
        )
    return current.code == 0


def dependency_check(runner: AuditRunner) -> bool:
    """Audit the locked runtime dependency resolution.

    Exports the uv lock without project or development dependencies, then runs a pinned pip-audit
    release against that exact temporary requirements set.

    Arguments:
        runner: Command execution boundary.

    Returns:
        True when export and vulnerability audit both succeed.
    """
    with tempfile.TemporaryDirectory(prefix="localforge-dependency-audit-") as temporary:
        requirements = Path(temporary) / "requirements.txt"
        exported = runner.run(
            (
                "uv",
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--no-hashes",
                "--quiet",
                "--output-file",
                str(requirements),
            )
        )
        if exported.code != 0:
            return False
        audited = runner.run(
            (
                "uvx",
                f"pip-audit@{PIP_AUDIT_VERSION}",
                "-r",
                str(requirements),
                "--format",
                "json",
                "--progress-spinner",
                "off",
            )
        )
        return audited.code == 0


def vulnerability_count(payload: str) -> int:
    """Count Trivy vulnerability records in one JSON result.

    Validates the image-report envelope before counting normalized package findings.
    Rejects a successful but empty or malformed scanner payload.

    Arguments:
        payload: Trivy JSON output.

    Returns:
        Number of reported vulnerability records.

    Raises:
        TypeError: If the JSON top level is not an object.
        json.JSONDecodeError: If the payload is not JSON.
    """
    return len(vulnerability_records(payload))


def trivy_document(payload: str) -> dict[str, object]:
    """Validate and return one complete Trivy image report.

    Requires schema version 2, artifact identity fields, and at least one target result.
    Rejects presentation-shaped JSON that cannot prove an image was scanned.

    Arguments:
        payload: Trivy JSON output.

    Returns:
        Validated image report.

    Raises:
        TypeError: If the report shape is invalid.
        json.JSONDecodeError: If the payload is not JSON.
    """
    document = json.loads(payload)
    if (
        not isinstance(document, dict)
        or document.get("SchemaVersion") != TRIVY_SCHEMA_VERSION
        or not isinstance(document.get("ArtifactName"), str)
        or not document["ArtifactName"]
        or not isinstance(document.get("ArtifactID"), str)
        or not document["ArtifactID"]
        or not isinstance(document.get("Results"), list)
        or not document["Results"]
        or not all(
            isinstance(result, dict) and isinstance(result.get("Target"), str)
            for result in document["Results"]
        )
    ):
        message = "Trivy JSON result has an invalid image-report schema"
        raise TypeError(message)
    return cast("dict[str, object]", document)


def trivy_results(payload: str) -> list[dict[str, object]]:
    """Return validated Trivy target results.

    Delegates image-envelope validation before exposing the target list.
    Keeps vulnerability and secret parsers on one schema boundary.

    Arguments:
        payload: Trivy JSON output.

    Returns:
        Validated image result mappings.

    Raises:
        TypeError: If the report shape is invalid.
        json.JSONDecodeError: If the payload is not JSON.
    """
    document = trivy_document(payload)
    return cast("list[dict[str, object]]", document["Results"])


def vulnerability_records(payload: str) -> list[dict[str, str]]:
    """Normalize Trivy vulnerability records for stable policy comparison.

    Retains the target, identifier, package, installed version, fixed version, and severity while
    discarding presentation metadata that can change independently of the finding.

    Arguments:
        payload: Trivy JSON output.

    Returns:
        Sorted normalized vulnerability records.

    Raises:
        TypeError: If the JSON top level is not an object.
        json.JSONDecodeError: If the payload is not JSON.
    """
    records: list[dict[str, str]] = []
    for result in trivy_results(payload):
        target = str(result.get("Target", ""))
        vulnerabilities = result.get("Vulnerabilities", [])
        if vulnerabilities is None:
            vulnerabilities = []
        if not isinstance(vulnerabilities, list):
            message = "Trivy vulnerabilities must be a list or null"
            raise TypeError(message)
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict):
                continue
            records.append(
                {
                    "fixed_version": str(vulnerability.get("FixedVersion", "")),
                    "identifier": str(vulnerability.get("VulnerabilityID", "")),
                    "installed_version": str(vulnerability.get("InstalledVersion", "")),
                    "package": str(vulnerability.get("PkgName", "")),
                    "severity": str(vulnerability.get("Severity", "")),
                    "target": target,
                }
            )
    return sorted(records, key=lambda record: tuple(record.values()))


def image_secret_records(payload: str) -> list[dict[str, object]]:
    """Normalize image secret findings for exact reviewed comparison.

    Retains only target path, rule, category, and bounded line coordinates.
    Omits matched values so policy and logs never contain detected material.

    Arguments:
        payload: Trivy JSON output.

    Returns:
        Sorted normalized image secret findings.

    Raises:
        TypeError: If the report or secret list shape is invalid.
        json.JSONDecodeError: If the payload is not JSON.
    """
    records: list[dict[str, object]] = []
    for result in trivy_results(payload):
        target = str(result.get("Target", ""))
        secrets = result.get("Secrets", [])
        if secrets is None:
            secrets = []
        if not isinstance(secrets, list):
            message = "Trivy secrets must be a list or null"
            raise TypeError(message)
        for secret in secrets:
            if not isinstance(secret, dict):
                continue
            records.append(
                {
                    "category": str(secret.get("Category", "")),
                    "end_line": int(secret.get("EndLine", 0)),
                    "rule": str(secret.get("RuleID", "")),
                    "start_line": int(secret.get("StartLine", 0)),
                    "target": target,
                }
            )
    return sorted(records, key=lambda record: tuple(str(value) for value in record.values()))


def vulnerability_policy_entry(
    payload: str,
    *,
    image_id: str | None = None,
) -> dict[str, object]:
    """Summarize one exact scanner result without duplicating every package row.

    Records a canonical digest, total package finding count, and unique vulnerability identifiers,
    allowing documentation to group rationale by image while the machine gate detects any drift.

    Arguments:
        payload: Trivy JSON output.
        image_id: Immutable Docker image identity requested from Trivy.

    Returns:
        Stable policy entry for one image.
    """
    document = trivy_document(payload)
    records = vulnerability_records(payload)
    canonical = json.dumps(records, separators=(",", ":"), sort_keys=True).encode()
    return {
        "artifact_id": document["ArtifactID"],
        "digest": hashlib.sha256(canonical).hexdigest(),
        "finding_count": len(records),
        "image_id": image_id or document["ArtifactName"],
        "secret_findings": image_secret_records(payload),
        "vulnerability_ids": sorted({record["identifier"] for record in records}),
    }


def load_image_policy(root: Path = REPOSITORY_ROOT) -> dict[str, object]:
    """Load the reviewed image-vulnerability snapshot.

    Reads the policy as JSON and requires an object-shaped top level.
    Leaves detailed schema comparison to the image audit.

    Arguments:
        root: Repository containing the documented policy file.

    Returns:
        Parsed policy object.

    Raises:
        FileNotFoundError: If the policy file is absent.
        TypeError: If the policy top level is not an object.
        json.JSONDecodeError: If the policy is invalid JSON.
    """
    policy = json.loads((root / IMAGE_POLICY_RELATIVE_PATH).read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        message = "image vulnerability policy must be an object"
        raise TypeError(message)
    return cast("dict[str, object]", policy)


def inspected_image_id(runner: AuditRunner, image: str) -> str | None:
    """Resolve one local image tag to its immutable Docker identity.

    Requires a successful inspect result with the exact SHA-256 identifier shape.
    Returns no identity when the tag is absent or malformed.

    Arguments:
        runner: Command execution boundary.
        image: Registered image tag.

    Returns:
        Immutable image ID, or None.
    """
    inspected = runner.run(("docker", "image", "inspect", "--format", "{{.Id}}", image))
    image_id = inspected.output.strip()
    if inspected.code != 0 or IMAGE_ID_PATTERN.fullmatch(image_id) is None:
        return None
    return image_id


def live_containers_use_image(
    runner: AuditRunner,
    image: str,
    image_id: str,
) -> bool:
    """Verify every required container for one tag runs its inspected identity.

    Resolves registered containers from the authoritative image map.
    Rejects missing containers, inspect failures, count drift, and stale identities.

    Arguments:
        runner: Command execution boundary.
        image: Registered image tag.
        image_id: Immutable local image identity.

    Returns:
        True when every required container uses that identity.
    """
    containers = sorted(name for name in REQUIRED_CONTAINERS if CONTAINER_IMAGES[name] == image)
    if not containers:
        return False
    live = runner.run(("docker", "inspect", "--format", "{{.Image}}", *containers))
    live_ids = tuple(line for line in live.output.splitlines() if line)
    return (
        live.code == 0
        and len(live_ids) == len(containers)
        and all(identifier == image_id for identifier in live_ids)
    )


def scanned_image_policy_entry(
    runner: AuditRunner,
    image_id: str,
    cache: str,
) -> dict[str, object] | None:
    """Scan one immutable image and normalize its security policy entry.

    Uses an isolated scan cache with a shared vulnerability database directory.
    Requires Trivy to report the same immutable reference it was asked to scan.

    Arguments:
        runner: Command execution boundary.
        image_id: Immutable Docker image identity.
        cache: Docker-compatible temporary cache path.

    Returns:
        Normalized policy entry, or None on scanner or schema failure.
    """
    result = runner.run(
        (
            "docker",
            "run",
            "--rm",
            "-v",
            "/var/run/docker.sock:/var/run/docker.sock",
            "-v",
            f"{cache}:/root/.cache/trivy",
            TRIVY_IMAGE,
            "image",
            "--scanners",
            "vuln,secret",
            "--severity",
            "HIGH,CRITICAL",
            "--ignore-unfixed",
            "--format",
            "json",
            "--quiet",
            "--skip-version-check",
            "--skip-files",
            "**/__pycache__/**",
            image_id,
        )
    )
    try:
        document = trivy_document(result.output)
        entry = vulnerability_policy_entry(result.output, image_id=image_id)
    except json.JSONDecodeError, TypeError:
        return None
    if result.code != 0 or document["ArtifactName"] != image_id:
        return None
    return entry


def accepted_image_policy(root: Path = REPOSITORY_ROOT) -> dict[str, object] | None:
    """Load image entries only when policy metadata matches this scanner.

    Contains file, JSON, schema-key, and scanner-version failures behind one fail-closed boundary.
    Returns no entries when the reviewed policy cannot govern the active audit.

    Arguments:
        root: Repository containing the reviewed image policy.

    Returns:
        Reviewed per-image entries, or None.
    """
    try:
        policy = load_image_policy(root)
        accepted = policy["images"]
    except FileNotFoundError, KeyError, TypeError, json.JSONDecodeError:
        return None
    if policy.get("scanner") != TRIVY_IMAGE or not isinstance(accepted, dict):
        return None
    return cast("dict[str, object]", accepted)


def image_matches_policy(
    runner: AuditRunner,
    image: str,
    expected: object,
    cache: str,
) -> bool:
    """Verify one image tag, live identity, and scanner result.

    Evaluates each boundary in order and emits one non-secret diagnostic naming the first drift
    found, while keeping the combined image audit fail-closed.

    Arguments:
        runner: Command execution boundary.
        image: Registered image tag.
        expected: Reviewed policy entry for the image.
        cache: Docker-compatible temporary Trivy cache path.

    Returns:
        Whether the image and every live consumer match reviewed evidence.
    """
    image_id = inspected_image_id(runner, image)
    failure = ""
    if image_id is None:
        failure = f"image-tag unavailable-or-malformed image={image}"
    elif not isinstance(expected, dict):
        failure = f"image-policy entry-not-object image={image}"
    elif expected.get("image_id") != image_id:
        failure = (
            f"image-policy identity image={image} "
            f"expected={expected.get('image_id')} actual={image_id}"
        )
    elif not live_containers_use_image(runner, image, image_id):
        failure = f"image-live-identity image={image} expected={image_id}"
    else:
        scanned = scanned_image_policy_entry(runner, image_id, cache)
        if scanned is None:
            failure = f"image-scan unavailable-or-invalid image={image} id={image_id}"
        elif scanned != expected:
            fields = sorted(
                key for key in set(expected) | set(scanned) if expected.get(key) != scanned.get(key)
            )
            failure = f"image-policy drift image={image} fields={fields}"

    if failure:
        print(f"FAIL {failure}")
        return False
    return True


def image_check(
    runner: AuditRunner,
    root: Path = REPOSITORY_ROOT,
) -> bool:
    """Scan every registered unique runtime image for fixable severe vulnerabilities.

    Uses a pinned Trivy container, one temporary shared database cache, the local Docker image
    store, and JSON parsing rather than trusting a presentation-oriented scanner exit code.

    Arguments:
        runner: Command execution boundary.
        root: Repository containing the reviewed image policy.

    Returns:
        True when every image matches its exact reviewed vulnerability snapshot.
    """
    accepted = accepted_image_policy(root)
    if accepted is None:
        print("FAIL image-policy unavailable-or-incompatible")
        return False

    images = sorted(set(CONTAINER_IMAGES.values()))
    if set(accepted) != set(images):
        missing = sorted(set(images) - set(accepted))
        unexpected = sorted(set(accepted) - set(images))
        print(f"FAIL image-policy inventory missing={missing} unexpected={unexpected}")
        return False

    with tempfile.TemporaryDirectory(prefix="localforge-trivy-") as temporary:
        cache = str(Path(temporary)).replace("\\", "/")
        for image in images:
            if not image_matches_policy(runner, image, accepted[image], cache):
                return False
    return True


def http_observation(url: str) -> HttpObservation:
    """Read one local operator endpoint without credentials or redirect following.

    Captures the exact first response so login redirects cannot be mistaken for protected content.
    Bounds body reads because only a login-boundary marker could be relevant.

    Arguments:
        url: Local endpoint to request.

    Returns:
        Bounded response observation.

    Raises:
        OSError: If the endpoint cannot be reached.
        ValueError: If the URL is not a local plaintext HTTP endpoint.
    """
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        message = "security audit HTTP endpoints must use local plaintext HTTP"
        raise ValueError(message)
    connection = http.client.HTTPConnection(
        parsed.hostname,
        parsed.port,
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    try:
        connection.request("GET", parsed.path or "/", headers={"Host": "localhost"})
        response = connection.getresponse()
        body = response.read(HTTP_BODY_LIMIT_BYTES)
        return HttpObservation(
            status=response.status,
            location=response.getheader("Location", ""),
            body=body,
        )
    finally:
        connection.close()


def http_status(url: str) -> int:
    """Read one local operator endpoint status without credentials.

    Delegates to the non-redirecting bounded observation helper.
    Preserves the compact status-only interface used by protected endpoints.

    Arguments:
        url: Local endpoint to request.

    Returns:
        HTTP response status.

    Raises:
        OSError: If the endpoint cannot be reached.
        ValueError: If the URL is not a local plaintext HTTP endpoint.
    """
    return http_observation(url).status


def port_is_closed(port: int) -> bool:
    """Report whether one loopback TCP port explicitly refuses or cannot route.

    Treats timeout, reset, and accepted connections as failures.
    Accepts only explicit connection-refused or unreachable socket errors.

    Arguments:
        port: Host port that must not be published.

    Returns:
        True when the TCP stack explicitly reports no reachable listener.
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=HTTP_TIMEOUT_SECONDS):
            return False
    except TimeoutError, ConnectionResetError:
        return False
    except OSError as error:
        winerror = getattr(error, "winerror", None)
        code = winerror if winerror is not None else error.errno
        return code in REFUSED_SOCKET_ERRORS


def protected_endpoints_reject_anonymous_access() -> bool:
    """Verify retained dashboards reject unauthenticated API requests.

    Requires each registered endpoint to return its exact authentication status.
    Treats transport and URL validation failures as audit failures.

    Arguments:
        None.

    Returns:
        True when every protected endpoint rejects the request.
    """
    for url, accepted in PROTECTED_ENDPOINTS.values():
        try:
            if http_status(url) not in accepted:
                return False
        except OSError, ValueError:
            return False
    return True


def pgadmin_rejects_anonymous_browser_access() -> bool:
    """Verify pgAdmin redirects its browser boundary to the exact login path.

    Rejects success responses, alternate redirects, and database-shaped body content.
    Treats transport and URL validation failures as audit failures.

    Arguments:
        None.

    Returns:
        True when pgAdmin exposes only its login redirect.
    """
    try:
        pgadmin = http_observation(PGADMIN_URL)
    except OSError, ValueError:
        return False
    return not (
        pgadmin.status != HTTPStatus.FOUND
        or pgadmin.location != PGADMIN_LOGIN_LOCATION
        or b"database" in pgadmin.body.lower()
    )


def broker_accounts_are_exact(runner: AuditRunner, root: Path = REPOSITORY_ROOT) -> bool:
    """Verify RabbitMQ contains only the generated non-default account.

    Parses the broker's JSON user listing and compares it with the development environment.
    Rejects command, JSON, and shape failures.

    Arguments:
        runner: Command execution boundary.
        root: Repository containing the generated development environment.

    Returns:
        True when only the configured account exists.
    """
    broker = runner.run(
        ("docker", "exec", "rabbitmq-rq4sx", "rabbitmqctl", "list_users", "--formatter", "json")
    )
    try:
        users = json.loads(broker.output)
    except json.JSONDecodeError:
        return False
    if broker.code != 0 or not isinstance(users, list):
        return False
    expected = parse_env_text((root / ".env.development").read_text(encoding="utf-8"))[
        "RABBITMQ_DEFAULT_USER"
    ]
    actual = {
        str(cast("dict[str, object]", record).get("user"))
        for record in users
        if isinstance(record, dict)
    }
    return actual == {expected} and "guest" not in actual


def runtime_exposure_check(runner: AuditRunner, root: Path = REPOSITORY_ROOT) -> bool:
    """Verify dashboard protection, private ports, and broker account replacement.

    Combines exact authentication, TCP refusal, and broker identity checks.
    Returns one fail-closed runtime exposure verdict.

    Arguments:
        runner: Command execution boundary.
        root: Repository containing the generated development environment.

    Returns:
        True when all runtime exposure checks pass.
    """
    return (
        protected_endpoints_reject_anonymous_access()
        and pgadmin_rejects_anonymous_browser_access()
        and all(port_is_closed(port) for port in PRIVATE_HOST_PORTS)
        and broker_accounts_are_exact(runner, root)
    )


def generated_sensitive_values(root: Path = REPOSITORY_ROOT) -> set[str]:
    """Load every generated credential and composed secret-bearing value.

    Derives variable names from `<GENERATED>` manifest entries rather than a hand-maintained list.
    Reads both environments and returns values only when long enough for exact leak matching.

    Arguments:
        root: Repository containing the manifest and generated environment files.

    Returns:
        Distinct generated values that must never appear in runtime evidence.
    """
    manifest = parse_env_text((root / ".env.example").read_text(encoding="utf-8"))
    variables = {name for name, value in manifest.items() if value == "<GENERATED>"}
    values: set[str] = set()
    for name in (".env.development", ".env.testing"):
        environment = parse_env_text((root / name).read_text(encoding="utf-8"))
        values.update(
            value
            for variable, value in environment.items()
            if variable in variables and len(value) >= MIN_SECRET_LENGTH
        )
    return values


def runtime_secret_check(runner: AuditRunner, root: Path = REPOSITORY_ROOT) -> bool:
    """Reject generated credentials in image layers or current container logs.

    Reads every generated manifest value without printing it, then checks Docker history metadata
    and every retained required-container log line for exact occurrences.

    Arguments:
        runner: Command execution boundary.
        root: Repository containing generated environment files.

    Returns:
        True when no sensitive value appears in either runtime evidence source.
    """
    values = generated_sensitive_values(root)

    commands: list[tuple[str, ...]] = [
        ("docker", "history", "--no-trunc", "--format", "{{.CreatedBy}}", image)
        for image in sorted(set(CONTAINER_IMAGES.values()))
    ]
    commands.extend(("docker", "logs", container) for container in sorted(REQUIRED_CONTAINERS))
    for command in commands:
        result = runner.run(command)
        if result.code != 0 or any(value in result.output for value in values):
            return False
    return True


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Registers the supported independent and combined audit scopes.
    Keeps command dispatch choices explicit for operator automation.

    Arguments:
        None.

    Returns:
        Parser accepting one audit scope.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope",
        choices=("all", "deployment", "history", "dependencies", "images", "runtime"),
        default="all",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: AuditRunner | None = None,
    root: Path = REPOSITORY_ROOT,
) -> int:
    """Run the selected security audit scope.

    Dispatches checks in stable order and reports only failed scope names.
    Returns a conventional process status without exposing scanner payloads.

    Arguments:
        argv: Command arguments, or None to read process arguments.
        runner: Optional injected command boundary.
        root: Repository containing generated environments.

    Returns:
        Zero when every selected check passes, otherwise one.
    """
    scope = build_parser().parse_args(argv).scope
    active_runner = runner or HostRunner()
    checks = {
        "deployment": lambda: deployment_check(active_runner, root),
        "history": lambda: history_secret_check(active_runner, root),
        "dependencies": lambda: dependency_check(active_runner),
        "images": lambda: image_check(active_runner, root),
        "runtime": lambda: (
            runtime_exposure_check(active_runner, root)
            and runtime_secret_check(active_runner, root)
        ),
    }
    selected = tuple(checks) if scope == "all" else (scope,)
    failed = [name for name in selected if not checks[name]()]
    if failed:
        print(f"security audit failed: {', '.join(failed)}")
        return 1
    print(f"security audit passed: {', '.join(selected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
