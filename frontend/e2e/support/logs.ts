/**
 * Log window classification.
 *
 * Splits warning-level log lines into those a journey causes on purpose and those nobody explained.
 * An unexplained line fails the run, as the runtime-truth gate requires.
 *
 * @packageDocumentation
 */

/**
 * An expected log pattern.
 *
 * A pattern for a line a journey provokes deliberately, with the reason it is acceptable.
 */
export interface ExpectedPattern {
  readonly pattern: RegExp;
  readonly reason: string;
}

/**
 * Classified log lines.
 *
 * The lines matched by an expected pattern, with their reasons, and the lines nobody explained.
 */
export interface Classified {
  readonly expected: readonly string[];
  readonly unexplained: readonly string[];
}

/**
 * Expected warnings.
 *
 * Each entry names a line the browser journeys provoke on purpose, such as the rejected CSRF token.
 */
export const EXPECTED_WARNINGS: readonly ExpectedPattern[] = [
  {
    pattern: /WARNING django\.request "[^"]*" GET \/api\/v1\/users\/me\/ 401$/,
    reason: "every signed-out page load asks for the profile and is told to sign in",
  },
  {
    pattern: /WARNING django\.request "[^"]*" POST \/api\/v1\/browser\/refresh\/ 401$/,
    reason:
      "the session bootstrap tries to refresh a session that does not exist, as the lost-cookies journey does on purpose",
  },
  {
    pattern: /WARNING django\.request "[^"]*" POST \/api\/v1\/browser\/login\/ 401$/,
    reason: "the deleted-account journey signs in with credentials that no longer exist",
  },
  {
    pattern: /WARNING django\.request "[^"]*" PATCH \/api\/v1\/users\/me\/ 403$/,
    reason: "the wrong-token journey sends a CSRF token that does not match",
  },
  {
    pattern: /WARNING django\.request "[^"]*" POST \/api\/v1\/users\/resend_activation\/ 429$/,
    reason: "the throttle journey exceeds the resend limit on purpose",
  },
];

const WARNING_LEVELS = new Set(["WARNING", "ERROR", "CRITICAL"]);

/**
 * Describe the warning-level records of the host-mode server log.
 *
 * The server writes one JSON record per line. A record at warning level or above becomes a short
 * line naming its level, logger, message, method, path, and status; a line that is not JSON is kept
 * whole when it names a warning level, so nothing is dropped silently.
 *
 * @param text - Contents of the server log.
 * @returns One description per warning-level line, prefixed with the server name.
 */
export function serverWarnings(text: string): string[] {
  const found: string[] = [];
  for (const line of text.split(/\r?\n/).filter((candidate) => candidate.trim() !== "")) {
    const record = parseRecord(line);
    if (record === null) {
      if (/\b(WARNING|ERROR|CRITICAL)\b/.test(line)) {
        found.push(`host-mode server: ${line.trim()}`);
      }
      continue;
    }
    if (WARNING_LEVELS.has(record.level)) {
      found.push(
        `host-mode server: ${record.level} ${record.logger} "${record.message}" ${record.method} ${record.path} ${record.status}`,
      );
    }
  }
  return found;
}

interface ServerRecord {
  readonly level: string;
  readonly logger: string;
  readonly message: string;
  readonly method: string;
  readonly path: string;
  readonly status: string;
}

/**
 * Read one structured log line.
 *
 * @param line - A line of the server log.
 * @returns The fields the check needs, or null when the line is not a JSON object.
 */
function parseRecord(line: string): ServerRecord | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(line);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) {
    return null;
  }
  const fields = parsed as Record<string, unknown>;
  const request = (fields.request ?? {}) as Record<string, unknown>;
  const text = (value: unknown): string =>
    typeof value === "string" || typeof value === "number" ? String(value) : "-";
  return {
    level: text(fields.level),
    logger: text(fields.logger),
    message: text(fields.message),
    method: text(request.method ?? fields.http_method),
    path: text(request.path ?? fields.http_path),
    status: text(fields.status_code ?? fields.http_status),
  };
}

/**
 * Classify warning-level lines.
 *
 * A line is expected when any pattern matches it; everything else is unexplained.
 * Matched lines keep their reason so the report explains why they were accepted.
 *
 * @param lines - Warning-level lines from the checked containers and the server log.
 * @param expected - Patterns for deliberately provoked lines.
 * @returns The two groups.
 */
export function classify(
  lines: readonly string[],
  expected: readonly ExpectedPattern[] = EXPECTED_WARNINGS,
): Classified {
  const known: string[] = [];
  const unknown: string[] = [];
  for (const line of lines) {
    const rule = expected.find((candidate) => candidate.pattern.test(line));
    if (rule === undefined) {
      unknown.push(line);
    } else {
      known.push(`${line} (${rule.reason})`);
    }
  }
  return { expected: known, unexplained: unknown };
}
