/**
 * End-to-end environment preparation.
 *
 * Creates the throwaway database, Valkey namespaces, and Mailpit container the browser suite uses
 * inside the testing stack, builds the host-mode server environment, and removes every trace
 * afterward. Commands run through an injected runner so each step is testable without Docker.
 *
 * @packageDocumentation
 */

import { spawn } from "node:child_process";

/**
 * Result of one command.
 *
 * The exit code and the captured output streams.
 */
export interface RunResult {
  readonly code: number;
  readonly stdout: string;
  readonly stderr: string;
}

/**
 * Options for one command.
 *
 * An optional environment, working directory, and standard input.
 */
export interface RunOptions {
  readonly env?: Readonly<Record<string, string>>;
  readonly cwd?: string;
  readonly input?: string;
}

/**
 * Command runner.
 *
 * Runs an executable with arguments, without a shell, and resolves with its result.
 */
export type Runner = (
  command: string,
  args: readonly string[],
  options?: RunOptions,
) => Promise<RunResult>;

/**
 * Run settings.
 *
 * Names the throwaway resources and origins used by one end-to-end run.
 */
export interface RunSettings {
  readonly database: string;
  readonly cacheDatabase: string;
  readonly resultsDatabase: string;
  readonly httpsOrigin: string;
  readonly httpOrigin: string;
  readonly backendOrigin: string;
}

/**
 * Default run settings.
 *
 * Throwaway names that cannot collide with the suite's own database or the default Valkey
 * databases.
 */
export const DEFAULT_SETTINGS: RunSettings = {
  database: "lf_e2e",
  cacheDatabase: "11",
  resultsDatabase: "12",
  httpsOrigin: "https://127.0.0.1:4443",
  httpOrigin: "http://127.0.0.1:4173",
  backendOrigin: "http://127.0.0.1:28000",
};

const COMPOSE_BASE = [
  "compose",
  "--project-name",
  "localforge-test",
  "--env-file",
  ".env.testing",
  "-f",
  "compose.yaml",
  "-f",
  "compose.testing.yaml",
] as const;

/**
 * Real command runner.
 *
 * Spawns the executable directly, collects its output, and resolves with the exit code. A process
 * that cannot start resolves with code 127 and the error text instead of rejecting.
 *
 * @param command - Executable name.
 * @param args - Arguments.
 * @param options - Environment, working directory, and standard input.
 * @returns The command result.
 */
export const spawnRunner: Runner = (command, args, options = {}) =>
  new Promise((resolve) => {
    const child = spawn(command, [...args], {
      cwd: options.cwd,
      env: options.env === undefined ? process.env : { ...process.env, ...options.env },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });
    child.on("error", (error) => {
      resolve({ code: 127, stdout, stderr: error.message });
    });
    child.on("close", (code) => {
      resolve({ code: code ?? 1, stdout, stderr });
    });
    child.stdin.end(options.input ?? "");
  });

/**
 * Build the environment of the host-mode server.
 *
 * Starts from the testing host env file and overrides only what the browser run needs: the
 * throwaway database and Valkey namespaces, SMTP delivery, the served origins, and rate limits
 * high enough for a parallel suite, with the resend account limit left low for one journey.
 *
 * @param hostEnv - Parsed testing host env file.
 * @param settings - Run settings.
 * @returns The complete environment for the server process.
 */
export function serverEnvironment(
  hostEnv: Readonly<Record<string, string>>,
  settings: RunSettings,
): Record<string, string> {
  const origins = `${settings.httpsOrigin},${settings.httpOrigin}`;
  return {
    ...hostEnv,
    POSTGRES_DB: settings.database,
    VALKEY_CACHE_DB: settings.cacheDatabase,
    VALKEY_RESULTS_DB: settings.resultsDatabase,
    EMAIL_BACKEND: "django.core.mail.backends.smtp.EmailBackend",
    DJANGO_SITE_URL: settings.httpsOrigin,
    DJANGO_ALLOWED_HOSTS: "127.0.0.1,localhost",
    DJANGO_CSRF_TRUSTED_ORIGINS: origins,
    DJANGO_CORS_ALLOWED_ORIGINS: origins,
    DJANGO_API_AUTHENTICATION_THROTTLE_RATE: "6000/minute",
    DJANGO_API_AUTHENTICATED_READ_THROTTLE_RATE: "6000/minute",
    DJANGO_API_ANONYMOUS_THROTTLE_RATE: "6000/minute",
    DJANGO_API_BOUNDARY_ADDRESS_THROTTLE_RATE: "6000/minute",
    DJANGO_WEBSOCKET_CONNECTION_THROTTLE_RATE: "6000/minute",
    DJANGO_TOKEN_LOGIN_ACCOUNT_THROTTLE_RATE: "6000/minute",
    DJANGO_TOKEN_LOGIN_ADDRESS_THROTTLE_RATE: "6000/minute",
    DJANGO_USER_REGISTRATION_ADDRESS_THROTTLE_RATE: "6000/minute",
    DJANGO_ACCOUNT_ACTIVATION_RESEND_ADDRESS_THROTTLE_RATE: "6000/hour",
    DJANGO_PASSWORD_RESET_ADDRESS_THROTTLE_RATE: "6000/hour",
    DJANGO_USERNAME_RESET_ADDRESS_THROTTLE_RATE: "6000/hour",
    DJANGO_ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE: "3/hour",
  };
}

/**
 * List the containers of the testing project.
 *
 * Filters by the Compose project label, so containers of any other project are never listed.
 * The result feeds the isolation check that gates the whole run.
 *
 * @param run - Command runner.
 * @returns The container names.
 * @throws Error If Docker fails.
 */
export async function listTestingContainers(run: Runner): Promise<string[]> {
  const result = await run("docker", [
    "ps",
    "--filter",
    "label=com.docker.compose.project=localforge-test",
    "--format",
    "{{.Names}}",
  ]);
  if (result.code !== 0) {
    throw new Error(`docker ps failed: ${result.stderr.trim()}`);
  }
  return result.stdout
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

/**
 * Start the temporary Mailpit container.
 *
 * Uses the same Compose command as the platform's own gates, scoped to the testing project and the
 * SMTP profile, then waits until the container reports healthy.
 *
 * @param run - Command runner.
 * @param root - Repository root.
 * @param attempts - Health polls before giving up.
 * @param pause - Function that waits between polls.
 * @throws Error If Mailpit does not start or does not become healthy.
 */
export async function startMailpit(
  run: Runner,
  root: string,
  attempts: number,
  pause: () => Promise<void>,
): Promise<void> {
  const started = await run(
    "docker",
    [...COMPOSE_BASE, "--profile", "smtp", "up", "-d", "--no-build", "mailpit-tm7bh"],
    { cwd: root },
  );
  if (started.code !== 0) {
    throw new Error(`Mailpit did not start: ${started.stderr.trim()}`);
  }
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const health = await run("docker", [
      "inspect",
      "--format",
      "{{.State.Health.Status}}",
      "mailpit-tm7bh",
    ]);
    if (health.stdout.trim() === "healthy") {
      return;
    }
    await pause();
  }
  throw new Error("Mailpit did not become healthy.");
}

/**
 * Remove the temporary Mailpit container.
 *
 * Removes only the registered Mailpit container of the testing project, leaving its named data
 * volume for the platform's own gates to manage.
 *
 * @param run - Command runner.
 * @param root - Repository root.
 * @returns The command result.
 */
export function stopMailpit(run: Runner, root: string): Promise<RunResult> {
  return run("docker", [...COMPOSE_BASE, "rm", "-f", "-s", "mailpit-tm7bh"], { cwd: root });
}

/**
 * Run SQL in the testing PostgreSQL container.
 *
 * Executes through the container's local socket against the maintenance database, so no password
 * travels and nothing outside the testing container is addressed.
 *
 * @param run - Command runner.
 * @param user - PostgreSQL user from the testing env file.
 * @param statement - SQL to execute.
 * @returns The command result.
 */
export function sql(run: Runner, user: string, statement: string): Promise<RunResult> {
  return run("docker", [
    "exec",
    "postgres-tp8vn",
    "psql",
    "--username",
    user,
    "--dbname",
    "postgres",
    "--set",
    "ON_ERROR_STOP=1",
    "--command",
    statement,
  ]);
}

/**
 * Empty one Valkey database of the cache instance.
 *
 * Passes the password through the environment of the exec call rather than the command line, and
 * flushes only the numbered database the run used.
 *
 * @param run - Command runner.
 * @param password - Cache instance password.
 * @param database - Database number.
 * @returns The command result.
 */
export function flushCacheDatabase(
  run: Runner,
  password: string,
  database: string,
): Promise<RunResult> {
  return run("docker", [
    "exec",
    "--env",
    `REDISCLI_AUTH=${password}`,
    "valkey-cache-tv4kq",
    "valkey-cli",
    "-n",
    database,
    "FLUSHDB",
  ]);
}

/**
 * Create the throwaway database.
 *
 * Drops any leftover database of the same name first, so an interrupted earlier run cannot make
 * this one fail or start from stale data.
 *
 * @param run - Command runner.
 * @param user - PostgreSQL user.
 * @param database - Database name.
 * @throws Error If creation fails.
 */
export async function createDatabase(run: Runner, user: string, database: string): Promise<void> {
  await dropDatabase(run, user, database);
  const result = await sql(run, user, `CREATE DATABASE ${database}`);
  if (result.code !== 0) {
    throw new Error(`Could not create the ${database} database: ${result.stderr.trim()}`);
  }
}

/**
 * Drop the throwaway database if it exists.
 *
 * Forces open connections closed, so cleanup succeeds even if a server process lingers.
 * Nothing else in the testing stack is affected.
 *
 * @param run - Command runner.
 * @param user - PostgreSQL user.
 * @param database - Database name.
 * @returns The command result.
 */
export function dropDatabase(run: Runner, user: string, database: string): Promise<RunResult> {
  return sql(run, user, `DROP DATABASE IF EXISTS ${database} WITH (FORCE)`);
}

/**
 * Apply the migrations to the throwaway database.
 *
 * Runs the backend's own migrate command with the host-mode environment, so the schema is
 * exactly what the application expects.
 *
 * @param run - Command runner.
 * @param root - Repository root.
 * @param env - The host-mode server environment.
 * @throws Error If migration fails.
 */
export async function migrate(
  run: Runner,
  root: string,
  env: Readonly<Record<string, string>>,
): Promise<void> {
  const result = await run(
    "uv",
    [
      "run",
      "--project",
      `${root}/backend`,
      "--directory",
      `${root}/backend/src`,
      "python",
      "manage.py",
      "migrate",
      "--noinput",
    ],
    { cwd: root, env },
  );
  if (result.code !== 0) {
    throw new Error(`Migration failed: ${result.stderr.trim().split("\n").at(-1) ?? ""}`);
  }
}

/**
 * Tell whether a database exists.
 *
 * Asks the maintenance database, so the answer holds even if the database itself cannot be opened.
 * Used after cleanup to prove the throwaway database is gone.
 *
 * @param run - Command runner.
 * @param user - PostgreSQL user.
 * @param database - Database name.
 * @returns True when the database exists.
 * @throws Error If the query fails.
 */
export async function databaseExists(
  run: Runner,
  user: string,
  database: string,
): Promise<boolean> {
  const result = await run("docker", [
    "exec",
    "postgres-tp8vn",
    "psql",
    "--username",
    user,
    "--dbname",
    "postgres",
    "--tuples-only",
    "--no-align",
    "--command",
    `SELECT 1 FROM pg_database WHERE datname = '${database}'`,
  ]);
  if (result.code !== 0) {
    throw new Error(`Could not query for ${database}: ${result.stderr.trim()}`);
  }
  return result.stdout.trim() !== "";
}

/**
 * Count the keys in one Valkey database.
 *
 * Reads the key total with the instance password passed through the exec environment, accepting
 * both the plain and the interactive form of the answer.
 *
 * @param run - Command runner.
 * @param container - Valkey container name.
 * @param password - Instance password, passed through the exec environment.
 * @param database - Database number.
 * @returns The number of keys.
 * @throws Error If the command fails or answers with something other than a number.
 */
export async function keyCount(
  run: Runner,
  container: string,
  password: string,
  database: string,
): Promise<number> {
  const result = await run("docker", [
    "exec",
    "--env",
    `REDISCLI_AUTH=${password}`,
    container,
    "valkey-cli",
    "-n",
    database,
    "DBSIZE",
  ]);
  const count = Number.parseInt(result.stdout.trim().replace(/^\(integer\)\s*/, ""), 10);
  if (result.code !== 0 || Number.isNaN(count)) {
    throw new Error(`Could not count the keys in ${container} database ${database}.`);
  }
  return count;
}

/**
 * Find warning-level log lines.
 *
 * Reads a bounded window of one container's logs and keeps lines that carry a warning, error, or
 * critical level, so the caller can classify each as expected or unexplained.
 *
 * @param run - Command runner.
 * @param container - Container name.
 * @param since - RFC 3339 start of the window.
 * @returns The matching lines, prefixed with the container name.
 * @throws Error If the log window cannot be read.
 */
export async function warningLines(
  run: Runner,
  container: string,
  since: string,
): Promise<string[]> {
  const result = await run("docker", ["logs", "--since", since, container]);
  if (result.code !== 0) {
    throw new Error(`Could not read the logs for ${container}.`);
  }
  return `${result.stdout}\n${result.stderr}`
    .split(/\r?\n/)
    .filter((line) => /\b(WARNING|WARN|ERROR|CRITICAL|FATAL|PANIC)\b/.test(line))
    .map((line) => `${container}: ${line.trim()}`);
}
