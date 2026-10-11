/**
 * Notification frames.
 *
 * Reads the text frames the notification socket delivers and describes the events they carry. Any
 * frame that is not a well-formed notification is ignored, so a bad frame can never break the feed.
 *
 * @packageDocumentation
 */

import { z } from "zod";

const frameSchema = z.object({
  type: z.literal("notification"),
  payload: z.object({
    event: z.string().min(1),
    data: z.record(z.string(), z.unknown()),
  }),
});

const LABELS: Readonly<Record<string, string>> = {
  "account.updated": "Your account was updated.",
  "account.username_changed": "Your username was changed.",
};

/**
 * Read the event name from a socket message.
 *
 * Accepts only a text message holding JSON with the notification envelope and a payload that has
 * an event name and an object of data. Binary messages, invalid JSON, other types, and error
 * frames all give null.
 *
 * @param data - The message data from the socket.
 * @returns The event name, or null when the message is not a notification.
 */
export function parseNotification(data: unknown): string | null {
  if (typeof data !== "string") {
    return null;
  }
  let decoded: unknown;
  try {
    decoded = JSON.parse(data);
  } catch {
    return null;
  }
  const result = frameSchema.safeParse(decoded);
  return result.success ? result.data.payload.event : null;
}

/**
 * Describe an event for the person.
 *
 * Uses a sentence for the events the backend documents and the raw event name, rendered as text,
 * for any other so a new backend event still appears.
 *
 * @param event - The event name.
 * @returns The text to show.
 */
export function describeEvent(event: string): string {
  return LABELS[event] ?? `Update received: ${event}`;
}
