/**
 * Host-mode backend launcher.
 *
 * Starts the Django ASGI application under Uvicorn on loopback with the environment the runner
 * prepared, writes its output to a log file for the log-window check, and stops when it is stopped.
 *
 * @packageDocumentation
 */

import { spawn } from "node:child_process";
import { openSync } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..", "..", "..");
const logFile = process.env.E2E_SERVER_LOG;
const wsMaxSize = process.env.UVICORN_WEBSOCKET_MAX_SIZE_BYTES;
if (logFile === undefined || wsMaxSize === undefined) {
  throw new Error("The end-to-end runner did not provide the server log and limits.");
}
const log = openSync(logFile, "w");

const child = spawn(
  "uv",
  [
    "run",
    "--project",
    path.join(root, "backend"),
    "--directory",
    path.join(root, "backend", "src"),
    "python",
    "-m",
    "uvicorn",
    "config.asgi:application",
    "--host",
    "127.0.0.1",
    "--port",
    "28000",
    "--log-level",
    "warning",
    "--no-access-log",
    "--ws",
    "websockets-sansio",
    "--ws-max-size",
    wsMaxSize,
  ],
  { cwd: root, stdio: ["ignore", log, log] },
);

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    child.kill();
  });
}
child.on("exit", (code) => {
  process.exit(code ?? 1);
});
