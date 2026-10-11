/**
 * Tests for the test suite's own policy.
 *
 * Confirms from the real configuration that every test has a timeout, files run in parallel, and
 * coverage thresholds are 100 percent with only the documented exclusions. Then runs real Vitest
 * processes on fixtures to prove that a console.error or console.warn call fails a test.
 *
 * @packageDocumentation
 */

import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import config from "../vite.config.ts";

const ROOT = path.resolve(import.meta.dirname, "..");

interface JsonReport {
  readonly testResults: readonly {
    readonly name: string;
    readonly assertionResults: readonly {
      readonly status: string;
      readonly failureMessages: string[];
    }[];
  }[];
}

describe("the configured policy", () => {
  const test = config.test ?? {};

  it("gives every test and hook a bounded timeout", () => {
    expect(test.testTimeout).toBeGreaterThanOrEqual(1000);
    expect(test.testTimeout).toBeLessThanOrEqual(15_000);
    expect(test.hookTimeout).toBeGreaterThanOrEqual(1000);
    expect(test.hookTimeout).toBeLessThanOrEqual(15_000);
  });

  it("runs test files in parallel", () => {
    expect(test.fileParallelism).not.toBe(false);
    expect(test.maxWorkers).not.toBe(1);
  });

  it("measures only the application source and gates it at 100 percent", () => {
    const coverage = test.coverage;

    expect(coverage?.provider).toBe("v8");
    expect(coverage?.include).toEqual(["src/**/*.{ts,tsx}"]);
    expect(coverage?.thresholds).toMatchObject({
      statements: 100,
      branches: 100,
      functions: 100,
      lines: 100,
    });
  });

  it("excludes only the bootstrap, the generated API files, tests, and declarations", () => {
    expect(test.coverage?.exclude).toEqual([
      "src/main.tsx",
      "src/api/generated/**",
      "**/*.test.{ts,tsx}",
      "**/*.d.ts",
    ]);
  });

  it("defines an application project with the shared setup and a tooling project", () => {
    const projects = (test.projects ?? []) as { test?: { name?: string; setupFiles?: string[] } }[];

    expect(projects.map((project) => project.test?.name)).toEqual(["app", "tooling"]);
    expect(projects[0]?.test?.setupFiles).toEqual(["test/setup.ts"]);
  });
});

describe("the console policy, observed in a real run", () => {
  it("fails a test that logs an error or a warning, and passes one that consumes them", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "lf-console-"));
    const reportFile = path.join(directory, "report.json");
    spawnSync(
      process.execPath,
      [
        path.join(ROOT, "node_modules", "vitest", "vitest.mjs"),
        "run",
        "--config",
        "scripts/fixtures/console.config.ts",
        "--reporter=json",
        `--outputFile.json=${reportFile}`,
      ],
      { cwd: ROOT, encoding: "utf8" },
    );
    const report = JSON.parse(await readFile(reportFile, "utf8")) as JsonReport;
    await rm(directory, { recursive: true, force: true });
    const outcome = (name: string): { status: string; failure: string } => {
      const file = report.testResults.find((result) => result.name.endsWith(name));
      const assertion = file?.assertionResults[0];
      return {
        status: assertion?.status ?? "missing",
        failure: assertion?.failureMessages.join(" ") ?? "",
      };
    };

    expect(outcome("console-error.fixture.ts").status).toBe("failed");
    expect(outcome("console-error.fixture.ts").failure).toContain(
      "console.error: unexpected failure",
    );
    expect(outcome("console-warn.fixture.ts").status).toBe("failed");
    expect(outcome("console-warn.fixture.ts").failure).toContain("console.warn: suspicious state");
    expect(outcome("console-consumed.fixture.ts").status).toBe("passed");
  }, 60_000);
});
