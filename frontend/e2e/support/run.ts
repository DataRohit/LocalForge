/**
 * End-to-end command.
 *
 * The entry point of `pnpm run e2e`. It wires the real command runner, files, and Playwright into
 * the orchestration and exits with the orchestration's result.
 *
 * @packageDocumentation
 */

import { spawn } from "node:child_process";
import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";

import { DEFAULT_SETTINGS, spawnRunner } from "./environment.ts";
import { clearMailbox } from "./mail.ts";
import { orchestrate, type Dependencies } from "./orchestrate.ts";
import { removeCertificate, writeCertificate } from "./tls.ts";

const frontend = path.resolve(import.meta.dirname, "..", "..");
const root = path.resolve(frontend, "..");
const results = path.join(frontend, ".e2e-run");
const tlsDirectory = path.join(results, "e2e-tls");
const serverLog = path.join(results, "e2e-server.log");

/**
 * Run a child process with inherited output.
 *
 * Leaves color selection to the child tool instead of inheriting a conflicting NO_COLOR flag.
 * Uses direct executables so arguments never pass through a shell.
 *
 * @param command - Executable.
 * @param args - Arguments.
 * @param env - Extra environment.
 * @returns The exit code.
 */
function inherit(
  command: string,
  args: readonly string[],
  env: Record<string, string> = {},
): Promise<number> {
  return new Promise((resolve) => {
    const child = spawn(command, [...args], {
      cwd: frontend,
      env: { ...process.env, ...env, NO_COLOR: undefined },
      stdio: "inherit",
    });
    child.on("error", () => {
      resolve(127);
    });
    child.on("close", (code) => {
      resolve(code ?? 1);
    });
  });
}

const dependencies: Dependencies = {
  run: spawnRunner,
  readFile: (file) => readFile(file, "utf8"),
  root,
  settings: DEFAULT_SETTINGS,
  build: () => inherit("make", ["-C", root, "frontend-build"]),
  playwright: (env, args) =>
    inherit(
      process.execPath,
      [path.join(frontend, "node_modules", "@playwright", "test", "cli.js"), "test", ...args],
      { ...env, E2E_TLS_DIRECTORY: tlsDirectory, E2E_SERVER_LOG: serverLog },
    ),
  writeCertificate: async () => {
    await mkdir(results, { recursive: true });
    await writeCertificate(tlsDirectory);
  },
  removeCertificate: () => removeCertificate(tlsDirectory),
  readServerLog: () => readFile(serverLog, "utf8"),
  clearMailbox: (baseUrl, auth) => clearMailbox({ baseUrl, auth }),
  pause: () => new Promise((resolve) => setTimeout(resolve, 1000)),
  now: () => new Date(),
  report: (line) => {
    console.log(line);
  },
};

const outcome = await orchestrate(dependencies, process.argv.slice(2));
process.exitCode = outcome.code;
