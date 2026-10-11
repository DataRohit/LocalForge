/**
 * End-to-end run orchestration.
 *
 * Runs the whole browser suite in a fixed order: isolation check, throwaway resources, Playwright,
 * log window, cleanup. Cleanup always runs, so a failed or interrupted run leaves no residue.
 *
 * @packageDocumentation
 */

import { parseDotenv } from "./dotenv.ts";
import {
  createDatabase,
  databaseExists,
  dropDatabase,
  flushCacheDatabase,
  keyCount,
  listTestingContainers,
  migrate,
  serverEnvironment,
  startMailpit,
  stopMailpit,
  warningLines,
  type RunSettings,
  type Runner,
} from "./environment.ts";
import { checkIsolation, OPTIONAL_CONTAINERS, REQUIRED_CONTAINERS } from "./isolation.ts";
import { classify, serverWarnings } from "./logs.ts";

/**
 * Orchestration dependencies.
 *
 * Everything the run touches outside this module, injected so the order is testable.
 */
export interface Dependencies {
  readonly run: Runner;
  readonly readFile: (path: string) => Promise<string>;
  readonly root: string;
  readonly settings: RunSettings;
  readonly build: () => Promise<number>;
  readonly playwright: (env: Record<string, string>, args: readonly string[]) => Promise<number>;
  readonly writeCertificate: () => Promise<void>;
  readonly removeCertificate: () => Promise<void>;
  readonly readServerLog: () => Promise<string>;
  readonly clearMailbox: (baseUrl: string, auth: string) => Promise<void>;
  readonly pause: () => Promise<void>;
  readonly now: () => Date;
  readonly report: (line: string) => void;
}

/**
 * Orchestration outcome.
 *
 * The exit code and the problems that made it non-zero.
 */
export interface Outcome {
  readonly code: number;
  readonly problems: readonly string[];
}

/**
 * Residue check inputs.
 *
 * The credentials the check needs and the channel-layer key count taken before the run.
 */
interface ResidueInput {
  readonly user: string;
  readonly cachePassword: string;
  readonly channelsPassword: string;
  readonly channelsBefore: number;
}

const CHANNEL_SETTLE_ATTEMPTS = 90;

/**
 * Verify that the run left nothing behind.
 *
 * Checks that the throwaway database and both cache databases are gone or empty and that Mailpit is
 * removed, then waits for the channel layer's expiring keys to return to the starting count.
 *
 * @param deps - The injected dependencies.
 * @param input - Credentials and the baseline key count.
 * @returns One message per leftover, empty when the testing stack is as it was found.
 */
async function findResidue(deps: Dependencies, input: ResidueInput): Promise<string[]> {
  const { run, settings } = deps;
  const left: string[] = [];
  try {
    if (await databaseExists(run, input.user, settings.database)) {
      left.push(`database ${settings.database} still exists`);
    }
    for (const database of [settings.cacheDatabase, settings.resultsDatabase]) {
      const count = await keyCount(run, "valkey-cache-tv4kq", input.cachePassword, database);
      if (count !== 0) {
        left.push(`cache database ${database} holds ${String(count)} keys`);
      }
    }
    if ((await listTestingContainers(run)).includes("mailpit-tm7bh")) {
      left.push("the Mailpit container is still present");
    }
    let channels = await keyCount(run, "valkey-channels-tv9zw", input.channelsPassword, "0");
    for (
      let attempt = 0;
      channels !== input.channelsBefore && attempt < CHANNEL_SETTLE_ATTEMPTS;
      attempt += 1
    ) {
      await deps.pause();
      channels = await keyCount(run, "valkey-channels-tv9zw", input.channelsPassword, "0");
    }
    if (channels !== input.channelsBefore) {
      left.push(
        `channel layer holds ${String(channels)} keys, expected ${String(input.channelsBefore)}`,
      );
    }
  } catch (error) {
    left.push(error instanceof Error ? error.message : String(error));
  }
  return left;
}

/**
 * Run the complete browser suite.
 *
 * Stops before creating anything if the isolation check fails. Otherwise prepares the throwaway
 * resources, runs Playwright, inspects the bounded log window, and cleans up in a finally block.
 *
 * @param deps - The injected dependencies.
 * @param args - Extra arguments for Playwright.
 * @returns The exit code and the problems found.
 */
export async function orchestrate(deps: Dependencies, args: readonly string[]): Promise<Outcome> {
  const { run, settings, report } = deps;
  const hostEnv = parseDotenv(await deps.readFile(`${deps.root}/.env.testing.host`));
  const containerEnv = parseDotenv(await deps.readFile(`${deps.root}/.env.testing`));

  const isolation = checkIsolation({
    hostEnv,
    containerEnv,
    containers: await listTestingContainers(run),
  });
  if (isolation.length > 0) {
    for (const problem of isolation) {
      report(`[FAIL] isolation: ${problem}`);
    }
    return { code: 1, problems: isolation };
  }
  report("[ OK ] isolation: only the testing project and ports are addressed");

  const buildCode = await deps.build();
  if (buildCode !== 0) {
    return { code: buildCode, problems: ["the production build failed"] };
  }
  report("[ OK ] production build");

  const environment = serverEnvironment(hostEnv, settings);
  const user = hostEnv.POSTGRES_USER ?? "";
  const cachePassword = hostEnv.VALKEY_CACHE_PASSWORD ?? "";
  const mailpitUrl = `http://127.0.0.1:${hostEnv.MAILPIT_WEB_PORT ?? ""}`;
  const mailpitAuth = hostEnv.MP_UI_AUTH ?? "";
  const channelsPassword = hostEnv.VALKEY_CHANNELS_PASSWORD ?? "";
  const channelsBefore = await keyCount(run, "valkey-channels-tv9zw", channelsPassword, "0");
  const since = deps.now().toISOString();
  const problems: string[] = [];
  let code = 1;

  try {
    await deps.writeCertificate();
    await startMailpit(run, deps.root, 60, deps.pause);
    await deps.clearMailbox(mailpitUrl, mailpitAuth);
    await createDatabase(run, user, settings.database);
    await migrate(run, deps.root, environment);
    report(`[ OK ] prepared ${settings.database}, Valkey databases, and Mailpit`);

    code = await deps.playwright(
      {
        ...environment,
        LOCALFORGE_E2E_JOURNEYS: "1",
        LOCALFORGE_API_ORIGIN: settings.backendOrigin,
        E2E_HTTPS_ORIGIN: settings.httpsOrigin,
        E2E_HTTP_ORIGIN: settings.httpOrigin,
        E2E_MAILPIT_URL: mailpitUrl,
        E2E_MAILPIT_AUTH: mailpitAuth,
      },
      args,
    );
    report(`${code === 0 ? "[ OK ]" : "[FAIL]"} Playwright exited with ${String(code)}`);

    const lines = [
      ...(
        await Promise.all(
          [...REQUIRED_CONTAINERS, ...OPTIONAL_CONTAINERS].map((container) =>
            warningLines(run, container, since),
          ),
        )
      ).flat(),
      ...serverWarnings(await deps.readServerLog()),
    ];
    const classified = classify(lines);
    for (const line of classified.expected) {
      report(`[ OK ] expected log line: ${line}`);
    }
    for (const line of classified.unexplained) {
      report(`[FAIL] unexplained log line: ${line}`);
      problems.push(line);
    }
    if (classified.unexplained.length === 0) {
      report("[ OK ] bounded log window: no unexplained warning, error, or critical line");
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    report(`[FAIL] ${message}`);
    problems.push(message);
  } finally {
    await deps.clearMailbox(mailpitUrl, mailpitAuth).catch(() => undefined);
    await stopMailpit(run, deps.root);
    await dropDatabase(run, user, settings.database);
    await flushCacheDatabase(run, cachePassword, settings.cacheDatabase);
    await flushCacheDatabase(run, cachePassword, settings.resultsDatabase);
    await deps.removeCertificate();
    report("[ OK ] cleanup: database dropped, Valkey databases flushed, Mailpit removed");
  }
  const residue = await findResidue(deps, {
    user,
    cachePassword,
    channelsPassword,
    channelsBefore,
  });
  for (const line of residue) {
    report(`[FAIL] residue: ${line}`);
    problems.push(line);
  }
  if (residue.length === 0) {
    report(
      "[ OK ] no residue: no database, no cache keys, no Mailpit, channel layer back to baseline",
    );
  }
  return { code: code !== 0 ? code : problems.length > 0 ? 1 : 0, problems };
}
