/**
 * Generated operation table.
 *
 * Produced by scripts/generate-api.ts from docs/api/openapi-v1.yaml. Do not edit by hand;
 * run the generator and commit the result.
 *
 * @packageDocumentation
 */

export const operations = {
  browser_csrf: {
    method: "get",
    path: "/api/v1/browser/csrf/",
    statuses: [204, 400, 405, 406, 413, 429, 500],
    authenticated: false,
    hasBody: false,
  },
  browser_csrf_head: {
    method: "head",
    path: "/api/v1/browser/csrf/",
    statuses: [204, 400, 406, 413, 429, 500],
    authenticated: false,
    hasBody: false,
  },
  browser_login: {
    method: "post",
    path: "/api/v1/browser/login/",
    statuses: [200, 400, 401, 403, 405, 406, 413, 415, 429, 500, 503],
    authenticated: false,
    hasBody: true,
  },
  browser_logout: {
    method: "post",
    path: "/api/v1/browser/logout/",
    statuses: [204, 400, 403, 405, 406, 413, 429, 500, 503],
    authenticated: false,
    hasBody: false,
  },
  browser_refresh: {
    method: "post",
    path: "/api/v1/browser/refresh/",
    statuses: [204, 400, 401, 403, 405, 406, 413, 429, 500, 503],
    authenticated: false,
    hasBody: false,
  },
  jwt_create: {
    method: "post",
    path: "/api/v1/jwt/create/",
    statuses: [200, 400, 401, 405, 406, 413, 415, 429, 500, 503],
    authenticated: false,
    hasBody: true,
  },
  jwt_refresh: {
    method: "post",
    path: "/api/v1/jwt/refresh/",
    statuses: [200, 400, 401, 405, 406, 413, 415, 429, 500, 503],
    authenticated: false,
    hasBody: true,
  },
  jwt_verify: {
    method: "post",
    path: "/api/v1/jwt/verify/",
    statuses: [200, 400, 401, 405, 406, 413, 415, 429, 500, 503],
    authenticated: false,
    hasBody: true,
  },
  token_login: {
    method: "post",
    path: "/api/v1/token/login/",
    statuses: [200, 400, 401, 405, 406, 413, 415, 429, 500, 503],
    authenticated: false,
    hasBody: true,
  },
  token_logout: {
    method: "post",
    path: "/api/v1/token/logout/",
    statuses: [204, 400, 401, 405, 406, 413, 429, 500, 503],
    authenticated: true,
    hasBody: false,
  },
  user_registration_or_activation: {
    method: "post",
    path: "/api/v1/users/",
    statuses: [201, 204, 400, 405, 406, 413, 415, 429, 500, 503],
    authenticated: false,
    hasBody: true,
  },
  user_profile_retrieve: {
    method: "get",
    path: "/api/v1/users/me/",
    statuses: [200, 400, 401, 405, 406, 413, 429, 500, 503],
    authenticated: true,
    hasBody: false,
  },
  user_profile_head: {
    method: "head",
    path: "/api/v1/users/me/",
    statuses: [200, 400, 401, 406, 413, 429, 500, 503],
    authenticated: true,
    hasBody: false,
  },
  user_profile_update: {
    method: "patch",
    path: "/api/v1/users/me/",
    statuses: [200, 400, 401, 403, 405, 406, 413, 415, 429, 500, 503],
    authenticated: true,
    hasBody: true,
  },
  user_profile_delete: {
    method: "delete",
    path: "/api/v1/users/me/",
    statuses: [204, 400, 401, 403, 405, 406, 413, 415, 429, 500, 503],
    authenticated: true,
    hasBody: true,
  },
  user_activation_resend: {
    method: "post",
    path: "/api/v1/users/resend_activation/",
    statuses: [202, 400, 405, 406, 413, 415, 429, 500, 503],
    authenticated: false,
    hasBody: true,
  },
  user_password_reset_request: {
    method: "post",
    path: "/api/v1/users/reset_password/",
    statuses: [202, 400, 405, 406, 413, 415, 429, 500, 503],
    authenticated: false,
    hasBody: true,
  },
  user_password_reset_confirm: {
    method: "post",
    path: "/api/v1/users/reset_password_confirm/",
    statuses: [204, 400, 405, 406, 413, 415, 429, 500, 503],
    authenticated: false,
    hasBody: true,
  },
  user_username_reset_request: {
    method: "post",
    path: "/api/v1/users/reset_username/",
    statuses: [202, 400, 405, 406, 413, 415, 429, 500, 503],
    authenticated: false,
    hasBody: true,
  },
  user_username_reset_confirm: {
    method: "post",
    path: "/api/v1/users/reset_username_confirm/",
    statuses: [204, 400, 405, 406, 413, 415, 429, 500, 503],
    authenticated: false,
    hasBody: true,
  },
  user_password_change: {
    method: "post",
    path: "/api/v1/users/set_password/",
    statuses: [204, 400, 401, 403, 405, 406, 413, 415, 429, 500, 503],
    authenticated: true,
    hasBody: true,
  },
  user_username_change: {
    method: "post",
    path: "/api/v1/users/set_username/",
    statuses: [204, 400, 401, 403, 405, 406, 413, 415, 429, 500, 503],
    authenticated: true,
    hasBody: true,
  },
  health_readiness: {
    method: "get",
    path: "/health/",
    statuses: [200, 400, 405, 406, 500, 503],
    authenticated: false,
    hasBody: false,
  },
  health_readiness_head: {
    method: "head",
    path: "/health/",
    statuses: [200, 400, 406, 500, 503],
    authenticated: false,
    hasBody: false,
  },
} as const;

export type OperationId = keyof typeof operations;
