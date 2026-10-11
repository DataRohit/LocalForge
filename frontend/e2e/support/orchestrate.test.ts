/**
 * Tests for the end-to-end orchestration.
 *
 * Drives the run with fake dependencies to prove the isolation check gates everything, the order of
 * steps, that cleanup always runs, and that an unexplained log line fails an otherwise green run.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from "vitest";

import { DEFAULT_SETTINGS, type RunResult, type Runner } from "./environment.ts";
import { OPTIONAL_CONTAINERS, REQUIRED_CONTAINERS } from "./isolation.ts";
import { orchestrate, type Dependencies } from "./orchestrate.ts";

const SECRETS = [
  "POSTGRES_USER=app",
  "POSTGRES_PASSWORD=pg",
  "VALKEY_CACHE_PASSWORD=cache",
  "VALKEY_CHANNELS_PASSWORD=channels",
  "RABBITMQ_DEFAULT_USER=rabbit",
  "RABBITMQ_DEFAULT_PASS=rabbitpw",
  "MP_UI_AUTH=mail:pw",
  "COMPOSE_PROJECT_NAME=localforge-test",
];
const HOST_ONLY = [
  "DJANGO_SETTINGS_MODULE=config.settings.testing",
  "POSTGRES_HOST=127.0.0.1",
  "VALKEY_CACHE_HOST=127.0.0.1",
  "VALKEY_CHANNELS_HOST=127.0.0.1",
  "RABBITMQ_HOST=127.0.0.1",
  "EMAIL_HOST=127.0.0.1",
  "POSTGRES_PORT=25432",
  "VALKEY_CACHE_PORT=26379",
  "VALKEY_CHANNELS_PORT=26380",
  "RABBITMQ_PORT=25672",
  "EMAIL_PORT=21025",
  "MAILPIT_WEB_PORT=28025",
];

interface Harness {
  deps: Dependencies;
  events: string[];
  reports: string[];
}

function harness(
  options: {
    containers?: readonly string[];
    playwright?: number;
    build?: number;
    logs?: Record<string, string>;
    serverLog?: string;
    failOn?: string;
    leftoverDatabase?: boolean;
    cacheKeys?: number;
    channelsAfter?: number;
  } = {},
): Harness {
  const events: string[] = [];
  const reports: string[] = [];
  let channelCounts = 0;
  const run: Runner = (command, args) => {
    const joined = `${command} ${args.join(" ")}`;
    if (args[0] === "ps") {
      events.push("list");
      return Promise.resolve<RunResult>({
        code: 0,
        stdout: (options.containers ?? [...REQUIRED_CONTAINERS]).join("\n"),
        stderr: "",
      });
    }
    if (joined.includes("pg_database")) {
      return Promise.resolve({
        code: 0,
        stdout: options.leftoverDatabase ? "1\n" : "",
        stderr: "",
      });
    }
    if (args.includes("DBSIZE")) {
      const channels = args.includes("valkey-channels-tv9zw");
      channelCounts += channels ? 1 : 0;
      const value = channels
        ? channelCounts === 1
          ? 4
          : (options.channelsAfter ?? 4)
        : (options.cacheKeys ?? 0);
      return Promise.resolve({ code: 0, stdout: `${String(value)}\n`, stderr: "" });
    }
    if (args[0] === "inspect") {
      return Promise.resolve({ code: 0, stdout: "healthy", stderr: "" });
    }
    if (args[0] === "logs") {
      return Promise.resolve({ code: 0, stdout: options.logs?.[args[3] ?? ""] ?? "", stderr: "" });
    }
    events.push(
      joined.includes("DROP DATABASE")
        ? "drop"
        : joined.includes("CREATE DATABASE")
          ? "create"
          : joined.includes("FLUSHDB")
            ? "flush"
            : joined.includes("migrate")
              ? "migrate"
              : joined.includes("rm -f")
                ? "mailpit-rm"
                : "other",
    );
    const failing = options.failOn !== undefined && events.at(-1) === options.failOn;
    return Promise.resolve({ code: failing ? 1 : 0, stdout: "", stderr: failing ? "boom" : "" });
  };
  const deps: Dependencies = {
    run,
    readFile: (file) =>
      Promise.resolve((file.endsWith(".host") ? [...SECRETS, ...HOST_ONLY] : SECRETS).join("\n")),
    root: "/repo",
    settings: DEFAULT_SETTINGS,
    build: () => {
      events.push("build");
      return Promise.resolve(options.build ?? 0);
    },
    playwright: () => {
      events.push("playwright");
      return Promise.resolve(options.playwright ?? 0);
    },
    writeCertificate: () => {
      events.push("certificate");
      return Promise.resolve();
    },
    removeCertificate: () => {
      events.push("certificate-removed");
      return Promise.resolve();
    },
    readServerLog: () => Promise.resolve(options.serverLog ?? ""),
    clearMailbox: () => {
      events.push("mailbox-clear");
      return Promise.resolve();
    },
    pause: () => Promise.resolve(),
    now: () => new Date("2026-10-11T00:00:00Z"),
    report: (line) => reports.push(line),
  };
  return { deps, events, reports };
}

describe("orchestrate", () => {
  it("prepares, runs the browsers, checks the logs, and cleans up in order", async () => {
    const { deps, events, reports } = harness();

    const outcome = await orchestrate(deps, ["--project=journeys-chromium"]);

    expect(outcome).toEqual({ code: 0, problems: [] });
    expect(events.indexOf("list")).toBeLessThan(events.indexOf("build"));
    expect(events.indexOf("create")).toBeLessThan(events.indexOf("migrate"));
    expect(events.indexOf("migrate")).toBeLessThan(events.indexOf("playwright"));
    expect(events.indexOf("playwright")).toBeLessThan(events.lastIndexOf("drop"));
    expect(events).toEqual(
      expect.arrayContaining(["certificate", "certificate-removed", "mailpit-rm", "flush"]),
    );
    expect(reports.some((line) => line.includes("cleanup"))).toBe(true);
  });

  it("stops before creating anything when isolation fails", async () => {
    const { deps, events, reports } = harness({
      containers: ["postgres-pg3ka", ...REQUIRED_CONTAINERS],
    });

    const outcome = await orchestrate(deps, []);

    expect(outcome.code).toBe(1);
    expect(events).toEqual(["list"]);
    expect(reports.some((line) => line.includes("not a registered testing container"))).toBe(true);
  });

  it("stops when the build fails", async () => {
    const { deps, events } = harness({ build: 2 });

    const outcome = await orchestrate(deps, []);

    expect(outcome).toEqual({ code: 2, problems: ["the production build failed"] });
    expect(events).not.toContain("create");
  });

  it("returns the browser exit code and still cleans up", async () => {
    const { deps, events } = harness({ playwright: 1 });

    const outcome = await orchestrate(deps, []);

    expect(outcome.code).toBe(1);
    expect(events).toEqual(expect.arrayContaining(["drop", "certificate-removed"]));
  });

  it("cleans up when preparation throws", async () => {
    const { deps, events, reports } = harness({ failOn: "migrate" });

    const outcome = await orchestrate(deps, []);

    expect(outcome.code).toBe(1);
    expect(events).not.toContain("playwright");
    expect(events.lastIndexOf("drop")).toBeGreaterThan(events.indexOf("migrate"));
    expect(reports.some((line) => line.includes("Migration failed"))).toBe(true);
  });

  it("fails a green run that left an unexplained log line", async () => {
    const { deps, reports } = harness({
      logs: { "postgres-tp8vn": "2026 ERROR something broke" },
      serverLog:
        '{"level": "WARNING", "logger": "django.request", "message": "boom", "request": {"method": "GET", "path": "/x/"}, "status_code": 500}\n{"level": "INFO", "message": "fine"}',
    });

    const outcome = await orchestrate(deps, []);

    expect(outcome.code).toBe(1);
    expect(outcome.problems).toHaveLength(2);
    expect(reports.filter((line) => line.startsWith("[FAIL] unexplained"))).toHaveLength(2);
  });

  it("rejects warnings from the temporary mail service", async () => {
    const { deps } = harness({ logs: { "mailpit-tm7bh": "ERROR mail delivery failed" } });

    const outcome = await orchestrate(deps, []);

    expect(outcome.code).toBe(1);
    expect(outcome.problems).toContain("mailpit-tm7bh: ERROR mail delivery failed");
  });

  it("verifies that nothing was left behind after a clean log window", async () => {
    const { deps, reports } = harness();

    const outcome = await orchestrate(deps, []);

    expect(outcome.code).toBe(0);
    expect(reports.at(-1)).toContain("no residue");
  });

  it("fails when the database, cache keys, Mailpit, or channel keys remain", async () => {
    const { deps, reports } = harness({
      leftoverDatabase: true,
      cacheKeys: 3,
      containers: [...REQUIRED_CONTAINERS, ...OPTIONAL_CONTAINERS],
      channelsAfter: 9,
    });

    const outcome = await orchestrate(deps, []);

    expect(outcome.code).toBe(1);
    expect(outcome.problems).toEqual([
      "database lf_e2e still exists",
      "cache database 11 holds 3 keys",
      "cache database 12 holds 3 keys",
      "the Mailpit container is still present",
      "channel layer holds 9 keys, expected 4",
    ]);
    expect(reports.filter((line) => line.startsWith("[FAIL] residue"))).toHaveLength(5);
  });

  it("reports a residue check that cannot run", async () => {
    const { deps } = harness();
    let channelCalls = 0;
    const broken: Dependencies = {
      ...deps,
      run: (command, args, options) => {
        channelCalls += args.includes("DBSIZE") && args.includes("valkey-channels-tv9zw") ? 1 : 0;
        return channelCalls > 1 && args.includes("valkey-channels-tv9zw")
          ? Promise.resolve({ code: 1, stdout: "", stderr: "down" })
          : deps.run(command, args, options);
      },
    };

    const outcome = await orchestrate(broken, []);

    expect(outcome.problems[0]).toContain("Could not count the keys");
  });

  it("reports a non-error exception by its text", async () => {
    const { deps } = harness();
    const failure: unknown = "plain text failure";
    const failing: Dependencies = {
      ...deps,
      writeCertificate: () => {
        throw failure;
      },
    };

    const outcome = await orchestrate(failing, []);

    expect(outcome.problems).toEqual(["plain text failure"]);
  });
});
