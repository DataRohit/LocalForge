"""OpenAPI contract post-processing.

Completes generated schema metadata, status evidence, strict request shapes,
and boundary-only response contracts without coupling them to runtime middleware.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from http import HTTPStatus
from typing import Any, cast

from config.api import HEALTH_PATH
from config.api_errors import BAD_REQUEST, NOT_FOUND, PERMISSION_DENIED

DOCUMENTATION_INFRASTRUCTURE_PATHS = frozenset(
    {
        "/api/schema/",
        "/api/schema/redoc/",
        "/api/schema/swagger-ui/",
    }
)

STRICT_REQUEST_SCHEMA_NAMES = frozenset(
    {
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
)

OPENAPI_OPERATION_EVIDENCE = {
    "health_readiness": (
        "tests/integration/config/test_readiness.py::"
        "test_health_observed_statuses_exactly_match_its_documented_contract"
    ),
    "health_readiness_head": (
        "tests/integration/config/test_readiness.py::"
        "test_health_head_observed_statuses_exactly_match_its_documented_contract"
    ),
    "jwt_create": (
        "tests/integration/accounts/test_jwt_authentication.py::"
        "test_jwt_create_observed_statuses_exactly_match_its_documented_contract"
    ),
    "jwt_refresh": (
        "tests/integration/accounts/test_jwt_authentication.py::"
        "test_jwt_token_operation_statuses_match_their_documented_contracts[refresh]"
    ),
    "jwt_verify": (
        "tests/integration/accounts/test_jwt_authentication.py::"
        "test_jwt_token_operation_statuses_match_their_documented_contracts[verify]"
    ),
    "token_login": (
        "tests/integration/accounts/test_token_authentication.py::"
        "test_login_observed_statuses_exactly_match_its_documented_contract"
    ),
    "token_logout": (
        "tests/integration/accounts/test_token_authentication.py::"
        "test_logout_observed_statuses_exactly_match_its_documented_contract"
    ),
    "user_activation_resend": (
        "tests/integration/accounts/test_account_activation.py::"
        "test_resend_observed_statuses_exactly_match_its_documented_contract"
    ),
    "user_password_change": (
        "tests/integration/accounts/test_password_management.py::"
        "test_password_operation_observed_statuses_exactly_match_its_documented_contract"
        "[set-password]"
    ),
    "user_password_reset_confirm": (
        "tests/integration/accounts/test_password_management.py::"
        "test_password_operation_observed_statuses_exactly_match_its_documented_contract"
        "[reset-password-confirm]"
    ),
    "user_password_reset_request": (
        "tests/integration/accounts/test_password_management.py::"
        "test_password_operation_observed_statuses_exactly_match_its_documented_contract"
        "[reset-password]"
    ),
    "user_profile_delete": (
        "tests/integration/accounts/test_user_profiles.py::"
        "test_profile_observed_statuses_exactly_match_each_documented_contract[delete]"
    ),
    "user_profile_retrieve": (
        "tests/integration/accounts/test_user_profiles.py::"
        "test_profile_observed_statuses_exactly_match_each_documented_contract[get]"
    ),
    "user_profile_head": (
        "tests/integration/accounts/test_user_profiles.py::"
        "test_profile_head_observed_statuses_exactly_match_its_documented_contract"
    ),
    "user_profile_update": (
        "tests/integration/accounts/test_user_profiles.py::"
        "test_profile_observed_statuses_exactly_match_each_documented_contract[patch]"
    ),
    "user_registration_or_activation": (
        "tests/integration/accounts/test_user_profiles.py::"
        "test_registration_observed_statuses_exactly_match_its_documented_contract"
    ),
    "user_username_change": (
        "tests/integration/accounts/test_username_management.py::"
        "test_username_operation_observed_statuses_exactly_match_its_documented_contract"
        "[set-username]"
    ),
    "user_username_reset_confirm": (
        "tests/integration/accounts/test_username_management.py::"
        "test_username_operation_observed_statuses_exactly_match_its_documented_contract"
        "[reset-username-confirm]"
    ),
    "user_username_reset_request": (
        "tests/integration/accounts/test_username_management.py::"
        "test_username_operation_observed_statuses_exactly_match_its_documented_contract"
        "[reset-username]"
    ),
}

OPENAPI_OPERATION_TAGS = {
    "health_readiness": "Health",
    "health_readiness_head": "Health",
    "jwt_create": "Authentication",
    "jwt_refresh": "Authentication",
    "jwt_verify": "Authentication",
    "token_login": "Authentication",
    "token_logout": "Authentication",
}

OPENAPI_TAGS = [
    {
        "name": "Authentication",
        "description": "Token and JSON Web Token authentication operations.",
    },
    {
        "name": "Accounts",
        "description": (
            "Account registration, profile, activation, password, and username operations."
        ),
    },
    {
        "name": "Health",
        "description": "Application readiness and dependency health operations.",
    },
]


def add_throttle_response_headers(
    result: dict[str, Any],
    generator: object,
    request: object,
    public: object,
) -> dict[str, Any]:
    """Attach exact protocol headers to every documented throttle response.

    Walks generated operations centrally so every current and future route that documents 429 also
    declares the retry delay and correlated request identifier clients receive at runtime.

    Arguments:
        result: Generated OpenAPI document.
        generator: Schema generator invoking the post-processing hook.
        request: Optional schema-generation request.
        public: Whether the schema was generated without request-specific filtering.

    Returns:
        Generated document with exact 429 response headers.
    """
    del generator, request, public
    retry_headers = {
        "Retry-After": {
            "description": "Whole seconds until the fixed or rolling admission window recovers.",
            "schema": {"type": "integer", "minimum": 1},
        },
        "X-Request-ID": {
            "description": "Request correlation identifier echoed by the error envelope.",
            "schema": {"type": "string", "format": "uuid"},
        },
    }
    paths = result.get("paths", {})
    if not isinstance(paths, Mapping):
        return result

    for path_item in paths.values():
        if not isinstance(path_item, Mapping):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses")
            if not isinstance(responses, dict):
                continue
            throttled = responses.get("429")
            if isinstance(throttled, dict):
                throttled["headers"] = retry_headers

    return result


def _add_profile_delete_request_body(paths: dict[str, dict[str, Any]]) -> None:
    """Document the request body drf-spectacular omits for DELETE.

    Adds the already implemented current-password contract to the generated profile deletion
    operation for each representation the REST parser accepts.

    Arguments:
        paths: Generated OpenAPI path mapping.

    Returns:
        None.
    """
    profile_delete = paths["/api/v1/users/me/"]["delete"]
    profile_delete["requestBody"] = {
        "content": {
            media_type: {
                "schema": {
                    "$ref": "#/components/schemas/UserProfileDeletion",
                }
            }
            for media_type in (
                "application/json",
                "application/x-www-form-urlencoded",
                "multipart/form-data",
            )
        },
        "required": True,
    }


def _add_implicit_head_operations(paths: dict[str, dict[str, Any]]) -> None:
    """Publish the safe HEAD operations Django derives from GET.

    Copies each runtime GET contract before assigning stable HEAD metadata and removing response
    representations, matching HTTP's bodyless wire semantics without duplicating view annotations.

    Arguments:
        paths: Generated OpenAPI path mapping.

    Returns:
        None.
    """
    contracts = {
        "/api/v1/users/me/": (
            "user_profile_head",
            "Check availability of the caller's profile",
            (
                "Runs the same authentication, authorization, dependency, negotiation, and "
                "authenticated-read admission boundaries as GET without returning a response body."
            ),
        ),
        "/health/": (
            "health_readiness_head",
            "Check application readiness without a body",
            (
                "Runs the same public readiness checks and negotiation boundaries as GET while "
                "returning status and headers only."
            ),
        ),
    }
    for route, (operation_id, summary, description) in contracts.items():
        operation = deepcopy(paths[route]["get"])
        operation["operationId"] = operation_id
        operation["summary"] = summary
        operation["description"] = description
        operation.pop("requestBody", None)
        responses = cast("dict[str, dict[str, Any]]", operation["responses"])
        responses.pop(str(HTTPStatus.METHOD_NOT_ALLOWED), None)
        for response in responses.values():
            response.pop("content", None)
        paths[route]["head"] = operation


def _add_invalid_host_responses(paths: dict[str, dict[str, Any]]) -> None:
    """Document host validation at every fixed operation.

    Adds the exact correlated bad-request representation without claiming malformed-body evidence
    for read or bodyless operations, and retains a representation example extension for HEAD.

    Arguments:
        paths: Generated OpenAPI path mapping.

    Returns:
        None.
    """
    example = {
        "InvalidHost": {
            "summary": "Invalid Host header",
            "value": {
                "code": BAD_REQUEST.code.value,
                "message": BAD_REQUEST.message,
                "details": {},
                "request_id": "00000000-0000-4000-8000-000000000000",
            },
        }
    }
    for path_item in paths.values():
        for method, operation in path_item.items():
            responses = cast("dict[str, dict[str, Any]]", operation["responses"])
            response = responses.setdefault(
                str(HTTPStatus.BAD_REQUEST),
                {
                    "description": (
                        "Django rejected the Host header before the operation executed."
                    ),
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/ErrorEnvelope",
                            },
                            "examples": {},
                        }
                    },
                },
            )
            if method == "head":
                response.pop("content", None)
                response["x-localforge-representation-examples"] = deepcopy(example)
            else:
                content = cast("dict[str, Any]", response["content"])
                media_type = cast("dict[str, Any]", content["application/json"])
                examples = cast("dict[str, Any]", media_type.setdefault("examples", {}))
                examples.update(deepcopy(example))


def _complete_operation_metadata(paths: dict[str, dict[str, Any]]) -> None:
    """Complete security and bodyless success metadata for every operation.

    Makes anonymous access explicit and adds a status-bound example extension where a 204 response
    correctly has no media type or response body.

    Arguments:
        paths: Generated OpenAPI path mapping.

    Returns:
        None.
    """
    for path_item in paths.values():
        for method, operation in path_item.items():
            operation.setdefault("security", [])
            operation_id = cast("str", operation["operationId"])
            operation_evidence = OPENAPI_OPERATION_EVIDENCE[operation_id]
            operation["tags"] = [OPENAPI_OPERATION_TAGS.get(operation_id, "Accounts")]
            operation["x-localforge-observed-by"] = operation_evidence
            responses = cast("dict[str, dict[str, Any]]", operation["responses"])
            for status, response in responses.items():
                invalid_host_evidence = (
                    "tests/integration/config/test_api.py::"
                    "test_fixed_operation_invalid_host_returns_correlated_bad_request"
                    f"[{operation_id}]"
                )
                response["x-localforge-observed-by"] = [
                    operation_evidence,
                    *([invalid_host_evidence] if status == str(HTTPStatus.BAD_REQUEST) else []),
                ]
                if status == str(HTTPStatus.NO_CONTENT) or method == "head":
                    response["x-localforge-examples"] = {
                        "NoContent": {
                            "summary": (
                                "Response with no response body."
                                if method == "head"
                                else "Successful response with no response body."
                            ),
                            "value": None,
                        }
                    }


def _add_profile_deletion_schema(components: dict[str, object]) -> None:
    """Register the serializer shape used only by profile DELETE.

    Supplies the component drf-spectacular cannot discover because its request-body inference
    excludes DELETE even when the operation carries an explicit serializer annotation.

    Arguments:
        components: Generated OpenAPI components mapping.

    Returns:
        None.
    """
    schemas = cast("dict[str, object]", components.setdefault("schemas", {}))
    schemas["UserProfileDeletion"] = {
        "type": "object",
        "properties": {
            "current_password": {
                "type": "string",
                "writeOnly": True,
            }
        },
        "required": ["current_password"],
        "additionalProperties": False,
    }


def _close_strict_request_schemas(components: dict[str, object]) -> None:
    """Match strict serializer rejection in every request component.

    Closes only the components backed by runtime serializers that reject undeclared fields,
    avoiding a global generator claim about response objects or future permissive inputs.

    Arguments:
        components: Generated OpenAPI components mapping.

    Returns:
        None.

    Raises:
        KeyError: If a strict runtime serializer has no generated component.
    """
    schemas = cast("dict[str, dict[str, Any]]", components["schemas"])
    for name in STRICT_REQUEST_SCHEMA_NAMES:
        schemas[name]["additionalProperties"] = False


def _add_health_schemas(
    paths: dict[str, dict[str, Any]],
    components: dict[str, object],
) -> None:
    """Publish exact status-specific public and staff health representations.

    Defines every dependency key, ties ready and not-ready states to their matching checks, keeps
    staff diagnostics bounded, and binds framework failures to the shared error envelope.

    Arguments:
        paths: Generated OpenAPI path mapping.
        components: Generated OpenAPI components mapping.

    Returns:
        None.
    """
    dependency_names = (
        "broker",
        "cache",
        "channel_layer",
        "database_primary",
        "database_replica",
        "mail",
        "object_storage",
    )
    availability_schema = {
        "type": "string",
        "enum": ["working", "unavailable"],
    }
    ready_checks_schema = {
        "type": "object",
        "properties": {
            name: {
                "type": "string",
                "const": "working",
            }
            for name in dependency_names
        },
        "required": list(dependency_names),
        "additionalProperties": False,
    }
    not_ready_checks_schema = {
        "type": "object",
        "properties": {name: deepcopy(availability_schema) for name in dependency_names},
        "required": list(dependency_names),
        "additionalProperties": False,
        "anyOf": [
            {
                "properties": {
                    name: {
                        "const": "unavailable",
                    }
                },
                "required": [name],
            }
            for name in dependency_names
        ],
    }
    working_diagnostic_schema = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "const": "working",
            },
            "duration_ms": {
                "type": "number",
                "minimum": 0,
            },
            "error": {
                "type": "null",
            },
        },
        "required": ["status", "duration_ms", "error"],
        "additionalProperties": False,
    }
    unavailable_diagnostic_schema = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "const": "unavailable",
            },
            "duration_ms": {
                "type": "number",
                "minimum": 0,
            },
            "error": {
                "type": "string",
                "const": "dependency_unavailable",
            },
        },
        "required": ["status", "duration_ms", "error"],
        "additionalProperties": False,
    }
    ready_details_schema = {
        "type": "object",
        "properties": {
            name: {"$ref": "#/components/schemas/HealthWorkingDiagnostic"}
            for name in dependency_names
        },
        "required": list(dependency_names),
        "additionalProperties": False,
    }
    not_ready_details_schema = {
        "type": "object",
        "properties": {
            name: {"$ref": "#/components/schemas/HealthDiagnostic"} for name in dependency_names
        },
        "required": list(dependency_names),
        "additionalProperties": False,
        "anyOf": [
            {
                "properties": {
                    name: {
                        "$ref": "#/components/schemas/HealthUnavailableDiagnostic",
                    }
                },
                "required": [name],
            }
            for name in dependency_names
        ],
    }
    staff_status_coupling = [
        {
            "if": {
                "properties": {
                    "checks": {
                        "properties": {
                            name: {
                                "const": availability,
                            }
                        },
                        "required": [name],
                    }
                },
                "required": ["checks"],
            },
            "then": {
                "properties": {
                    "details": {
                        "properties": {
                            name: {
                                "properties": {
                                    "status": {
                                        "const": availability,
                                    }
                                },
                                "required": ["status"],
                            }
                        },
                        "required": [name],
                    }
                },
                "required": ["details"],
            },
        }
        for name in dependency_names
        for availability in ("working", "unavailable")
    ]
    common_properties = {
        "liveness": {
            "type": "string",
            "const": "alive",
        },
    }
    ready_properties = {
        "status": {
            "type": "string",
            "const": "ready",
        },
        **deepcopy(common_properties),
        "readiness": {
            "type": "string",
            "const": "ready",
        },
        "checks": {
            "$ref": "#/components/schemas/HealthReadyChecks",
        },
    }
    not_ready_properties = {
        "status": {
            "type": "string",
            "const": "not_ready",
        },
        **deepcopy(common_properties),
        "readiness": {
            "type": "string",
            "const": "not_ready",
        },
        "checks": {
            "$ref": "#/components/schemas/HealthNotReadyChecks",
        },
    }
    schemas = cast("dict[str, dict[str, Any]]", components["schemas"])
    schemas["HealthReadyChecks"] = ready_checks_schema
    schemas["HealthNotReadyChecks"] = not_ready_checks_schema
    schemas["HealthWorkingDiagnostic"] = working_diagnostic_schema
    schemas["HealthUnavailableDiagnostic"] = unavailable_diagnostic_schema
    schemas["HealthDiagnostic"] = {
        "oneOf": [
            {"$ref": "#/components/schemas/HealthWorkingDiagnostic"},
            {"$ref": "#/components/schemas/HealthUnavailableDiagnostic"},
        ]
    }
    schemas["HealthStaffStatusCoupling"] = {
        "allOf": staff_status_coupling,
    }
    schemas["HealthReadyDetails"] = ready_details_schema
    schemas["HealthNotReadyDetails"] = not_ready_details_schema
    schemas["HealthReadyPublic"] = {
        "type": "object",
        "properties": deepcopy(ready_properties),
        "required": ["status", "liveness", "readiness", "checks"],
        "additionalProperties": False,
    }
    schemas["HealthReadyStaff"] = {
        "type": "object",
        "properties": {
            **deepcopy(ready_properties),
            "details": {
                "$ref": "#/components/schemas/HealthReadyDetails",
            },
        },
        "required": ["status", "liveness", "readiness", "checks", "details"],
        "additionalProperties": False,
        "allOf": [
            {
                "$ref": "#/components/schemas/HealthStaffStatusCoupling",
            }
        ],
    }
    schemas["HealthReady"] = {
        "oneOf": [
            {"$ref": "#/components/schemas/HealthReadyPublic"},
            {"$ref": "#/components/schemas/HealthReadyStaff"},
        ]
    }
    schemas["HealthNotReadyPublic"] = {
        "type": "object",
        "properties": deepcopy(not_ready_properties),
        "required": ["status", "liveness", "readiness", "checks"],
        "additionalProperties": False,
    }
    schemas["HealthNotReadyStaff"] = {
        "type": "object",
        "properties": {
            **deepcopy(not_ready_properties),
            "details": {
                "$ref": "#/components/schemas/HealthNotReadyDetails",
            },
        },
        "required": ["status", "liveness", "readiness", "checks", "details"],
        "additionalProperties": False,
        "allOf": [
            {
                "$ref": "#/components/schemas/HealthStaffStatusCoupling",
            }
        ],
    }
    schemas["HealthNotReady"] = {
        "oneOf": [
            {"$ref": "#/components/schemas/HealthNotReadyPublic"},
            {"$ref": "#/components/schemas/HealthNotReadyStaff"},
        ]
    }
    error_envelope = schemas["ErrorEnvelope"]
    error_envelope["additionalProperties"] = False
    error_envelope["required"] = ["code", "message", "details", "request_id"]
    error_properties = cast("dict[str, dict[str, Any]]", error_envelope["properties"])
    error_properties["details"] = {
        "type": "object",
        "additionalProperties": {
            "type": "array",
            "items": {
                "type": "string",
                "minLength": 1,
            },
            "minItems": 1,
        },
        "propertyNames": {
            "type": "string",
            "minLength": 1,
        },
        "readOnly": True,
    }
    responses = cast("dict[str, dict[str, Any]]", paths[HEALTH_PATH]["get"]["responses"])
    responses[str(HTTPStatus.OK)]["content"]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/HealthReady"
    }
    responses[str(HTTPStatus.SERVICE_UNAVAILABLE)]["content"]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/HealthNotReady"
    }
    for status in (
        str(HTTPStatus.BAD_REQUEST),
        str(HTTPStatus.METHOD_NOT_ALLOWED),
        str(HTTPStatus.NOT_ACCEPTABLE),
        str(HTTPStatus.INTERNAL_SERVER_ERROR),
    ):
        responses[status]["content"]["application/json"]["schema"] = {
            "$ref": "#/components/schemas/ErrorEnvelope"
        }


def _boundary_response_contract() -> dict[str, object]:
    """Build status-bound examples for responses without operation objects.

    Records CORS, routing, and CSRF middleware behavior beside the generated REST operations while
    retaining the public tests that observe each boundary.

    Arguments:
        None.

    Returns:
        Boundary response extension keyed by HTTP status.
    """
    return {
        str(HTTPStatus.NO_CONTENT): {
            "description": (
                "Allowed CORS preflight for a resolvable versioned API route returns status and "
                "headers only before authentication or the REST operation runs."
            ),
            "x-localforge-examples": {
                "NoContent": {
                    "summary": "Allowed CORS preflight response with no response body.",
                    "value": None,
                }
            },
            "x-localforge-observed-by": (
                "tests/integration/config/test_security.py::"
                "test_allowed_origin_preflight_bypasses_credentials_without_wildcards"
            ),
        },
        str(HTTPStatus.FORBIDDEN): {
            "description": (
                "Session-authenticated writes rejected by Django CSRF middleware return the "
                "standard correlated error envelope before a REST operation runs."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "CsrfRejected": {
                            "summary": "CSRF rejected",
                            "value": {
                                "code": PERMISSION_DENIED.code.value,
                                "message": PERMISSION_DENIED.message,
                                "details": {},
                                "request_id": "00000000-0000-4000-8000-000000000000",
                            },
                        }
                    }
                }
            },
            "x-localforge-observed-by": (
                "tests/integration/config/test_api.py::"
                "test_session_authenticated_csrf_failure_uses_middleware_backed_authentication"
            ),
        },
        str(HTTPStatus.NOT_FOUND): {
            "description": (
                "Unknown paths beneath the versioned API prefix return the standard correlated "
                "error envelope even though no OpenAPI operation matches them."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "RouteNotFound": {
                            "summary": "Route not found",
                            "value": {
                                "code": NOT_FOUND.code.value,
                                "message": NOT_FOUND.message,
                                "details": {},
                                "request_id": "00000000-0000-4000-8000-000000000000",
                            },
                        }
                    }
                }
            },
            "x-localforge-observed-by": (
                "tests/integration/config/test_api.py::"
                "test_unknown_path_returns_a_correlated_json_envelope"
            ),
        },
    }


def finalize_openapi_contract(
    result: dict[str, Any],
    generator: object,
    request: object,
    public: object,
) -> dict[str, Any]:
    """Complete metadata that cannot be expressed by per-view annotations.

    Removes optional documentation infrastructure, marks every application operation explicitly,
    gives bodyless successes a status-bound example, and records boundary-only response contracts.

    Arguments:
        result: Generated OpenAPI document.
        generator: Schema generator invoking the post-processing hook.
        request: Optional schema-generation request.
        public: Whether the schema was generated without request-specific filtering.

    Returns:
        Generated document with complete access and boundary-response metadata.
    """
    del generator, request, public
    paths = cast("dict[str, dict[str, Any]]", result["paths"])
    components = cast("dict[str, object]", result["components"])
    for infrastructure_path in DOCUMENTATION_INFRASTRUCTURE_PATHS:
        paths.pop(infrastructure_path, None)
    _add_implicit_head_operations(paths)
    _add_invalid_host_responses(paths)
    _add_profile_delete_request_body(paths)
    _complete_operation_metadata(paths)
    _add_profile_deletion_schema(components)
    _close_strict_request_schemas(components)
    _add_health_schemas(paths, components)
    result["tags"] = deepcopy(OPENAPI_TAGS)
    result["x-localforge-boundary-responses"] = _boundary_response_contract()

    return result
