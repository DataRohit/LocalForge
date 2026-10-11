/**
 * Tests for the end-to-end environment steps.
 *
 * Uses a fake command runner that records calls, so the exact Docker and database commands, the
 * scope of each, and every failure path are checked without any container.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from "vitest";

import {
  createDatabase,
  databaseExists,
  DEFAULT_SETTINGS,
  dropDatabase,
  flushCacheDatabase,
  keyCount,
  listTestingContainers,
  migrate,
  serverEnvironment,
  spawnRunner,
  sql,
  startMailpit,
  stopMailpit,
  warningLines,
  type RunOptions,
  type RunResult,
  type Runner,
} from "./environment.ts";

interface Call {
  readonly command: string;
  readonly args: readonly string[];
  readonly options: RunOptions | undefined;
}

function fake(answer: (call: Call) => Partial<RunResult> = () => ({})): {
  run: Runner;
  calls: Call[];
} {
  const calls: Call[] = [];
  const run: Runner = (command, args, options) => {
    const call = { command, args, options };
    calls.push(call);
    return Promise.resolve({ code: 0, stdout: "", stderr: "", ...answer(call) });
  };
  return { run, calls };
}

const HOST_ENV = { POSTGRES_DB: "localforge", DJANGO_SETTINGS_MODULE: "config.settings.testing" };

describe("serverEnvironment", () => {
  const env = serverEnvironment(HOST_ENV, DEFAULT_SETTINGS);

  it("keeps the testing values and points at the throwaway resources", () => {
    expect(env.DJANGO_SETTINGS_MODULE).toBe("config.settings.testing");
    expect(env.POSTGRES_DB).toBe("lf_e2e");
    expect(env.VALKEY_CACHE_DB).toBe("11");
    expect(env.VALKEY_RESULTS_DB).toBe("12");
  });

  it("sends mail over SMTP and trusts exactly the two served origins", () => {
    expect(env.EMAIL_BACKEND).toContain("smtp");
    expect(env.DJANGO_SITE_URL).toBe("https://127.0.0.1:4443");
    expect(env.DJANGO_CSRF_TRUSTED_ORIGINS).toBe("https://127.0.0.1:4443,http://127.0.0.1:4173");
    expect(env.DJANGO_CORS_ALLOWED_ORIGINS).toBe(env.DJANGO_CSRF_TRUSTED_ORIGINS);
    expect(env.DJANGO_ALLOWED_HOSTS).toBe("127.0.0.1,localhost");
  });

  it("raises the shared limits but leaves the resend account limit low", () => {
    expect(env.DJANGO_USER_REGISTRATION_ADDRESS_THROTTLE_RATE).toBe("6000/minute");
    expect(env.DJANGO_ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE).toBe("3/hour");
  });
});

describe("listTestingContainers", () => {
  it("filters by the testing project label and returns names", async () => {
    const { run, calls } = fake(() => ({ stdout: "a\r\n\nb\n" }));

    expect(await listTestingContainers(run)).toEqual(["a", "b"]);
    expect(calls[0]?.args).toContain("label=com.docker.compose.project=localforge-test");
  });

  it("fails when Docker fails", async () => {
    const { run } = fake(() => ({ code: 1, stderr: "daemon down" }));

    await expect(listTestingContainers(run)).rejects.toThrow(/daemon down/);
  });
});

describe("Mailpit", () => {
  it("starts through the smtp profile of the testing project and waits for health", async () => {
    let polls = 0;
    const { run, calls } = fake((call) =>
      call.args[0] === "inspect" ? { stdout: ++polls < 2 ? "starting" : "healthy\n" } : {},
    );
    let pauses = 0;

    await startMailpit(run, "/repo", 5, () => {
      pauses += 1;
      return Promise.resolve();
    });

    const start = calls[0];
    expect(start?.args).toEqual(
      expect.arrayContaining([
        "--project-name",
        "localforge-test",
        "--profile",
        "smtp",
        "mailpit-tm7bh",
      ]),
    );
    expect(start?.options?.cwd).toBe("/repo");
    expect(pauses).toBe(1);
  });

  it("fails when the container does not start or never becomes healthy", async () => {
    await expect(
      startMailpit(fake(() => ({ code: 1, stderr: "no image" })).run, "/repo", 1, () =>
        Promise.resolve(),
      ),
    ).rejects.toThrow(/no image/);
    await expect(
      startMailpit(fake(() => ({ stdout: "unhealthy" })).run, "/repo", 2, () => Promise.resolve()),
    ).rejects.toThrow(/did not become healthy/);
  });

  it("is removed through the same project", async () => {
    const { run, calls } = fake();

    await stopMailpit(run, "/repo");

    expect(calls[0]?.args).toEqual(
      expect.arrayContaining(["localforge-test", "rm", "-f", "-s", "mailpit-tm7bh"]),
    );
  });
});

describe("database and cache", () => {
  it("runs SQL inside the testing PostgreSQL container over its local socket", async () => {
    const { run, calls } = fake();

    await sql(run, "app", "SELECT 1");

    expect(calls[0]?.args.slice(0, 3)).toEqual(["exec", "postgres-tp8vn", "psql"]);
    expect(calls[0]?.args).toContain("SELECT 1");
  });

  it("drops any old database before creating the new one", async () => {
    const { run, calls } = fake();

    await createDatabase(run, "app", "lf_e2e");

    expect(calls.map((call) => call.args.at(-1))).toEqual([
      "DROP DATABASE IF EXISTS lf_e2e WITH (FORCE)",
      "CREATE DATABASE lf_e2e",
    ]);
  });

  it("fails when creation fails", async () => {
    const { run } = fake((call) =>
      call.args.at(-1)?.startsWith("CREATE") ? { code: 1, stderr: "denied" } : {},
    );

    await expect(createDatabase(run, "app", "lf_e2e")).rejects.toThrow(/denied/);
  });

  it("drops with force", async () => {
    const { run, calls } = fake();

    await dropDatabase(run, "app", "lf_e2e");

    expect(calls[0]?.args.at(-1)).toContain("WITH (FORCE)");
  });

  it("flushes one database of the cache instance without putting the password in arguments order", async () => {
    const { run, calls } = fake();

    await flushCacheDatabase(run, "pw", "11");

    expect(calls[0]?.args).toEqual([
      "exec",
      "--env",
      "REDISCLI_AUTH=pw",
      "valkey-cache-tv4kq",
      "valkey-cli",
      "-n",
      "11",
      "FLUSHDB",
    ]);
  });
});

describe("migrate", () => {
  it("runs the backend migrations with the prepared environment", async () => {
    const { run, calls } = fake();

    await migrate(run, "/repo", { POSTGRES_DB: "lf_e2e" });

    expect(calls[0]?.command).toBe("uv");
    expect(calls[0]?.args).toContain("migrate");
    expect(calls[0]?.args).toEqual(expect.arrayContaining(["/repo/backend", "/repo/backend/src"]));
    expect(calls[0]?.options?.env).toEqual({ POSTGRES_DB: "lf_e2e" });
  });

  it("reports the last error line on failure", async () => {
    const { run } = fake(() => ({ code: 1, stderr: "trace\nCommandError: broken\n" }));

    await expect(migrate(run, "/repo", {})).rejects.toThrow(/CommandError: broken/);
  });

  it("copes with a failure that printed nothing", async () => {
    const { run } = fake(() => ({ code: 1 }));

    await expect(migrate(run, "/repo", {})).rejects.toThrow(/Migration failed/);
  });
});

describe("warningLines", () => {
  it("rejects an unreadable log window instead of treating it as clean", async () => {
    const { run } = fake(() => ({ code: 1, stderr: "container unavailable" }));

    await expect(warningLines(run, "postgres-tp8vn", "2026-10-11T00:00:00Z")).rejects.toThrow(
      /Could not read the logs/,
    );
  });

  it("keeps warning-level lines from both streams and prefixes the container", async () => {
    const { run, calls } = fake(() => ({
      stdout: "ok line\n2026 WARNING something\n",
      stderr: "FATAL: terminating\nERROR: nope\nfine\n",
    }));

    const lines = await warningLines(run, "postgres-tp8vn", "2026-10-11T00:00:00Z");

    expect(lines).toEqual([
      "postgres-tp8vn: 2026 WARNING something",
      "postgres-tp8vn: FATAL: terminating",
      "postgres-tp8vn: ERROR: nope",
    ]);
    expect(calls[0]?.args).toEqual(["logs", "--since", "2026-10-11T00:00:00Z", "postgres-tp8vn"]);
  });
});

describe("spawnRunner", () => {
  it("captures output, the exit code, and standard input", async () => {
    const script =
      "process.stdin.on('data', d => process.stdout.write(d)); process.stdin.on('end', () => { console.error('err'); process.exit(3); })";

    const result = await spawnRunner(process.execPath, ["-e", script], {
      input: "hello",
      env: { X: "1" },
    });

    expect(result).toEqual({ code: 3, stdout: "hello", stderr: "err\n" });
  });

  it("uses the inherited environment when none is given", async () => {
    const result = await spawnRunner(process.execPath, [
      "-e",
      "console.log(typeof process.env.PATH)",
    ]);

    expect(result.stdout.trim()).toBe("string");
  });

  it("reports code 127 when the program cannot start", async () => {
    const result = await spawnRunner("definitely-not-a-real-program-name", []);

    expect(result.code).toBe(127);
    expect(result.stderr).not.toBe("");
  });
});

describe("residue queries", () => {
  it("asks the maintenance database whether a database exists", async () => {
    const present = fake(() => ({ stdout: "1\n" }));
    const absent = fake(() => ({ stdout: "\n" }));

    expect(await databaseExists(present.run, "app", "lf_e2e")).toBe(true);
    expect(await databaseExists(absent.run, "app", "lf_e2e")).toBe(false);
    expect(present.calls[0]?.args).toEqual(
      expect.arrayContaining(["postgres-tp8vn", "--dbname", "postgres"]),
    );
  });

  it("fails when the existence query fails", async () => {
    await expect(
      databaseExists(fake(() => ({ code: 1, stderr: "no" })).run, "app", "lf_e2e"),
    ).rejects.toThrow(/Could not query/);
  });

  it("counts keys, accepting a plain number or the interactive form", async () => {
    const plain = fake(() => ({ stdout: "7\n" }));
    const interactive = fake(() => ({ stdout: "(integer) 12\n" }));

    expect(await keyCount(plain.run, "valkey-cache-tv4kq", "pw", "11")).toBe(7);
    expect(await keyCount(interactive.run, "valkey-cache-tv4kq", "pw", "11")).toBe(12);
    expect(plain.calls[0]?.args).toEqual(expect.arrayContaining(["REDISCLI_AUTH=pw", "DBSIZE"]));
  });

  it("fails when the count is not a number or the command fails", async () => {
    await expect(
      keyCount(fake(() => ({ stdout: "NOAUTH" })).run, "valkey-cache-tv4kq", "pw", "11"),
    ).rejects.toThrow(/Could not count/);
    await expect(
      keyCount(fake(() => ({ code: 1, stdout: "3" })).run, "valkey-cache-tv4kq", "pw", "11"),
    ).rejects.toThrow(/Could not count/);
  });
});
