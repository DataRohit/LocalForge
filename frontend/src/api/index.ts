/**
 * API module surface.
 *
 * Re-exports what features and the session use: the endpoint functions, error types, the problem
 * mapper, the query client, and the session-loss signal. Nothing else leaves the module.
 *
 * @packageDocumentation
 */

export { renewSession } from "./client";
export * from "./endpoints";
export { ApiError, NetworkError, UnexpectedResponseError } from "./errors";
export { toProblem } from "./problem";
export type { Problem, ProblemKind } from "./problem";
export { createQueryClient, queryKeys } from "./query";
export { openNotificationSocket } from "./socket";
export type { ConnectNotifications, NotificationSocket, SocketHandlers } from "./socket";
export { subscribeUnauthorized } from "./unauthorized";
export { useSubmission } from "./useSubmission";
export type { Submission } from "./useSubmission";
