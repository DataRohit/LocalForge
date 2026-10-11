/**
 * Generated response examples.
 *
 * Produced by scripts/generate-api.ts from docs/api/openapi-v1.yaml. Do not edit by hand;
 * run the generator and commit the result.
 *
 * @packageDocumentation
 */

export const examples: Readonly<
  Record<string, Readonly<Record<string, readonly unknown[]>>>
> = {
  "browser_csrf": {
    "400": [
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "browser_csrf_head": {},
  "browser_login": {
    "200": [
      {
        "id": "00000000-0000-7000-8000-000000000000",
        "username": "river",
        "email": "river@localforge.invalid",
        "capabilities": [
          "account:read",
          "account:update",
          "account:delete",
          "credentials:change",
          "notifications:receive"
        ]
      }
    ],
    "400": [
      {
        "code": "parse_error",
        "message": "The request body could not be parsed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "username": [
            "This field is required."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "401": [
      {
        "code": "authentication_failed",
        "message": "Authentication failed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "403": [
      {
        "code": "permission_denied",
        "message": "You do not have permission to perform this action.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "415": [
      {
        "code": "unsupported_media_type",
        "message": "The request content type is not supported.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "browser_logout": {
    "400": [
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "403": [
      {
        "code": "permission_denied",
        "message": "You do not have permission to perform this action.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "browser_refresh": {
    "400": [
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "401": [
      {
        "code": "authentication_failed",
        "message": "Authentication failed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "403": [
      {
        "code": "permission_denied",
        "message": "You do not have permission to perform this action.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "jwt_create": {
    "200": [
      {
        "access": "<JWT>",
        "refresh": "<JWT>"
      }
    ],
    "400": [
      {
        "code": "parse_error",
        "message": "The request body could not be parsed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "username": [
            "This field is required."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "401": [
      {
        "code": "authentication_failed",
        "message": "Authentication failed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "415": [
      {
        "code": "unsupported_media_type",
        "message": "The request content type is not supported.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "jwt_refresh": {
    "200": [
      {
        "access": "<JWT>",
        "refresh": "<JWT>"
      }
    ],
    "400": [
      {
        "code": "parse_error",
        "message": "The request body could not be parsed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "refresh": [
            "This field is required."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "401": [
      {
        "code": "authentication_failed",
        "message": "Authentication failed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "415": [
      {
        "code": "unsupported_media_type",
        "message": "The request content type is not supported.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "jwt_verify": {
    "200": [
      {}
    ],
    "400": [
      {
        "code": "parse_error",
        "message": "The request body could not be parsed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "token": [
            "This field is required."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "401": [
      {
        "code": "authentication_failed",
        "message": "Authentication failed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "415": [
      {
        "code": "unsupported_media_type",
        "message": "The request content type is not supported.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "token_login": {
    "200": [
      {
        "token": "0123456789abcdef0123456789abcdef01234567"
      }
    ],
    "400": [
      {
        "code": "parse_error",
        "message": "The request body could not be parsed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "username": [
            "This field is required."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "401": [
      {
        "code": "authentication_failed",
        "message": "Authentication failed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "415": [
      {
        "code": "unsupported_media_type",
        "message": "The request content type is not supported.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "token_logout": {
    "400": [
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "401": [
      {
        "code": "not_authenticated",
        "message": "Authentication credentials were not provided.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "authentication_failed",
        "message": "Authentication failed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "user_registration_or_activation": {
    "201": [
      {
        "username": "river",
        "email": "river@localforge.invalid",
        "capabilities": [
          "account:read",
          "account:update",
          "account:delete",
          "credentials:change",
          "notifications:receive"
        ]
      }
    ],
    "400": [
      {
        "code": "parse_error",
        "message": "The request body could not be parsed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "username": [
            "This field is required."
          ],
          "email": [
            "This field is required."
          ],
          "password": [
            "This field is required."
          ],
          "password_confirm": [
            "This field is required."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "password_confirm": [
            "The password confirmation does not match."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "password": [
            "This password is too short. It must contain at least 8 characters."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "activation_token_expired",
        "message": "The activation token has expired.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "activation_token_foreign",
        "message": "The activation token does not match the account.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "activation_token_malformed",
        "message": "The activation token is malformed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "activation_token_used",
        "message": "The activation token has already been used.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "415": [
      {
        "code": "unsupported_media_type",
        "message": "The request content type is not supported.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "user_profile_retrieve": {
    "200": [
      {
        "id": "00000000-0000-7000-8000-000000000000",
        "username": "river",
        "email": "river@localforge.invalid",
        "capabilities": [
          "account:read",
          "account:update",
          "account:delete",
          "credentials:change",
          "notifications:receive"
        ]
      }
    ],
    "400": [
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "401": [
      {
        "code": "not_authenticated",
        "message": "Authentication credentials were not provided.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "authentication_failed",
        "message": "Authentication failed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "user_profile_head": {},
  "user_profile_update": {
    "200": [
      {
        "id": "00000000-0000-7000-8000-000000000000",
        "username": "river",
        "email": "updated@localforge.invalid",
        "capabilities": [
          "account:read",
          "account:update",
          "account:delete",
          "credentials:change",
          "notifications:receive"
        ]
      }
    ],
    "400": [
      {
        "code": "parse_error",
        "message": "The request body could not be parsed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "email": [
            "Enter a valid email address."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "is_active": [
            "This field is not allowed."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "401": [
      {
        "code": "not_authenticated",
        "message": "Authentication credentials were not provided.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "authentication_failed",
        "message": "Authentication failed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "415": [
      {
        "code": "unsupported_media_type",
        "message": "The request content type is not supported.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "user_profile_delete": {
    "400": [
      {
        "code": "parse_error",
        "message": "The request body could not be parsed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "current_password": [
            "This field is required."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "current_password": [
            "The current password is incorrect."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "401": [
      {
        "code": "not_authenticated",
        "message": "Authentication credentials were not provided.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "authentication_failed",
        "message": "Authentication failed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "415": [
      {
        "code": "unsupported_media_type",
        "message": "The request content type is not supported.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "user_activation_resend": {
    "202": [
      {
        "detail": "If an inactive account matches this address, an activation email will be sent."
      }
    ],
    "400": [
      {
        "code": "parse_error",
        "message": "The request body could not be parsed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "email": [
            "Enter a valid email address."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "415": [
      {
        "code": "unsupported_media_type",
        "message": "The request content type is not supported.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "user_password_reset_request": {
    "202": [
      {
        "detail": "If an account matches this address, a password reset email will be sent."
      }
    ],
    "400": [
      {
        "code": "parse_error",
        "message": "The request body could not be parsed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "email": [
            "Enter a valid email address."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "415": [
      {
        "code": "unsupported_media_type",
        "message": "The request content type is not supported.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "user_password_reset_confirm": {
    "400": [
      {
        "code": "parse_error",
        "message": "The request body could not be parsed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "new_password": [
            "This password is too short. It must contain at least 8 characters."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "password_reset_token_expired",
        "message": "The password reset token has expired.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "password_reset_token_foreign",
        "message": "The password reset token does not match the account.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "password_reset_token_malformed",
        "message": "The password reset token is malformed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "password_reset_token_used",
        "message": "The password reset token has already been used.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "415": [
      {
        "code": "unsupported_media_type",
        "message": "The request content type is not supported.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "user_username_reset_request": {
    "202": [
      {
        "detail": "If an account matches this address, a username reset email will be sent."
      }
    ],
    "400": [
      {
        "code": "parse_error",
        "message": "The request body could not be parsed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "email": [
            "Enter a valid email address."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "415": [
      {
        "code": "unsupported_media_type",
        "message": "The request content type is not supported.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "user_username_reset_confirm": {
    "400": [
      {
        "code": "parse_error",
        "message": "The request body could not be parsed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "new_username": [
            "Enter a valid username. This value may contain only letters, numbers, and @/./+/-/_ characters."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "new_username": [
            "That username is not available."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "username_reset_token_expired",
        "message": "The username reset token has expired.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "username_reset_token_foreign",
        "message": "The username reset token does not match the account.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "username_reset_token_malformed",
        "message": "The username reset token is malformed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "username_reset_token_used",
        "message": "The username reset token has already been used.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "415": [
      {
        "code": "unsupported_media_type",
        "message": "The request content type is not supported.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "user_password_change": {
    "400": [
      {
        "code": "parse_error",
        "message": "The request body could not be parsed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "current_password": [
            "The current password is incorrect."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "new_password": [
            "The new password must differ from the current password."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "new_password": [
            "This password is too short. It must contain at least 8 characters."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "401": [
      {
        "code": "not_authenticated",
        "message": "Authentication credentials were not provided.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "authentication_failed",
        "message": "Authentication failed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "415": [
      {
        "code": "unsupported_media_type",
        "message": "The request content type is not supported.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "user_username_change": {
    "400": [
      {
        "code": "parse_error",
        "message": "The request body could not be parsed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "current_password": [
            "The current password is incorrect."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "new_username": [
            "Enter a valid username. This value may contain only letters, numbers, and @/./+/-/_ characters."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "validation_error",
        "message": "The submitted data is invalid.",
        "details": {
          "new_username": [
            "That username is not available."
          ]
        },
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "401": [
      {
        "code": "not_authenticated",
        "message": "Authentication credentials were not provided.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      },
      {
        "code": "authentication_failed",
        "message": "Authentication failed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "413": [
      {
        "code": "request_too_large",
        "message": "The request body is too large.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "415": [
      {
        "code": "unsupported_media_type",
        "message": "The request content type is not supported.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "429": [
      {
        "code": "throttled",
        "message": "Too many requests.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "code": "service_unavailable",
        "message": "A required service is unavailable.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ]
  },
  "health_readiness": {
    "200": [
      {
        "status": "ready",
        "liveness": "alive",
        "readiness": "ready",
        "checks": {
          "database_primary": "working",
          "database_replica": "working",
          "cache": "working",
          "channel_layer": "working",
          "broker": "working",
          "object_storage": "working",
          "mail": "working"
        }
      }
    ],
    "400": [
      {
        "code": "bad_request",
        "message": "The request was invalid.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "405": [
      {
        "code": "method_not_allowed",
        "message": "The requested method is not allowed.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "406": [
      {
        "code": "not_acceptable",
        "message": "The requested response format is not available.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "500": [
      {
        "code": "internal_server_error",
        "message": "An unexpected error occurred.",
        "details": {},
        "request_id": "00000000-0000-4000-8000-000000000000"
      }
    ],
    "503": [
      {
        "status": "not_ready",
        "liveness": "alive",
        "readiness": "not_ready",
        "checks": {
          "database_primary": "working",
          "database_replica": "working",
          "cache": "unavailable",
          "channel_layer": "working",
          "broker": "working",
          "object_storage": "working",
          "mail": "working"
        }
      }
    ]
  },
  "health_readiness_head": {}
};
