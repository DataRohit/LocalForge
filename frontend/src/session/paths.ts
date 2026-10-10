/**
 * Client route paths.
 *
 * Names every client route once so links, redirects, and the route table cannot disagree.
 * The values are the routes in the Phase 12 route map.
 *
 * @packageDocumentation
 */

/**
 * Client route paths.
 *
 * One constant per route in the route map, used by links and by the route table.
 */
export const PATHS = {
  home: "/",
  signIn: "/sign-in",
  register: "/register",
  activate: "/activate",
  resendActivation: "/resend-activation",
  forgotPassword: "/forgot-password",
  resetPassword: "/reset-password",
  forgotUsername: "/forgot-username",
  resetUsername: "/reset-username",
  account: "/account",
  accountPassword: "/account/password",
  accountUsername: "/account/username",
  notifications: "/notifications",
} as const;
