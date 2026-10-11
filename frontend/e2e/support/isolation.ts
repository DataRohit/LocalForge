/**
 * Testing isolation check.
 *
 * Proves, before any browser starts, that the end-to-end run addresses only the testing stack: the
 * testing project, the testing ports, the credentials the testing containers were started with,
 * and the registered testing containers. It reports problems by variable name, never by value.
 *
 * @packageDocumentation
 */

/**
 * Isolation inputs.
 *
 * The parsed host-mode and container-mode testing env files and the containers Docker reports
 * for the testing project.
 */
export interface IsolationInput {
  readonly hostEnv: Readonly<Record<string, string>>;
  readonly containerEnv: Readonly<Record<string, string>>;
  readonly containers: readonly string[];
}

/**
 * Required testing containers.
 *
 * The headless set the suite needs, named as in the conventions registry.
 */
export const REQUIRED_CONTAINERS: readonly string[] = [
  "postgres-tp8vn",
  "valkey-cache-tv4kq",
  "valkey-channels-tv9zw",
  "rabbitmq-tr6mc",
  "seaweedfs-ts3jd",
  "django-test-dt5qx",
];

/**
 * Optional testing containers.
 *
 * Mailpit exists only while the temporary SMTP profile runs.
 */
export const OPTIONAL_CONTAINERS: readonly string[] = ["mailpit-tm7bh"];

const EXPECTED_PORTS: Readonly<Record<string, string>> = {
  POSTGRES_PORT: "25432",
  VALKEY_CACHE_PORT: "26379",
  VALKEY_CHANNELS_PORT: "26380",
  RABBITMQ_PORT: "25672",
  EMAIL_PORT: "21025",
  MAILPIT_WEB_PORT: "28025",
};

const LOOPBACK_HOSTS = [
  "POSTGRES_HOST",
  "VALKEY_CACHE_HOST",
  "VALKEY_CHANNELS_HOST",
  "RABBITMQ_HOST",
  "EMAIL_HOST",
] as const;

const SHARED_CREDENTIALS = [
  "POSTGRES_USER",
  "POSTGRES_PASSWORD",
  "VALKEY_CACHE_PASSWORD",
  "VALKEY_CHANNELS_PASSWORD",
  "RABBITMQ_DEFAULT_USER",
  "RABBITMQ_DEFAULT_PASS",
  "MP_UI_AUTH",
] as const;

/**
 * Check that the run can only reach the testing stack.
 *
 * Verifies the project name, settings module, loopback hosts, exact testing ports, agreement of the
 * credentials between the two env files, and the container set. Every problem names a variable or a
 * container and never a value.
 *
 * @param input - The parsed env files and the container names.
 * @returns One message per problem, empty when the run is isolated.
 */
export function checkIsolation(input: IsolationInput): string[] {
  const { hostEnv, containerEnv, containers } = input;
  const problems: string[] = [];
  for (const [label, env] of [
    ["host env file", hostEnv],
    ["container env file", containerEnv],
  ] as const) {
    if (env.COMPOSE_PROJECT_NAME !== "localforge-test") {
      problems.push(`${label}: COMPOSE_PROJECT_NAME is not localforge-test.`);
    }
  }
  if (hostEnv.DJANGO_SETTINGS_MODULE !== "config.settings.testing") {
    problems.push("host env file: DJANGO_SETTINGS_MODULE is not config.settings.testing.");
  }
  for (const name of LOOPBACK_HOSTS) {
    if (hostEnv[name] !== "127.0.0.1") {
      problems.push(`host env file: ${name} is not 127.0.0.1.`);
    }
  }
  for (const [name, port] of Object.entries(EXPECTED_PORTS)) {
    if (hostEnv[name] !== port) {
      problems.push(`host env file: ${name} is not the testing port ${port}.`);
    }
  }
  for (const name of SHARED_CREDENTIALS) {
    const host = hostEnv[name];
    if (host === undefined || host === "" || host !== containerEnv[name]) {
      problems.push(`${name} differs between the host and container env files.`);
    }
  }
  const allowed = new Set([...REQUIRED_CONTAINERS, ...OPTIONAL_CONTAINERS]);
  for (const name of REQUIRED_CONTAINERS.filter((required) => !containers.includes(required))) {
    problems.push(`testing container ${name} is not running.`);
  }
  for (const name of containers.filter((running) => !allowed.has(running))) {
    problems.push(`container ${name} is not a registered testing container.`);
  }
  return problems;
}
