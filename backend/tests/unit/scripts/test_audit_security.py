"""Unit tests for the security audit command.

Exercises command construction, parsing, failure propagation, runtime boundaries, and secret-safe
verdicts without scanning the shared repository or mutating the Docker engine.
"""

from __future__ import annotations

import errno
import http.client
import json
import re
import runpy
import socket
import subprocess
import sys
import tomllib
from contextlib import nullcontext
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from scripts import audit_security as audit
from scripts import manage_platform as platform

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

pytestmark = pytest.mark.unit
PAIR_COUNT = 2
TRANSIENT_SCAN_ATTEMPTS = 2
TEST_IMAGE_ID = f"sha256:{'1' * 64}"
TEST_ARTIFACT_ID = f"sha256:{'2' * 64}"


@dataclass
class FakeRunner:
    """Return configured command results and record invocations.

    Supplies deterministic subprocess outcomes in call order. Inherits nothing and satisfies the
    security audit runner protocol structurally.

    Attributes:
        results: Results consumed in call order.
        calls: Commands and environments observed.

    Members:
        run: Record one command and return its configured result.
    """

    results: list[audit.CommandResult] = field(default_factory=list)
    calls: list[tuple[tuple[str, ...], Mapping[str, str] | None]] = field(default_factory=list)

    def run(
        self,
        command: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> audit.CommandResult:
        """Record one command and return its configured result.

        Appends the complete invocation before consuming the next result.
        Falls back to an empty successful result when no outcome remains.

        Arguments:
            command: Argument vector requested by the audit.
            environment: Optional complete child environment.

        Returns:
            Next configured result, or a successful empty result.
        """
        self.calls.append((tuple(command), environment))
        if self.results:
            return self.results.pop(0)
        return audit.CommandResult(0, "")


def write_environment(root: Path) -> None:
    """Write minimal generated environments for isolated audit tests.

    Creates only the settings needed by deployment, broker, and secret checks.
    Keeps all generated values inside the temporary repository.

    Arguments:
        root: Temporary repository root.

    Returns:
        None.
    """
    (root / ".env.testing.host").write_text(
        "DJANGO_SECRET_KEY=test-secret\nRABBITMQ_DEFAULT_USER=test-user\n",
        encoding="utf-8",
    )
    (root / ".env.example").write_text(
        "DJANGO_SECRET_KEY=<GENERATED>\n"
        "POSTGRES_PASSWORD=<GENERATED>\n"
        "CELERY_BROKER_URL=<GENERATED>\n"
        "RABBITMQ_DEFAULT_USER=localforge_broker\n",
        encoding="utf-8",
    )
    text = (
        "DJANGO_SECRET_KEY=development-secret-value\n"
        "RABBITMQ_DEFAULT_USER=test-user\n"
        "RABBITMQ_DEFAULT_PASS=broker-secret-value\n"
        "POSTGRES_PASSWORD=database-secret-value\n"
        "TUNNEL_TOKEN=development-tunnel-token\n"
        "RESEND_API_KEY=development-resend-key\n"
        "EMAIL_HOST_PASSWORD=development-resend-key\n"
        "CELERY_BROKER_URL=amqp://user:broker-secret-value@broker/vhost\n"
    )
    (root / ".env.development").write_text(text, encoding="utf-8")
    (root / ".env.testing").write_text(
        "DJANGO_SECRET_KEY=testing-secret-value\n"
        "RABBITMQ_DEFAULT_PASS=testing-broker-secret\n"
        "POSTGRES_PASSWORD=testing-database-secret\n"
        "CELERY_BROKER_URL=amqp://user:testing-broker-secret@broker/vhost\n",
        encoding="utf-8",
    )


def deployment_output(*warnings: str) -> str:
    """Build Django deployment-check output.

    Formats warning identifiers in the same shape Django emits.
    Allows exact-set behavior to be tested without running Django.

    Arguments:
        warnings: Warning identifiers to include.

    Returns:
        Text containing one line per warning.
    """
    return "\n".join(f"?: ({warning}) accepted local transport" for warning in warnings)


def trivy_payload(
    count: int = 0,
    *,
    artifact_name: str = "image:tag",
    artifact_id: str = TEST_ARTIFACT_ID,
    secrets: list[dict[str, object]] | None = None,
) -> str:
    """Build a minimal Trivy JSON result.

    Produces one result target with the requested number of records.
    Omits presentation fields that are irrelevant to parser tests.

    Arguments:
        count: Number of vulnerability records to include.
        artifact_name: Image reference Trivy reports.
        artifact_id: Immutable scanner artifact identifier.
        secrets: Optional scanner-shaped secret records.

    Returns:
        Serialized Trivy result.
    """
    return json.dumps(
        {
            "ArtifactID": artifact_id,
            "ArtifactName": artifact_name,
            "Results": [
                {
                    "Secrets": secrets or [],
                    "Target": "image-target",
                    "Vulnerabilities": [{} for _ in range(count)],
                }
            ],
            "SchemaVersion": 2,
        }
    )


def write_image_policy(root: Path, payload: str | None = None) -> None:
    """Write an exact policy for every registered image.

    Summarizes the same scanner payload for each image in the temporary tree.
    Provides a deterministic policy fixture for image-check tests.

    Arguments:
        root: Temporary repository root.
        payload: Trivy output summarized for each image, or None for a clean report.

    Returns:
        None.
    """
    path = root / audit.IMAGE_POLICY_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "images": {
                    image: audit.vulnerability_policy_entry(
                        payload or trivy_payload(artifact_name=TEST_IMAGE_ID),
                        image_id=TEST_IMAGE_ID,
                    )
                    for image in sorted(set(platform.CONTAINER_IMAGES.values()))
                },
                "scanner": audit.TRIVY_IMAGE,
            }
        ),
        encoding="utf-8",
    )


def image_check_results(
    payload: str,
    *,
    scan_code: int = 0,
    image_id: str = TEST_IMAGE_ID,
) -> list[audit.CommandResult]:
    """Build command results for every image identity and scan.

    Emits tag inspection, live-container inspection, and Trivy output in audit order.
    Uses one immutable identity across fabricated images for compact fixtures.

    Arguments:
        payload: Trivy JSON returned for each image.
        scan_code: Trivy process status.
        image_id: Immutable image identity returned by Docker.

    Returns:
        Ordered command results for a complete image audit.
    """
    results: list[audit.CommandResult] = []
    for image in sorted(set(platform.CONTAINER_IMAGES.values())):
        containers = [
            name
            for name in platform.REQUIRED_CONTAINERS
            if platform.CONTAINER_IMAGES[name] == image
        ]
        results.extend(
            (
                audit.CommandResult(0, f"{image_id}\n"),
                audit.CommandResult(0, "".join(f"{image_id}\n" for _ in containers)),
                audit.CommandResult(scan_code, payload),
            )
        )
        if scan_code != 0:
            results.append(audit.CommandResult(scan_code, payload))
    return results


def test_host_runner_captures_process_output() -> None:
    """Run one real bounded child process.

    Exercises the concrete subprocess boundary with harmless output.
    Confirms standard output and the return code are retained together.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If output or status is lost.
    """
    result = audit.HostRunner().run(
        (
            sys.executable,
            "-c",
            "import sys; print('audit-ok'); print('diagnostic', file=sys.stderr)",
        )
    )

    assert result == audit.CommandResult(0, "audit-ok\n", "diagnostic\n")


def test_host_runner_resolves_executable_before_process_start() -> None:
    """Execute the resolved absolute command path.

    Requires PATH lookup to happen explicitly before process creation, avoiding Windows command
    resolution drift between interactive PowerShell and a non-shell Python child.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the unresolved command name reaches subprocess.
    """
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="ok\n",
        stderr="diagnostic\n",
    )

    with (
        patch("scripts.audit_security.shutil.which", return_value="C:/tools/tool.exe"),
        patch("scripts.audit_security.subprocess.run", return_value=completed) as run,
    ):
        result = audit.HostRunner().run(("tool", "status"))

    assert result == audit.CommandResult(0, "ok\n", "diagnostic\n")
    assert run.call_args.args[0] == ("C:/tools/tool.exe", "status")


def test_host_runner_reports_missing_executable_without_traceback() -> None:
    """Convert an absent command into one failed audit result.

    Keeps optional-tool or PATH failures inside the audit verdict rather than crashing the command
    with an unhandled Windows process-creation exception.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a missing command starts a process or raises.
    """
    with (
        patch("scripts.audit_security.shutil.which", return_value=None),
        patch("scripts.audit_security.subprocess.run") as run,
    ):
        result = audit.HostRunner().run(("missing-tool", "status"))

    assert result.code == audit.EXECUTABLE_NOT_FOUND
    assert result.output == "missing-tool not found on PATH\n"
    run.assert_not_called()


def test_host_runner_contains_process_start_race() -> None:
    """Contain an executable that disappears after PATH resolution.

    Models an installation or managed-tool update racing process creation and requires the audit
    to return a stable failure instead of exposing an operating-system traceback.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the process-start failure escapes.
    """
    failure = FileNotFoundError(errno.ENOENT, "missing")

    with (
        patch("scripts.audit_security.shutil.which", return_value="C:/tools/tool.exe"),
        patch("scripts.audit_security.subprocess.run", side_effect=failure),
    ):
        result = audit.HostRunner().run(("tool", "status"))

    assert result.code == audit.EXECUTABLE_NOT_FOUND
    assert result.output == "tool could not be started\n"


def test_testing_environment_merges_generated_values(tmp_path: Path) -> None:
    """Load testing values over the current process environment.

    Reads the temporary host environment through the production loader.
    Confirms required settings and the explicit module selector win.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If generated settings are absent.
    """
    write_environment(tmp_path)

    environment = audit.load_testing_environment(tmp_path)

    assert environment["DJANGO_SECRET_KEY"].startswith("test-")
    assert environment["DJANGO_SETTINGS_MODULE"] == "config.settings.testing"


def test_testing_environment_falls_back_to_container_file(tmp_path: Path) -> None:
    """Load the mounted container environment when no host file exists.

    Creates only `.env.testing`, matching the persistent test image.
    Confirms deployment checks can run identically inside the container.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If the container environment is not selected.
    """
    (tmp_path / ".env.testing").write_text(
        "DJANGO_SECRET_KEY=container-secret\n",
        encoding="utf-8",
    )

    environment = audit.load_testing_environment(tmp_path)

    assert environment["DJANGO_SECRET_KEY"].startswith("container-")


def test_deployment_check_requires_exact_accepted_warnings(tmp_path: Path) -> None:
    """Accept only the documented local plaintext deployment warnings.

    Covers successful, failed, and incomplete Django check results.
    Prevents an added or missing warning from passing unnoticed.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If missing, extra, or failed checks pass.
    """
    write_environment(tmp_path)
    expected = deployment_output(*sorted(audit.EXPECTED_DEPLOYMENT_WARNINGS))

    assert audit.deployment_check(FakeRunner([audit.CommandResult(0, expected)]), tmp_path)
    assert not audit.deployment_check(FakeRunner([audit.CommandResult(1, expected)]), tmp_path)
    assert not audit.deployment_check(
        FakeRunner([audit.CommandResult(0, deployment_output("security.W004"))]),
        tmp_path,
    )
    assert not audit.deployment_check(
        FakeRunner(
            [
                audit.CommandResult(
                    0,
                    deployment_output(
                        *sorted(audit.EXPECTED_DEPLOYMENT_WARNINGS),
                        "models.W042",
                    ),
                )
            ]
        ),
        tmp_path,
    )


def test_history_secret_check_scans_history_and_current_files(tmp_path: Path) -> None:
    """Scan full history plus tracked and untracked current files.

    Exercises both pinned Gitleaks invocations and the isolated current snapshot.
    Confirms scanner and configuration paths are explicit.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If either scan or snapshot command is omitted.
    """
    (tmp_path / ".gitleaks.toml").write_text("[extend]\nuseDefault = true\n", encoding="utf-8")
    (tmp_path / "tracked.txt").write_text("ordinary content\n", encoding="utf-8")
    clean = FakeRunner(
        [
            audit.CommandResult(0, ""),
            audit.CommandResult(0, ""),
            audit.CommandResult(0, "tracked.txt\0.gitleaks.toml\0missing.txt\0"),
            audit.CommandResult(0, ""),
        ]
    )
    assert audit.history_secret_check(clean, tmp_path)
    assert clean.calls[0][0][5] == audit.GITLEAKS_IMAGE
    assert "--log-opts=--all" in clean.calls[0][0]
    assert "--staged" in clean.calls[1][0]
    assert clean.calls[3][0][5] == audit.GITLEAKS_IMAGE


def test_history_secret_check_rejects_scan_and_snapshot_failures(tmp_path: Path) -> None:
    """Reject scanner, file-list, traversal, empty, and current-scan failures.

    Creates the minimum current file required by a valid snapshot.
    Confirms every precondition fails closed.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If any failure path is accepted.
    """
    (tmp_path / ".gitleaks.toml").write_text("[extend]\nuseDefault = true\n", encoding="utf-8")
    (tmp_path / "tracked.txt").write_text("ordinary content\n", encoding="utf-8")

    assert not audit.history_secret_check(
        FakeRunner([audit.CommandResult(1, "")]),
        tmp_path,
    )
    assert not audit.history_secret_check(
        FakeRunner(
            [
                audit.CommandResult(0, ""),
                audit.CommandResult(1, ""),
            ]
        ),
        tmp_path,
    )
    assert not audit.history_secret_check(
        FakeRunner(
            [
                audit.CommandResult(0, ""),
                audit.CommandResult(0, ""),
                audit.CommandResult(1, ""),
            ]
        ),
        tmp_path,
    )
    assert not audit.history_secret_check(
        FakeRunner(
            [
                audit.CommandResult(0, ""),
                audit.CommandResult(0, ""),
                audit.CommandResult(0, ""),
            ]
        ),
        tmp_path,
    )
    assert not audit.history_secret_check(
        FakeRunner(
            [
                audit.CommandResult(0, ""),
                audit.CommandResult(0, ""),
                audit.CommandResult(0, "../outside\0"),
            ]
        ),
        tmp_path,
    )
    assert not audit.history_secret_check(
        FakeRunner(
            [
                audit.CommandResult(0, ""),
                audit.CommandResult(0, ""),
                audit.CommandResult(0, "tracked.txt\0.gitleaks.toml\0"),
                audit.CommandResult(1, ""),
            ]
        ),
        tmp_path,
    )


def test_gitleaks_allowlist_matches_only_complete_fixture_values() -> None:
    """Anchor every documented Gitleaks fixture allowlist expression.

    Confirms each known fixture is accepted only as the complete detected secret.
    Rejects a larger credential that embeds any allowlisted fixture text.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a pattern is unanchored or misses its fixture.
    """
    config = tomllib.loads((audit.REPOSITORY_ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
    patterns = config["allowlists"][0]["regexes"]
    fixtures = (
        "0123456789abcdef0123456789abcdef01234567",
        "Correct-Horse-Battery-Staple-32",
        "Correct-Horse-Battery-Staple-33",
        "Correct-Horse-Battery-Staple-34",
        "Different-Correct-Horse-Battery-34",
        "Third-Correct-Horse-Battery-35",
        "dGhlIHNhbXBsZSBub25jZQ==",
        "djang0-0123456789abcdef0123456789abcdef.nonce",
    )

    for pattern, fixture in zip(patterns, fixtures, strict=True):
        assert re.fullmatch(pattern, fixture)
        assert re.search(pattern, f"prefix-{fixture}-suffix") is None
    assert all(
        re.fullmatch(pattern, "Correct-Horse-Battery-Staple-999999") is None for pattern in patterns
    )


def test_dependency_check_propagates_export_and_audit_status() -> None:
    """Require both locked export and pinned pip-audit success.

    Covers failure at each subprocess boundary and the clean path.
    Confirms the scanner release is pinned in the invocation.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either failure is hidden.
    """
    exported_failure = FakeRunner([audit.CommandResult(1, "")])
    assert not audit.dependency_check(exported_failure)
    assert len(exported_failure.calls) == 1

    audit_failure = FakeRunner(
        [audit.CommandResult(0, ""), audit.CommandResult(1, '{"dependencies": []}')]
    )
    assert not audit.dependency_check(audit_failure)

    passed = FakeRunner(
        [audit.CommandResult(0, ""), audit.CommandResult(0, '{"dependencies": []}')]
    )
    assert audit.dependency_check(passed)
    assert passed.calls[0][0][1:3] == ("export", "--project")
    assert passed.calls[1][0][1] == f"pip-audit@{audit.PIP_AUDIT_VERSION}"


def test_vulnerability_count_validates_and_counts_results() -> None:
    """Parse Trivy result variants.

    Covers absent, null, populated, malformed, and wrong-shaped payloads.
    Confirms only vulnerability rows contribute to the count.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If records are miscounted or invalid structure is accepted.
    """
    assert audit.vulnerability_count(trivy_payload()) == 0
    assert audit.vulnerability_count(trivy_payload(PAIR_COUNT)) == PAIR_COUNT
    nullable = json.dumps(
        {
            "ArtifactID": TEST_ARTIFACT_ID,
            "ArtifactName": "image",
            "Results": [{"Secrets": None, "Target": "target", "Vulnerabilities": None}],
            "SchemaVersion": audit.TRIVY_SCHEMA_VERSION,
        }
    )
    assert audit.vulnerability_records(nullable) == []
    assert audit.image_secret_records(nullable) == []
    for invalid in (
        "{}",
        "[]",
        (
            f'{{"ArtifactID":"{TEST_ARTIFACT_ID}","ArtifactName":"image",'
            '"Results":[],"SchemaVersion":2}'
        ),
        (
            f'{{"ArtifactID":"{TEST_ARTIFACT_ID}","ArtifactName":"image",'
            '"Results":[{}],"SchemaVersion":2}'
        ),
        (
            f'{{"ArtifactID":"{TEST_ARTIFACT_ID}","ArtifactName":"image",'
            '"Results":[{"Target":"x"}],"SchemaVersion":1}'
        ),
    ):
        with pytest.raises(TypeError):
            audit.vulnerability_count(invalid)
    with pytest.raises(json.JSONDecodeError):
        audit.vulnerability_count("not-json")
    invalid_vulnerabilities = json.dumps(
        {
            "ArtifactID": TEST_ARTIFACT_ID,
            "ArtifactName": "image",
            "Results": [{"Target": "target", "Vulnerabilities": {}}],
            "SchemaVersion": audit.TRIVY_SCHEMA_VERSION,
        }
    )
    with pytest.raises(TypeError, match="vulnerabilities"):
        audit.vulnerability_records(invalid_vulnerabilities)
    invalid_secrets = json.dumps(
        {
            "ArtifactID": TEST_ARTIFACT_ID,
            "ArtifactName": "image",
            "Results": [{"Secrets": {}, "Target": "target"}],
            "SchemaVersion": audit.TRIVY_SCHEMA_VERSION,
        }
    )
    with pytest.raises(TypeError, match="secrets"):
        audit.image_secret_records(invalid_secrets)


def test_vulnerability_records_normalize_scanner_fields() -> None:
    """Normalize scanner records and compute a stable policy entry.

    Discards invalid rows and presentation-only metadata.
    Preserves every field that must cause policy drift.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If irrelevant values affect the normalized result.
    """
    payload = json.dumps(
        {
            "ArtifactID": TEST_ARTIFACT_ID,
            "ArtifactName": "image:tag",
            "Results": [
                {
                    "Secrets": [
                        "invalid",
                        {
                            "Category": "Test",
                            "EndLine": 3,
                            "RuleID": "fixture",
                            "StartLine": 2,
                        },
                    ],
                    "Target": "layer",
                    "Vulnerabilities": [
                        "invalid",
                        {
                            "FixedVersion": "2",
                            "InstalledVersion": "1",
                            "PkgName": "package",
                            "Severity": "HIGH",
                            "VulnerabilityID": "CVE-1",
                            "Title": "ignored",
                        },
                    ],
                },
            ],
            "SchemaVersion": 2,
        }
    )

    assert audit.vulnerability_records(payload) == [
        {
            "fixed_version": "2",
            "identifier": "CVE-1",
            "installed_version": "1",
            "package": "package",
            "severity": "HIGH",
            "target": "layer",
        }
    ]
    entry = audit.vulnerability_policy_entry(payload)
    assert entry["finding_count"] == 1
    assert entry["secret_findings"] == [
        {
            "category": "Test",
            "end_line": 3,
            "rule": "fixture",
            "start_line": 2,
            "target": "layer",
        }
    ]


def test_load_image_policy_validates_top_level(tmp_path: Path) -> None:
    """Load only object-shaped image policy documents.

    Writes a syntactically valid but structurally invalid policy.
    Confirms the loader rejects it before image comparison.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If a non-object policy is accepted.
    """
    path = tmp_path / audit.IMAGE_POLICY_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(TypeError):
        audit.load_image_policy(tmp_path)


def test_image_check_scans_unique_images_and_rejects_findings(tmp_path: Path) -> None:
    """Require valid clean output for every registered image.

    Covers exact policy matches, scanner failure, malformed output, and drift.
    Confirms duplicate container images are scanned only once.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If scanner failures or vulnerabilities pass.
    """
    image_count = len(set(platform.CONTAINER_IMAGES.values()))
    write_image_policy(tmp_path)
    payload = trivy_payload(artifact_name=TEST_IMAGE_ID)
    passed = FakeRunner(image_check_results(payload))

    assert audit.image_check(passed, tmp_path)
    assert len(passed.calls) == image_count * 3
    scan_calls = [call[0] for call in passed.calls if audit.TRIVY_IMAGE in call[0]]
    assert len(scan_calls) == image_count
    assert all(call[-1] == TEST_IMAGE_ID for call in scan_calls)
    assert all(
        "--parallel" in call and call[call.index("--parallel") + 1] == "1" for call in scan_calls
    )
    assert all("--cache-backend" not in call for call in scan_calls)
    cache_mounts = {
        argument
        for call in scan_calls
        for argument in call
        if argument.endswith(":/root/.cache/trivy")
    }
    assert len(cache_mounts) == 1

    vulnerable = FakeRunner(
        image_check_results(
            trivy_payload(1, artifact_name=TEST_IMAGE_ID),
        )
    )
    assert not audit.image_check(vulnerable, tmp_path)
    failed = FakeRunner(image_check_results(payload, scan_code=1))
    assert not audit.image_check(failed, tmp_path)
    invalid = FakeRunner(image_check_results("invalid"))
    assert not audit.image_check(invalid, tmp_path)


def test_image_scan_retries_one_transient_scanner_failure() -> None:
    """Retry one unavailable scanner process before failing closed.

    Supplies one non-zero Trivy result followed by a complete valid report, requiring the scan
    boundary to recover once while retaining the same immutable image and low-memory invocation.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the scanner failure is not retried or evidence changes between attempts.
    """
    payload = trivy_payload(artifact_name=TEST_IMAGE_ID)
    runner = FakeRunner(
        [
            audit.CommandResult(1, "temporary scanner failure"),
            audit.CommandResult(0, payload),
        ]
    )

    entry = audit.scanned_image_policy_entry(runner, TEST_IMAGE_ID, "cache")

    assert entry == audit.vulnerability_policy_entry(payload, image_id=TEST_IMAGE_ID)
    assert len(runner.calls) == TRANSIENT_SCAN_ATTEMPTS
    assert runner.calls[0][0] == runner.calls[1][0]


def test_image_scan_ignores_docker_pull_progress_on_standard_error() -> None:
    """Parse scanner JSON separately from Docker pull diagnostics.

    Models the first security audit after a complete Docker image cleanup, where ``docker run``
    pulls Trivy and writes progress to standard error while Trivy writes valid JSON to standard
    output.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If pull progress corrupts valid scanner evidence.
    """
    payload = trivy_payload(artifact_name=TEST_IMAGE_ID)
    runner = FakeRunner(
        [
            audit.CommandResult(
                0,
                payload,
                "Unable to find image locally\nPull complete\n",
            )
        ]
    )

    entry = audit.scanned_image_policy_entry(runner, TEST_IMAGE_ID, "cache")

    assert entry == audit.vulnerability_policy_entry(payload, image_id=TEST_IMAGE_ID)


def test_image_check_rejects_missing_or_drifted_policy(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject absent, malformed, wrong-scanner, and incomplete policies.

    Exercises every policy precondition before a scanner can run.
    Confirms invalid policy state cannot consume command results.

    Arguments:
        tmp_path: Temporary repository root.
        capsys: Fixture capturing actionable failure diagnostics.

    Returns:
        None.

    Raises:
        AssertionError: If policy drift reaches the scanner.
    """
    runner = FakeRunner()
    assert not audit.image_check(runner, tmp_path)
    assert "image-policy unavailable-or-incompatible" in capsys.readouterr().out

    path = tmp_path / audit.IMAGE_POLICY_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    for policy in (
        "invalid",
        "[]",
        "{}",
        '{"images":{},"scanner":"wrong"}',
        json.dumps({"images": {}, "scanner": audit.TRIVY_IMAGE}),
    ):
        path.write_text(policy, encoding="utf-8")
        assert not audit.image_check(runner, tmp_path)
        assert "FAIL image-policy" in capsys.readouterr().out
    assert runner.calls == []


def test_image_check_rejects_identity_and_live_container_drift(tmp_path: Path) -> None:
    """Bind scanner evidence to the immutable running image identity.

    Covers tag inspection, policy identity, registered-container presence, and live identity drift.
    Confirms Trivy must report the exact immutable reference it was asked to scan.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If any identity mismatch reaches policy acceptance.
    """
    write_image_policy(tmp_path)
    first_image = min(set(platform.CONTAINER_IMAGES.values()))
    container_count = sum(
        platform.CONTAINER_IMAGES[name] == first_image for name in platform.REQUIRED_CONTAINERS
    )

    for inspected in (
        audit.CommandResult(1, ""),
        audit.CommandResult(0, "not-an-image-id"),
    ):
        assert not audit.image_check(FakeRunner([inspected]), tmp_path)

    policy_path = tmp_path / audit.IMAGE_POLICY_RELATIVE_PATH
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["images"][first_image]["image_id"] = f"sha256:{'3' * 64}"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    assert not audit.image_check(
        FakeRunner([audit.CommandResult(0, f"{TEST_IMAGE_ID}\n")]),
        tmp_path,
    )
    write_image_policy(tmp_path)

    with patch("scripts.audit_security.REQUIRED_CONTAINERS", frozenset()):
        assert not audit.image_check(
            FakeRunner([audit.CommandResult(0, f"{TEST_IMAGE_ID}\n")]),
            tmp_path,
        )

    for live in (
        audit.CommandResult(1, ""),
        audit.CommandResult(0, ""),
        audit.CommandResult(0, f"sha256:{'4' * 64}\n" * container_count),
    ):
        assert not audit.image_check(
            FakeRunner(
                [
                    audit.CommandResult(0, f"{TEST_IMAGE_ID}\n"),
                    live,
                ]
            ),
            tmp_path,
        )

        assert not audit.image_matches_policy(
            FakeRunner([audit.CommandResult(0, f"{TEST_IMAGE_ID}\n")]),
            first_image,
            [],
            "cache",
        )

    wrong_artifact = trivy_payload(artifact_name=f"sha256:{'5' * 64}")
    assert not audit.image_check(
        FakeRunner(
            [
                audit.CommandResult(0, f"{TEST_IMAGE_ID}\n"),
                audit.CommandResult(0, f"{TEST_IMAGE_ID}\n" * container_count),
                audit.CommandResult(0, wrong_artifact),
            ]
        ),
        tmp_path,
    )


def test_local_image_policy_accepts_new_identity_with_identical_security_evidence() -> None:
    """Allow a rebuilt local image when reviewed security evidence is unchanged.

    Uses different immutable image and artifact identifiers with the same vulnerability and secret
    snapshot, matching the supported clean setup that rebuilds project images from current source.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If local build identity churn is treated as security-policy drift.
    """
    expected = audit.vulnerability_policy_entry(
        trivy_payload(artifact_name=TEST_IMAGE_ID),
        image_id=TEST_IMAGE_ID,
    )
    rebuilt_image_id = f"sha256:{'6' * 64}"
    rebuilt_artifact_id = f"sha256:{'7' * 64}"
    scanned = audit.vulnerability_policy_entry(
        trivy_payload(
            artifact_name=rebuilt_image_id,
            artifact_id=rebuilt_artifact_id,
        ),
        image_id=rebuilt_image_id,
    )

    assert (
        audit.image_policy_drift_fields(
            "localforge/django:0.1.0",
            expected,
            scanned,
        )
        == []
    )


def test_local_image_policy_still_rejects_security_evidence_drift() -> None:
    """Reject vulnerability changes in a rebuilt local image.

    Changes both immutable identifiers and the normalized vulnerability snapshot, requiring the
    local-image exception to ignore identity fields only.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a changed vulnerability snapshot is accepted.
    """
    expected = audit.vulnerability_policy_entry(
        trivy_payload(artifact_name=TEST_IMAGE_ID),
        image_id=TEST_IMAGE_ID,
    )
    rebuilt_image_id = f"sha256:{'6' * 64}"
    scanned = audit.vulnerability_policy_entry(
        trivy_payload(1, artifact_name=rebuilt_image_id),
        image_id=rebuilt_image_id,
    )

    assert audit.image_policy_drift_fields(
        "localforge/django:0.1.0",
        expected,
        scanned,
    ) == ["digest", "finding_count", "vulnerability_ids"]


def test_http_observation_captures_status_redirect_and_body() -> None:
    """Capture one bounded response without following its redirect.

    Exercises status, location, body, connection closure, and local URL validation.
    Keeps the HTTP seam deterministic without a live listener.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If response evidence is lost.
    """

    class Response:
        """Provide one bounded HTTP response.

        Models the minimal `HTTPResponse` surface consumed by the audit.
        Inherits nothing and returns fixed redirect evidence.

        Attributes:
            status: HTTP status returned to the caller.

        Members:
            read: Return bounded response bytes.
            getheader: Return the redirect location.
        """

        status = HTTPStatus.FOUND

        def read(self, amount: int) -> bytes:
            """Return the response prefix.

            Confirms the caller requests the documented body limit.
            Returns a fixed login response marker.

            Arguments:
                amount: Maximum bytes requested.

            Returns:
                Fixed body bytes.

            Raises:
                AssertionError: If the body limit drifts.
            """
            assert amount == audit.HTTP_BODY_LIMIT_BYTES
            return b"login"

        def getheader(self, name: str, default: str = "") -> str:
            """Return one response header.

            Supports the location lookup used by the audit.
            Returns the supplied default for every other header.

            Arguments:
                name: Header name requested.
                default: Value returned for other headers.

            Returns:
                Redirect location or the supplied default.
            """
            return audit.PGADMIN_LOGIN_LOCATION if name == "Location" else default

    class Connection:
        """Provide one fabricated HTTP connection.

        Records request and close activity around the fixed response.
        Inherits nothing and performs no network operation.

        Attributes:
            requested: Whether a request was issued.
            closed: Whether the connection was closed.

        Members:
            request: Record the request.
            getresponse: Return the fabricated response.
            close: Record connection closure.
        """

        def __init__(self) -> None:
            """Create an unused open connection.

            Initializes request and closure observations independently.
            Carries no socket or external resource.

            Arguments:
                None.

            Returns:
                None.
            """
            self.requested = False
            self.closed = False

        def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
            """Record the exact local GET request.

            Validates method, path, and Host header used by the audit.
            Marks the request as issued.

            Arguments:
                method: HTTP method.
                path: Request path.
                headers: Request headers.

            Returns:
                None.

            Raises:
                AssertionError: If the request contract drifts.
            """
            assert (method, path, headers) == ("GET", "/browser/", {"Host": "localhost"})
            self.requested = True

        def getresponse(self) -> Response:
            """Return the fabricated response.

            Requires a request to precede response access.
            Returns one fixed response object.

            Arguments:
                None.

            Returns:
                Fabricated response.

            Raises:
                AssertionError: If called before request.
            """
            assert self.requested
            return Response()

        def close(self) -> None:
            """Record connection closure.

            Marks the in-memory connection as closed.
            Performs no external cleanup.

            Arguments:
                None.

            Returns:
                None.
            """
            self.closed = True

    connection = Connection()
    with patch.object(http.client, "HTTPConnection", return_value=connection):
        observation = audit.http_observation(audit.PGADMIN_URL)
        status = audit.http_status(audit.PGADMIN_URL)

    assert observation == audit.HttpObservation(
        HTTPStatus.FOUND,
        audit.PGADMIN_LOGIN_LOCATION,
        b"login",
    )
    assert status == HTTPStatus.FOUND
    assert connection.closed
    with pytest.raises(ValueError, match="local plaintext HTTP"):
        audit.http_observation("https://example.com/")


def test_port_is_closed_distinguishes_refusal_from_listener() -> None:
    """Recognize closed and accepting loopback ports.

    Models refusal, unreachable, accepted, timeout, reset, and unrelated failures.
    Confirms only explicit no-listener outcomes count as closed.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an accepting listener is called closed.
    """
    with patch.object(
        socket,
        "create_connection",
        side_effect=ConnectionRefusedError(errno.ECONNREFUSED, "refused"),
    ):
        assert audit.port_is_closed(1234)
    with patch.object(
        socket,
        "create_connection",
        side_effect=OSError(errno.EHOSTUNREACH, "unreachable"),
    ):
        assert audit.port_is_closed(1234)
    for failure in (
        TimeoutError(),
        ConnectionResetError(),
        OSError(errno.EACCES, "denied"),
    ):
        with patch.object(socket, "create_connection", side_effect=failure):
            assert not audit.port_is_closed(1234)
    with patch.object(socket, "create_connection", return_value=nullcontext()):
        assert not audit.port_is_closed(1234)


def test_runtime_exposure_check_requires_protection_ports_and_broker(tmp_path: Path) -> None:
    """Validate every runtime exposure boundary and RabbitMQ account.

    Covers protected endpoints, closed ports, broker failures, and account drift.
    Keeps the live checks deterministic through patched local boundaries.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If a boundary failure passes.
    """
    write_environment(tmp_path)
    broker = audit.CommandResult(0, '[{"user":"test-user","tags":["administrator"]}]')
    pgadmin = audit.HttpObservation(
        HTTPStatus.FOUND,
        audit.PGADMIN_LOGIN_LOCATION,
        b"",
    )

    with (
        patch.object(
            audit,
            "http_status",
            side_effect=[
                next(iter(accepted)) for _, accepted in audit.PROTECTED_ENDPOINTS.values()
            ],
        ),
        patch.object(audit, "http_observation", return_value=pgadmin),
        patch.object(audit, "port_is_closed", return_value=True),
    ):
        assert audit.runtime_exposure_check(FakeRunner([broker]), tmp_path)

    with patch.object(audit, "http_status", return_value=HTTPStatus.OK):
        assert not audit.runtime_exposure_check(FakeRunner([broker]), tmp_path)
    with (
        patch.object(
            audit,
            "http_status",
            side_effect=[
                next(iter(accepted)) for _, accepted in audit.PROTECTED_ENDPOINTS.values()
            ],
        ),
        patch.object(audit, "http_observation", return_value=pgadmin),
        patch.object(audit, "port_is_closed", return_value=False),
    ):
        assert not audit.runtime_exposure_check(FakeRunner([broker]), tmp_path)
    with patch.object(audit, "http_status", side_effect=OSError("down")):
        assert not audit.runtime_exposure_check(FakeRunner([broker]), tmp_path)

    for observation in (
        audit.HttpObservation(HTTPStatus.OK, "", b"login"),
        audit.HttpObservation(HTTPStatus.FOUND, "/wrong", b""),
        audit.HttpObservation(HTTPStatus.FOUND, audit.PGADMIN_LOGIN_LOCATION, b"database"),
    ):
        with (
            patch.object(
                audit,
                "http_status",
                side_effect=[
                    next(iter(accepted)) for _, accepted in audit.PROTECTED_ENDPOINTS.values()
                ],
            ),
            patch.object(audit, "http_observation", return_value=observation),
        ):
            assert not audit.runtime_exposure_check(FakeRunner([broker]), tmp_path)
    with (
        patch.object(
            audit,
            "http_status",
            side_effect=[
                next(iter(accepted)) for _, accepted in audit.PROTECTED_ENDPOINTS.values()
            ],
        ),
        patch.object(audit, "http_observation", side_effect=OSError("down")),
    ):
        assert not audit.runtime_exposure_check(FakeRunner([broker]), tmp_path)

    for result in (
        audit.CommandResult(1, "[]"),
        audit.CommandResult(0, "invalid"),
        audit.CommandResult(0, "{}"),
        audit.CommandResult(0, '[{"user":"guest"}]'),
    ):
        with (
            patch.object(
                audit,
                "http_status",
                side_effect=[
                    next(iter(accepted)) for _, accepted in audit.PROTECTED_ENDPOINTS.values()
                ],
            ),
            patch.object(audit, "http_observation", return_value=pgadmin),
            patch.object(audit, "port_is_closed", return_value=True),
        ):
            assert not audit.runtime_exposure_check(FakeRunner([result]), tmp_path)


def test_runtime_secret_check_rejects_command_and_secret_failures(tmp_path: Path) -> None:
    """Scan image history and logs without exposing generated values.

    Covers clean evidence, command failure, and an exact credential occurrence.
    Confirms every unique image and required container is inspected.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If a failed command or leaked value passes.
    """
    write_environment(tmp_path)
    command_count = len(set(platform.CONTAINER_IMAGES.values())) + len(platform.REQUIRED_CONTAINERS)
    clean = FakeRunner([audit.CommandResult(0, "ordinary output") for _ in range(command_count)])

    values = audit.generated_sensitive_values(tmp_path)
    assert "database-secret-value" in values
    assert "amqp://user:broker-secret-value@broker/vhost" in values
    assert "development-tunnel-token" in values
    assert "development-resend-key" in values
    assert audit.runtime_secret_check(clean, tmp_path)
    log_commands = [call[0] for call in clean.calls if call[0][0:2] == ("docker", "logs")]
    assert all("--since" not in command for command in log_commands)

    assert not audit.runtime_secret_check(
        FakeRunner([audit.CommandResult(1, "")]),
        tmp_path,
    )
    assert not audit.runtime_secret_check(
        FakeRunner([audit.CommandResult(0, "database-secret-value")]),
        tmp_path,
    )
    assert not audit.runtime_secret_check(
        FakeRunner([audit.CommandResult(0, "development-tunnel-token")]),
        tmp_path,
    )
    assert not audit.runtime_secret_check(
        FakeRunner([audit.CommandResult(0, "development-resend-key")]),
        tmp_path,
    )


@pytest.mark.parametrize(
    ("scope", "expected_calls"),
    [
        ("deployment", ["deployment"]),
        ("history", ["history"]),
        ("dependencies", ["dependencies"]),
        ("images", ["images"]),
        ("runtime", ["exposure", "secrets"]),
        ("all", ["deployment", "history", "dependencies", "images", "exposure", "secrets"]),
    ],
)
def test_main_runs_selected_checks(
    tmp_path: Path,
    scope: str,
    expected_calls: list[str],
) -> None:
    """Dispatch one or all audit scopes.

    Replaces every check with an ordered recorder.
    Confirms each CLI scope selects only its intended checks.

    Arguments:
        tmp_path: Temporary repository root.
        scope: Requested command scope.
        expected_calls: Check functions expected in order.

    Returns:
        None.

    Raises:
        AssertionError: If dispatch or success status is wrong.
    """
    observed: list[str] = []

    def record(name: str) -> bool:
        """Record one dispatched audit check.

        Appends the stable check name to the observed sequence.
        Always reports success so dispatch can continue.

        Arguments:
            name: Audit check name.

        Returns:
            True after recording the name.
        """
        observed.append(name)
        return True

    with (
        patch.object(audit, "deployment_check", side_effect=lambda *_args: record("deployment")),
        patch.object(audit, "history_secret_check", side_effect=lambda *_args: record("history")),
        patch.object(audit, "dependency_check", side_effect=lambda *_args: record("dependencies")),
        patch.object(audit, "image_check", side_effect=lambda *_args: record("images")),
        patch.object(
            audit,
            "runtime_exposure_check",
            side_effect=lambda *_args: record("exposure"),
        ),
        patch.object(
            audit,
            "runtime_secret_check",
            side_effect=lambda *_args: record("secrets"),
        ),
    ):
        assert audit.main(["--scope", scope], runner=FakeRunner(), root=tmp_path) == 0

    assert observed == expected_calls


def test_main_reports_failed_scope_without_running_runtime_secret_check(tmp_path: Path) -> None:
    """Return failure and preserve runtime short-circuiting.

    Makes the exposure check fail before secret inspection.
    Confirms the CLI reports failure without running the dependent check.

    Arguments:
        tmp_path: Temporary repository root.

    Returns:
        None.

    Raises:
        AssertionError: If failure is hidden or the dependent secret check runs.
    """
    with (
        patch.object(audit, "runtime_exposure_check", return_value=False),
        patch.object(audit, "runtime_secret_check") as secret_check,
    ):
        assert audit.main(["--scope", "runtime"], runner=FakeRunner(), root=tmp_path) == 1

    secret_check.assert_not_called()


def test_module_entrypoint_runs_deployment_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """Execute the module guard against the bounded deployment check.

    Re-runs the module under its command-line name with safe arguments.
    Confirms the guard returns the successful main status.

    Arguments:
        monkeypatch: Fixture replacing command arguments.

    Returns:
        None.

    Raises:
        AssertionError: If the module guard does not return success.
    """
    monkeypatch.setattr(sys, "argv", ["audit_security.py", "--scope", "deployment"])
    monkeypatch.delitem(sys.modules, "scripts.audit_security")

    with pytest.raises(SystemExit) as failure:
        runpy.run_module("scripts.audit_security", run_name="__main__")

    assert failure.value.code == 0
