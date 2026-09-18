"""Unit tests for the complete published API contract.

Generates the public OpenAPI document without network access and checks fixed routes, security,
status evidence, response examples, deterministic output, and the planned WebSocket companion.
"""

from __future__ import annotations

import subprocess
import sys
from io import StringIO
from pathlib import Path
from typing import Any, Final, cast

import pytest
import yaml
from django.core.management import call_command
from django.urls import URLPattern, URLResolver
from jsonschema.validators import Draft202012Validator  # type: ignore[import-untyped]

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
OPENAPI_ARTIFACT: Final = REPOSITORY_ROOT / "docs" / "api" / "openapi-v1.yaml"
WEBSOCKET_ARTIFACT: Final = REPOSITORY_ROOT / "docs" / "api" / "websocket-v1.md"
PUBLIC_SECURITY: Final[list[dict[str, list[str]]]] = []
DUAL_TOKEN_SECURITY: Final[list[dict[str, list[str]]]] = [
    {"jwtAuth": []},
    {"tokenAuth": []},
]
TOKEN_ONLY_SECURITY: Final[list[dict[str, list[str]]]] = [{"tokenAuth": []}]
HEALTH_SECURITY: Final[list[dict[str, list[str]]]] = [{"cookieAuth": []}, {}]

OPERATION_CONTRACT: Final = {
    ("post", "/api/v1/jwt/create/"): (
        "jwt_create",
        PUBLIC_SECURITY,
        {"200", "400", "401", "405", "406", "413", "415", "429", "500", "503"},
        "tests/integration/accounts/test_jwt_authentication.py",
        "test_jwt_create_observed_statuses_exactly_match_its_documented_contract",
    ),
    ("post", "/api/v1/jwt/refresh/"): (
        "jwt_refresh",
        PUBLIC_SECURITY,
        {"200", "400", "401", "405", "406", "413", "415", "429", "500", "503"},
        "tests/integration/accounts/test_jwt_authentication.py",
        "test_jwt_token_operation_statuses_match_their_documented_contracts[refresh]",
    ),
    ("post", "/api/v1/jwt/verify/"): (
        "jwt_verify",
        PUBLIC_SECURITY,
        {"200", "400", "401", "405", "406", "413", "415", "429", "500", "503"},
        "tests/integration/accounts/test_jwt_authentication.py",
        "test_jwt_token_operation_statuses_match_their_documented_contracts[verify]",
    ),
    ("post", "/api/v1/token/login/"): (
        "token_login",
        PUBLIC_SECURITY,
        {"200", "400", "401", "405", "406", "413", "415", "429", "500", "503"},
        "tests/integration/accounts/test_token_authentication.py",
        "test_login_observed_statuses_exactly_match_its_documented_contract",
    ),
    ("post", "/api/v1/token/logout/"): (
        "token_logout",
        TOKEN_ONLY_SECURITY,
        {"204", "400", "401", "405", "406", "413", "429", "500", "503"},
        "tests/integration/accounts/test_token_authentication.py",
        "test_logout_observed_statuses_exactly_match_its_documented_contract",
    ),
    ("post", "/api/v1/users/"): (
        "user_registration_or_activation",
        PUBLIC_SECURITY,
        {"201", "204", "400", "405", "406", "413", "415", "429", "500", "503"},
        "tests/integration/accounts/test_user_profiles.py",
        "test_registration_observed_statuses_exactly_match_its_documented_contract",
    ),
    ("get", "/api/v1/users/me/"): (
        "user_profile_retrieve",
        DUAL_TOKEN_SECURITY,
        {"200", "400", "401", "405", "406", "413", "429", "500", "503"},
        "tests/integration/accounts/test_user_profiles.py",
        "test_profile_observed_statuses_exactly_match_each_documented_contract[get]",
    ),
    ("head", "/api/v1/users/me/"): (
        "user_profile_head",
        DUAL_TOKEN_SECURITY,
        {"200", "400", "401", "406", "413", "429", "500", "503"},
        "tests/integration/accounts/test_user_profiles.py",
        "test_profile_head_observed_statuses_exactly_match_its_documented_contract",
    ),
    ("patch", "/api/v1/users/me/"): (
        "user_profile_update",
        DUAL_TOKEN_SECURITY,
        {"200", "400", "401", "405", "406", "413", "415", "429", "500", "503"},
        "tests/integration/accounts/test_user_profiles.py",
        "test_profile_observed_statuses_exactly_match_each_documented_contract[patch]",
    ),
    ("delete", "/api/v1/users/me/"): (
        "user_profile_delete",
        DUAL_TOKEN_SECURITY,
        {"204", "400", "401", "405", "406", "413", "415", "429", "500", "503"},
        "tests/integration/accounts/test_user_profiles.py",
        "test_profile_observed_statuses_exactly_match_each_documented_contract[delete]",
    ),
    ("post", "/api/v1/users/resend_activation/"): (
        "user_activation_resend",
        PUBLIC_SECURITY,
        {"202", "400", "405", "406", "413", "415", "429", "500", "503"},
        "tests/integration/accounts/test_account_activation.py",
        "test_resend_observed_statuses_exactly_match_its_documented_contract",
    ),
    ("post", "/api/v1/users/reset_password/"): (
        "user_password_reset_request",
        PUBLIC_SECURITY,
        {"202", "400", "405", "406", "413", "415", "429", "500", "503"},
        "tests/integration/accounts/test_password_management.py",
        (
            "test_password_operation_observed_statuses_exactly_match_its_documented_contract"
            "[reset-password]"
        ),
    ),
    ("post", "/api/v1/users/reset_password_confirm/"): (
        "user_password_reset_confirm",
        PUBLIC_SECURITY,
        {"204", "400", "405", "406", "413", "415", "429", "500", "503"},
        "tests/integration/accounts/test_password_management.py",
        (
            "test_password_operation_observed_statuses_exactly_match_its_documented_contract"
            "[reset-password-confirm]"
        ),
    ),
    ("post", "/api/v1/users/reset_username/"): (
        "user_username_reset_request",
        PUBLIC_SECURITY,
        {"202", "400", "405", "406", "413", "415", "429", "500", "503"},
        "tests/integration/accounts/test_username_management.py",
        (
            "test_username_operation_observed_statuses_exactly_match_its_documented_contract"
            "[reset-username]"
        ),
    ),
    ("post", "/api/v1/users/reset_username_confirm/"): (
        "user_username_reset_confirm",
        PUBLIC_SECURITY,
        {"204", "400", "405", "406", "413", "415", "429", "500", "503"},
        "tests/integration/accounts/test_username_management.py",
        (
            "test_username_operation_observed_statuses_exactly_match_its_documented_contract"
            "[reset-username-confirm]"
        ),
    ),
    ("post", "/api/v1/users/set_password/"): (
        "user_password_change",
        DUAL_TOKEN_SECURITY,
        {"204", "400", "401", "405", "406", "413", "415", "429", "500", "503"},
        "tests/integration/accounts/test_password_management.py",
        (
            "test_password_operation_observed_statuses_exactly_match_its_documented_contract"
            "[set-password]"
        ),
    ),
    ("post", "/api/v1/users/set_username/"): (
        "user_username_change",
        DUAL_TOKEN_SECURITY,
        {"204", "400", "401", "405", "406", "413", "415", "429", "500", "503"},
        "tests/integration/accounts/test_username_management.py",
        (
            "test_username_operation_observed_statuses_exactly_match_its_documented_contract"
            "[set-username]"
        ),
    ),
    ("get", "/health/"): (
        "health_readiness",
        HEALTH_SECURITY,
        {"200", "400", "405", "406", "500", "503"},
        "tests/integration/config/test_readiness.py",
        "test_health_observed_statuses_exactly_match_its_documented_contract",
    ),
    ("head", "/health/"): (
        "health_readiness_head",
        HEALTH_SECURITY,
        {"200", "400", "406", "500", "503"},
        "tests/integration/config/test_readiness.py",
        "test_health_head_observed_statuses_exactly_match_its_documented_contract",
    ),
}

BODYLESS_OPERATIONS: Final = {
    ("get", "/api/v1/users/me/"),
    ("head", "/api/v1/users/me/"),
    ("post", "/api/v1/token/logout/"),
    ("get", "/health/"),
    ("head", "/health/"),
}


def _render_openapi() -> str:
    """Generate one validated warning-free OpenAPI document in memory.

    Runs the public management command contract so tests and artifact generation use the same
    renderer and validation behavior.

    Arguments:
        None.

    Returns:
        Rendered OpenAPI YAML.

    Raises:
        CommandError: If generation warns or validation fails.
    """
    output = StringIO()
    errors = StringIO()
    call_command(
        "spectacular",
        format="openapi",
        validate=True,
        fail_on_warn=True,
        stdout=output,
        stderr=errors,
        verbosity=0,
    )
    assert errors.getvalue() == ""

    return output.getvalue()


def _operation(
    schema: dict[str, Any],
    method: str,
    route: str,
) -> dict[str, Any]:
    """Read one operation from a generated schema.

    Selects the exact route and lowercase method named by the fixed contract.
    Keeps assertions independent of the schema generator's internal objects.

    Arguments:
        schema: Parsed OpenAPI document.
        method: Lowercase HTTP method.
        route: Absolute public route.

    Returns:
        Operation mapping.

    Raises:
        KeyError: If the route or method is absent.
    """
    paths = cast("dict[str, Any]", schema["paths"])
    path_item = cast("dict[str, Any]", paths[route])

    return cast("dict[str, Any]", path_item[method])


def _evidence_node_id(relative_path: str, test_name: str) -> str:
    """Build one repository-relative public pytest node identifier.

    Preserves exact parameter case suffixes so one operation cannot cite another case from the
    same parameterized runtime contract test.

    Arguments:
        relative_path: Repository-relative Python test module.
        test_name: Public test function and optional parameter case suffix.

    Returns:
        Exact pytest node identifier.
    """
    return f"{relative_path}::{test_name}"


def _runtime_operations() -> set[tuple[str, str]]:
    """Discover real operations from the current fixed route table.

    Reads the mounted account URL patterns and health view methods directly, so a newly exposed
    route cannot disappear from OpenAPI merely by being omitted from the expected schema mapping.

    Arguments:
        None.

    Returns:
        Lowercase method and absolute path pairs exposed at runtime.

    Raises:
        AssertionError: If the versioned API resolver has an unexpected shape.
    """
    api_module = __import__("config.api", fromlist=["urlpatterns"])
    api_patterns = cast("list[URLPattern | URLResolver]", api_module.urlpatterns)
    assert len(api_patterns) == 1
    accounts_resolver = api_patterns[0]
    assert isinstance(accounts_resolver, URLResolver)
    operations: set[tuple[str, str]] = set()
    for pattern in accounts_resolver.url_patterns:
        assert isinstance(pattern, URLPattern)
        view_class = cast("Any", pattern.callback).view_class
        route = f"/api/v1/{pattern.pattern}"
        operations.update(
            (method, route)
            for method in ("get", "post", "put", "patch", "delete")
            if hasattr(view_class, method)
        )
        if hasattr(view_class, "get"):
            operations.add(("head", route))
    operations.add(("get", "/health/"))
    operations.add(("head", "/health/"))

    return operations


@pytest.mark.unit
def test_openapi_contract_is_complete_and_bound_to_observed_evidence() -> None:
    """Match every generated operation to the fixed executable contract.

    Verifies exact routes, stable identifiers, descriptions, request boundaries, accepted
    authentication, complete statuses, examples, and durable public-test evidence.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If generated documentation or its observed evidence is incomplete.
    """
    schema = cast("dict[str, Any]", yaml.safe_load(_render_openapi()))
    generated_operations = {
        (method, route)
        for route, path_item in cast("dict[str, dict[str, Any]]", schema["paths"]).items()
        for method in path_item
    }

    assert generated_operations == _runtime_operations() == set(OPERATION_CONTRACT)
    assert schema["openapi"] == "3.1.0"
    assert schema["info"] == {
        "title": "LocalForge API",
        "version": "1.0.0",
        "description": (
            "The fixed LocalForge health and versioned REST API contract. "
            "WebSocket protocol details are published separately."
        ),
    }
    security_schemes = cast(
        "dict[str, Any]",
        cast("dict[str, Any]", schema["components"])["securitySchemes"],
    )
    assert security_schemes["jwtAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    assert security_schemes["tokenAuth"] == {
        "type": "apiKey",
        "in": "header",
        "name": "Authorization",
        "description": "Send `Authorization: Token <key>`.",
    }
    operation_ids: set[str] = set()

    for (method, route), contract in OPERATION_CONTRACT.items():
        operation_id, security, statuses, evidence_file, evidence_test = contract
        operation = _operation(schema, method, route)
        responses = cast("dict[str, Any]", operation["responses"])

        assert operation["operationId"] == operation_id
        operation_ids.add(cast("str", operation["operationId"]))
        assert cast("str", operation["summary"]).strip()
        assert cast("str", operation["description"]).strip()
        assert operation["security"] == security
        assert set(responses) == statuses
        assert ("requestBody" not in operation) == ((method, route) in BODYLESS_OPERATIONS)
        operation_evidence = _evidence_node_id(evidence_file, evidence_test)
        assert operation["x-localforge-observed-by"] == operation_evidence

        for status, response in responses.items():
            response_document = cast("dict[str, Any]", response)
            assert cast("str", response_document["description"]).strip()
            observed_by = cast("list[str]", response_document["x-localforge-observed-by"])
            assert observed_by[0] == operation_evidence
            assert all("::test_" in reference for reference in observed_by)
            if status == "204" or method == "head":
                assert "content" not in response_document
                assert response_document["x-localforge-examples"] == {
                    "NoContent": {
                        "summary": (
                            "Response with no response body."
                            if method == "head"
                            else "Successful response with no response body."
                        ),
                        "value": None,
                    }
                }
            else:
                content = cast("dict[str, Any]", response_document["content"])
                examples = cast(
                    "dict[str, Any]",
                    cast("dict[str, Any]", content["application/json"])["examples"],
                )
                assert examples

        if "429" in responses:
            throttle_headers = cast("dict[str, Any]", responses["429"])["headers"]
            assert set(cast("dict[str, Any]", throttle_headers)) == {
                "Retry-After",
                "X-Request-ID",
            }

        invalid_host_examples = (
            cast(
                "dict[str, Any]",
                responses["400"]["x-localforge-representation-examples"],
            )
            if method == "head"
            else cast(
                "dict[str, Any]",
                cast(
                    "dict[str, Any]",
                    responses["400"]["content"],
                )["application/json"]["examples"],
            )
        )
        invalid_host = cast("dict[str, Any]", invalid_host_examples["InvalidHost"]["value"])
        assert invalid_host == {
            "code": "bad_request",
            "message": "The request was invalid.",
            "details": {},
            "request_id": "00000000-0000-4000-8000-000000000000",
        }
        assert (
            "tests/integration/config/test_api.py::"
            f"test_fixed_operation_invalid_host_returns_correlated_bad_request[{operation_id}]"
            in responses["400"]["x-localforge-observed-by"]
        )

        if "400" in responses and operation_id not in {
            "health_readiness",
            "health_readiness_head",
            "token_logout",
            "user_profile_head",
            "user_profile_retrieve",
        }:
            validation_examples = cast(
                "dict[str, Any]",
                cast(
                    "dict[str, Any]",
                    cast("dict[str, Any]", responses["400"])["content"],
                )["application/json"]["examples"],
            )
            assert any(
                cast("dict[str, Any]", example["value"])["code"] == "validation_error"
                and cast("dict[str, Any]", example["value"])["details"]
                for example in validation_examples.values()
            )

    assert len(operation_ids) == len(OPERATION_CONTRACT)


@pytest.mark.unit
def test_openapi_documents_boundary_only_statuses_with_observed_evidence() -> None:
    """Document routing and CSRF responses that have no real operation object.

    Checks the root extension for exact status-bound examples and test references, preserving
    independent evidence for middleware behavior a per-view schema cannot represent.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If boundary-only response documentation is missing or stale.
    """
    schema = cast("dict[str, Any]", yaml.safe_load(_render_openapi()))
    boundary = cast("dict[str, Any]", schema["x-localforge-boundary-responses"])
    expected = {
        "204": (
            "tests/integration/config/test_security.py",
            "test_allowed_origin_preflight_bypasses_credentials_without_wildcards",
        ),
        "403": (
            "tests/integration/config/test_api.py",
            "test_session_authenticated_csrf_failure_uses_middleware_backed_authentication",
        ),
        "404": (
            "tests/integration/config/test_api.py",
            "test_unknown_path_returns_a_correlated_json_envelope",
        ),
    }

    assert set(boundary) == set(expected)
    for status, (evidence_file, evidence_test) in expected.items():
        response = cast("dict[str, Any]", boundary[status])
        if status == "204":
            assert "content" not in response
            assert response["x-localforge-examples"] == {
                "NoContent": {
                    "summary": "Allowed CORS preflight response with no response body.",
                    "value": None,
                }
            }
            assert response["x-localforge-observed-by"] == _evidence_node_id(
                evidence_file,
                evidence_test,
            )
            continue

        examples = cast(
            "dict[str, Any]",
            cast("dict[str, Any]", response["content"])["application/json"]["examples"],
        )

        assert examples
        assert response["x-localforge-observed-by"] == _evidence_node_id(
            evidence_file,
            evidence_test,
        )


@pytest.mark.unit
def test_every_published_evidence_node_id_collects() -> None:
    """Collect every executable test cited by the generated public contract.

    Invokes pytest collection on exact node identifiers, including parameter cases, so a renamed,
    missing, unimportable, or mistyped evidence reference fails before the artifact can publish it.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any cited public test node cannot be collected.
        subprocess.TimeoutExpired: If collection exceeds its bounded duration.
    """
    schema = cast("dict[str, Any]", yaml.safe_load(_render_openapi()))
    references = {
        cast("str", response["x-localforge-observed-by"])
        for response in cast(
            "dict[str, dict[str, Any]]",
            schema["x-localforge-boundary-responses"],
        ).values()
    }
    for path_item in cast("dict[str, dict[str, Any]]", schema["paths"]).values():
        for operation in path_item.values():
            references.add(cast("str", operation["x-localforge-observed-by"]))
            for response in cast("dict[str, dict[str, Any]]", operation["responses"]).values():
                references.update(cast("list[str]", response["x-localforge-observed-by"]))

    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "--no-cov",
            "-q",
            *sorted(references),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert all(reference in result.stdout for reference in references)


@pytest.mark.unit
def test_enumeration_resistant_operations_explain_their_public_semantics() -> None:
    """Keep non-enumerating outcomes explicit for client integrators.

    Searches the exact operations whose accepted or rejected response deliberately hides account
    existence and requires the generated prose to state that semantic.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an enumeration-resistant contract becomes ambiguous.
    """
    schema = cast("dict[str, Any]", yaml.safe_load(_render_openapi()))
    requirements = {
        ("post", "/api/v1/users/"): ("same status and body", "already occupied"),
        ("post", "/api/v1/users/resend_activation/"): ("unknown", "active"),
        ("post", "/api/v1/users/reset_password/"): ("whether", "matches"),
        ("post", "/api/v1/users/reset_username/"): ("whether", "matches"),
        ("post", "/api/v1/token/login/"): ("without revealing", "exists"),
        ("post", "/api/v1/jwt/create/"): ("without revealing", "exists"),
    }

    for (method, route), phrases in requirements.items():
        description = cast("str", _operation(schema, method, route)["description"]).lower()

        assert all(phrase in description for phrase in phrases)


@pytest.mark.unit
def test_request_components_reject_undeclared_properties() -> None:
    """Publish every strict runtime request branch as a closed object schema.

    Follows each request component used by the fixed operations, including both registration
    one-of branches, and rejects an undeclared nonsense field with the standard JSON Schema engine.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a strict runtime request becomes open in the public contract.
    """
    schema = cast("dict[str, Any]", yaml.safe_load(_render_openapi()))
    components = cast(
        "dict[str, dict[str, Any]]",
        cast("dict[str, Any]", schema["components"])["schemas"],
    )
    expected = {
        "ActivationConfirmation",
        "ActivationResend",
        "JWTRefreshRequest",
        "JWTVerifyRequest",
        "PasswordChange",
        "PasswordResetConfirm",
        "PasswordResetRequest",
        "PatchedUserProfileUpdate",
        "Registration",
        "TokenLogin",
        "UserProfileDeletion",
        "UsernameChange",
        "UsernameResetConfirm",
        "UsernameResetRequest",
    }
    request_components = {
        cast("str", media["schema"]["$ref"]).rsplit("/", maxsplit=1)[-1]
        for path_item in cast("dict[str, dict[str, Any]]", schema["paths"]).values()
        for operation in path_item.values()
        for media in cast(
            "dict[str, dict[str, Any]]",
            operation.get("requestBody", {}).get("content", {}),
        ).values()
    }
    registration_union = components["UserRegistrationOrActivation"]
    request_components.remove("UserRegistrationOrActivation")
    request_components.update(
        branch["$ref"].rsplit("/", maxsplit=1)[-1]
        for branch in cast("list[dict[str, str]]", registration_union["oneOf"])
    )

    assert request_components == expected
    for component_name in request_components:
        component_document = {
            "$ref": f"#/components/schemas/{component_name}",
            "components": schema["components"],
        }
        validator = Draft202012Validator(component_document)

        assert components[component_name]["additionalProperties"] is False
        assert not validator.is_valid({"nonsense": True})


@pytest.mark.unit
def test_health_components_define_exact_public_and_staff_representations() -> None:
    """Publish machine-readable health success and error schemas.

    Validates the public and staff readiness branches with JSON Schema, requires every
    dependency field and bounded diagnostic member, and proves arbitrary objects match neither
    health nor error.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If health schemas become incomplete, open, or disconnected from responses.
    """
    schema = cast("dict[str, Any]", yaml.safe_load(_render_openapi()))
    components = cast(
        "dict[str, dict[str, Any]]",
        cast("dict[str, Any]", schema["components"])["schemas"],
    )
    health = _operation(schema, "get", "/health/")
    responses = cast("dict[str, dict[str, Any]]", health["responses"])
    ready_reference = {"$ref": "#/components/schemas/HealthReady"}
    not_ready_reference = {"$ref": "#/components/schemas/HealthNotReady"}
    error_reference = {"$ref": "#/components/schemas/ErrorEnvelope"}

    assert responses["200"]["content"]["application/json"]["schema"] == ready_reference
    assert responses["503"]["content"]["application/json"]["schema"] == not_ready_reference
    for status in ("400", "405", "406", "500"):
        assert responses[status]["content"]["application/json"]["schema"] == error_reference

    ready_checks = components["HealthReadyChecks"]
    not_ready_checks = components["HealthNotReadyChecks"]
    ready_details = components["HealthReadyDetails"]
    not_ready_details = components["HealthNotReadyDetails"]
    diagnostic = components["HealthDiagnostic"]
    expected_checks = {
        "broker",
        "cache",
        "channel_layer",
        "database_primary",
        "database_replica",
        "mail",
        "object_storage",
    }

    for checks in (ready_checks, not_ready_checks):
        assert set(checks["properties"]) == expected_checks
        assert set(checks["required"]) == expected_checks
        assert checks["additionalProperties"] is False
    for details in (ready_details, not_ready_details):
        assert set(details["properties"]) == expected_checks
        assert set(details["required"]) == expected_checks
        assert details["additionalProperties"] is False
    assert diagnostic["oneOf"] == [
        {"$ref": "#/components/schemas/HealthWorkingDiagnostic"},
        {"$ref": "#/components/schemas/HealthUnavailableDiagnostic"},
    ]
    coupling_reference = {
        "$ref": "#/components/schemas/HealthStaffStatusCoupling",
    }
    assert components["HealthReadyStaff"]["allOf"] == [coupling_reference]
    assert components["HealthNotReadyStaff"]["allOf"] == [coupling_reference]
    assert len(components["HealthStaffStatusCoupling"]["allOf"]) == len(expected_checks) * 2

    ready_validator = Draft202012Validator(
        {
            "$ref": "#/components/schemas/HealthReady",
            "components": schema["components"],
        }
    )
    not_ready_validator = Draft202012Validator(
        {
            "$ref": "#/components/schemas/HealthNotReady",
            "components": schema["components"],
        }
    )
    error_validator = Draft202012Validator(
        {
            "$ref": "#/components/schemas/ErrorEnvelope",
            "components": schema["components"],
        }
    )
    ready_example = responses["200"]["content"]["application/json"]["examples"]["Ready"]["value"]
    not_ready_example = responses["503"]["content"]["application/json"]["examples"]["NotReady"][
        "value"
    ]
    staff_ready_example = {
        **ready_example,
        "details": {
            name: {
                "status": "working",
                "duration_ms": 1.25,
                "error": None,
            }
            for name in expected_checks
        },
    }
    staff_not_ready_example = {
        **not_ready_example,
        "details": {
            name: {
                "status": not_ready_example["checks"][name],
                "duration_ms": 1.25,
                "error": (
                    "dependency_unavailable"
                    if not_ready_example["checks"][name] == "unavailable"
                    else None
                ),
            }
            for name in expected_checks
        },
    }
    mismatched_cache_example = {
        **staff_not_ready_example,
        "checks": {
            **staff_not_ready_example["checks"],
            "broker": "unavailable",
        },
        "details": {
            **staff_not_ready_example["details"],
            "broker": {
                "status": "unavailable",
                "duration_ms": 1.25,
                "error": "dependency_unavailable",
            },
            "cache": {
                "status": "working",
                "duration_ms": 1.25,
                "error": None,
            },
        },
    }
    mismatched_broker_example = {
        **staff_not_ready_example,
        "details": {
            **staff_not_ready_example["details"],
            "broker": {
                "status": "unavailable",
                "duration_ms": 1.25,
                "error": "dependency_unavailable",
            },
        },
    }

    assert ready_validator.is_valid(ready_example)
    assert ready_validator.is_valid(staff_ready_example)
    assert not_ready_validator.is_valid(not_ready_example)
    assert not_ready_validator.is_valid(staff_not_ready_example)
    assert not not_ready_validator.is_valid(mismatched_cache_example)
    assert not not_ready_validator.is_valid(mismatched_broker_example)
    assert not ready_validator.is_valid(not_ready_example)
    assert not ready_validator.is_valid(
        {
            **ready_example,
            "checks": {
                **ready_example["checks"],
                "cache": "unavailable",
            },
        }
    )
    assert not not_ready_validator.is_valid(ready_example)
    assert not not_ready_validator.is_valid(
        {
            **not_ready_example,
            "checks": dict.fromkeys(expected_checks, "working"),
        }
    )
    assert not ready_validator.is_valid({"nonsense": True})
    assert not ready_validator.is_valid({**ready_example, "nonsense": True})
    assert not error_validator.is_valid({"nonsense": True})


@pytest.mark.unit
def test_error_envelope_details_and_examples_match_runtime_validation_shape() -> None:
    """Constrain validation details and validate every published envelope example.

    Requires an empty mapping or field-keyed non-empty string arrays, rejects scalar and nested
    field values, and checks operation, HEAD representation, and boundary examples against the
    shared component.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the component accepts impossible details or an example violates it.
    """
    schema = cast("dict[str, Any]", yaml.safe_load(_render_openapi()))
    components = cast(
        "dict[str, dict[str, Any]]",
        cast("dict[str, Any]", schema["components"])["schemas"],
    )
    validator = Draft202012Validator(
        {
            "$ref": "#/components/schemas/ErrorEnvelope",
            "components": schema["components"],
        }
    )
    base = {
        "code": "validation_error",
        "message": "The submitted data was invalid.",
        "request_id": "00000000-0000-4000-8000-000000000000",
    }

    assert validator.is_valid({**base, "details": {}})
    assert validator.is_valid({**base, "details": {"email": ["This field is required."]}})
    invalid_details_cases: tuple[dict[str, object], ...] = (
        {"email": True},
        {"email": "This field is required."},
        {"email": 1},
        {"email": {"nested": ["This field is required."]}},
        {"email": []},
        {"email": [""]},
    )
    for invalid_details in invalid_details_cases:
        assert not validator.is_valid({**base, "details": invalid_details})

    error_reference = "#/components/schemas/ErrorEnvelope"
    examples: list[dict[str, object]] = []
    for path_item in cast("dict[str, dict[str, Any]]", schema["paths"]).values():
        for operation in path_item.values():
            for response in cast("dict[str, dict[str, Any]]", operation["responses"]).values():
                content = cast("dict[str, Any]", response.get("content", {}))
                media = cast("dict[str, Any]", content.get("application/json", {}))
                response_schema = cast("dict[str, str]", media.get("schema", {}))
                if response_schema.get("$ref") == error_reference:
                    examples.extend(
                        cast("dict[str, object]", example["value"])
                        for example in cast(
                            "dict[str, dict[str, object]]", media["examples"]
                        ).values()
                    )
                examples.extend(
                    cast("dict[str, object]", example["value"])
                    for example in cast(
                        "dict[str, dict[str, object]]",
                        response.get("x-localforge-representation-examples", {}),
                    ).values()
                )
    for response in cast(
        "dict[str, dict[str, Any]]",
        schema["x-localforge-boundary-responses"],
    ).values():
        content = cast("dict[str, Any]", response.get("content", {}))
        media = cast("dict[str, Any]", content.get("application/json", {}))
        examples.extend(
            cast("dict[str, object]", example["value"])
            for example in cast(
                "dict[str, dict[str, object]]",
                media.get("examples", {}),
            ).values()
        )

    assert examples
    assert all(validator.is_valid(example) for example in examples)
    details = cast("dict[str, Any]", components["ErrorEnvelope"]["properties"])["details"]
    assert details["additionalProperties"] == {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "minItems": 1,
    }


@pytest.mark.unit
def test_committed_openapi_artifact_is_deterministic_and_current() -> None:
    """Keep the reviewable generated artifact byte-for-byte current.

    Generates the validated warning-free document twice and compares both results with the
    committed version, failing the build on nondeterminism or stale output.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If generation is nondeterministic or the artifact is stale.
    """
    first = _render_openapi()
    second = _render_openapi()

    assert first == second
    assert OPENAPI_ARTIFACT.read_text(encoding="utf-8") == first


@pytest.mark.unit
def test_websocket_contract_is_versioned_and_explicitly_planned() -> None:
    """Publish the future socket contract without claiming Phase 5 verification.

    Requires the companion document to mark its state, name the shared envelope, list every
    planned failure category, and avoid claiming a runtime route already exists.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the planned contract is absent, incomplete, or overstated.
    """
    contract = WEBSOCKET_ARTIFACT.read_text(encoding="utf-8")
    required_phrases = {
        "Version: 1",
        "Status: planned",
        "not runtime-verified",
        "WebSocket subprotocol header",
        '"type"',
        '"payload"',
        '"code"',
        '"message"',
        '"details"',
        '"request_id"',
        "credential_absent",
        "credential_malformed",
        "credential_expired",
        "account_inactive",
        "account_not_found",
        "permission_denied",
        "malformed_frame",
        "unknown_message_type",
        "frame_too_large",
        "connection_throttled",
        "server_error",
    }

    assert all(phrase in contract for phrase in required_phrases)
    assert "runtime-verified" not in contract.replace("not runtime-verified", "")
