/**
 * Standalone frontend runtime proof.
 *
 * Drives the read-only image and a disposable Traefik probe against the isolated testing backend.
 * Uses no Compose project or new network and always removes its standalone containers.
 *
 * @packageDocumentation
 */

import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { request as httpRequest } from "node:http";
import path from "node:path";

import { chromium } from "@playwright/test";
import { parse, stringify } from "yaml";

import { DEFAULT_SETTINGS, spawnRunner } from "../e2e/support/environment.ts";
import { clearMailbox } from "../e2e/support/mail.ts";
import { orchestrate } from "../e2e/support/orchestrate.ts";

const root = path.resolve(import.meta.dirname, "..", "..");
const results = path.join(root, "frontend", ".e2e-run");
const serverLog = path.join(results, "image-backend.log");
const proxyFile = path.join(results, "image-routing.yaml");
const pause = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 1000));

/**
 * Check the response security and caching contract.
 *
 * Requires the same policy on documents and assets, with distinct cache lifetimes.
 * Returns every defect so the runtime proof cannot hide a missing header.
 *
 * @param headers - Served headers.
 * @param kind - Document or hashed asset.
 * @returns Header contract defects.
 */
export function checkHeaders(headers: Headers, kind: "document" | "asset"): string[] {
  const problems: string[] = [];
  const policy = headers.get("Content-Security-Policy") ?? "";
  if (
    !/(?:^|;)\s*default-src 'self'\s*(?:;|$)/.test(policy) ||
    !/(?:^|;)\s*script-src 'self'\s*(?:;|$)/.test(policy) ||
    !policy.includes("frame-ancestors 'none'") ||
    /unsafe-inline|unsafe-eval/.test(policy)
  ) {
    problems.push("unsafe CSP");
  }
  for (const [name, value] of [
    ["Referrer-Policy", "no-referrer"],
    ["X-Content-Type-Options", "nosniff"],
    ["X-Frame-Options", "DENY"],
  ]) {
    if (headers.get(name ?? "") !== value) {
      problems.push(`missing ${name ?? "header"}`);
    }
  }
  const cache = headers.get("Cache-Control") ?? "";
  if (
    kind === "document"
      ? !cache.includes("no-cache")
      : !cache.includes("immutable") || !cache.includes("max-age=31536000")
  ) {
    problems.push("wrong cache policy");
  }
  return problems;
}

/**
 * Run a Docker command and require success.
 *
 * Captures only output needed by this proof, never the environment values.
 * Failed commands stop verification rather than producing empty evidence.
 *
 * @param args - Docker arguments.
 * @returns Trimmed command output.
 * @throws Error If Docker fails.
 */
async function docker(args: readonly string[]): Promise<string> {
  const result = await spawnRunner("docker", args, { cwd: root });
  if (result.code !== 0) {
    throw new Error(`Docker ${args[0] ?? "command"} failed: ${result.stderr}`);
  }
  return result.stdout.trim();
}

/**
 * Find failure-level records in a runtime log window.
 *
 * Reads structured levels rather than matching ordinary words inside successful messages.
 * Native severity records and malformed structured records remain fail-closed.
 *
 * @param text - Captured stdout and stderr.
 * @returns Unexplained failure-level or malformed records.
 */
export function checkRuntimeLogs(text: string): string[] {
  return text.split(/\r?\n/).filter((line) => {
    if (line.trim() === "") {
      return false;
    }
    try {
      const record: unknown = JSON.parse(line);
      if (
        typeof record !== "object" ||
        record === null ||
        !("level" in record) ||
        typeof record.level !== "string"
      ) {
        return true;
      }
      return !["INFO", "DEBUG", "NOTICE", "TRACE"].includes(record.level.toUpperCase());
    } catch {
      return /\b(WARN|WARNING|ERROR|CRITICAL|FATAL|PANIC)\b|\[(warn|error|crit|alert|emerg)\]/i.test(
        line,
      );
    }
  });
}

/**
 * Describe the disposable proxy routing as a file-provider document.
 *
 * Builds the backend router and, when given, the frontend router at the lower frontend priority.
 * Omitting the frontend reproduces the routing that existed before the frontend was added.
 *
 * @param backendRule - Rule for the Django router.
 * @param frontend - Frontend rule and health path, or undefined for the rolled-back routing.
 * @returns YAML text for the proxy's file provider.
 */
export function routingDocument(
  backendRule: string,
  frontend?: { readonly rule: string; readonly healthPath: string },
): string {
  const routers: Record<string, unknown> = {
    backend: {
      rule: backendRule,
      ...(frontend === undefined ? {} : { priority: 100 }),
      entryPoints: ["web"],
      service: "backend",
    },
  };
  const services: Record<string, unknown> = {
    backend: { loadBalancer: { servers: [{ url: "http://host.docker.internal:28000" }] } },
  };
  if (frontend !== undefined) {
    routers.frontend = {
      rule: frontend.rule,
      priority: 10,
      entryPoints: ["web"],
      service: "frontend",
    };
    services.frontend = {
      loadBalancer: {
        servers: [{ url: "http://127.0.0.1:8080" }],
        healthCheck: { path: frontend.healthPath, interval: "5s" },
      },
    };
  }
  return stringify({ http: { routers, services } });
}

/**
 * Development Compose document shape read by the routing proof.
 *
 * Keeps only the service labels the proof consumes, so unrelated Compose keys stay unchecked.
 */
export interface ComposeDocument {
  readonly services: Record<string, { readonly labels?: readonly string[] }>;
}

/**
 * Read one service's Traefik labels from the development Compose document.
 *
 * Splits each `key=value` entry at its first equals sign. A missing service fails closed so the
 * proof never invents routing.
 *
 * @param compose - Parsed development Compose document.
 * @param service - Compose service name.
 * @returns The service labels keyed by name.
 * @throws Error If the service is absent.
 */
export function serviceLabels(compose: ComposeDocument, service: string): Record<string, string> {
  const definition = compose.services[service];
  if (definition === undefined) {
    throw new Error(`The development Compose service ${service} is missing.`);
  }
  return Object.fromEntries(
    (definition.labels ?? []).map((entry) => {
      const separator = entry.indexOf("=");
      return [entry.slice(0, separator), entry.slice(separator + 1)];
    }),
  );
}

/**
 * Read the whole-host rule that a rollback returns Django to.
 *
 * The frontend's local router matches the entire host at a lower priority, so removing the frontend
 * leaves Django answering exactly that rule. A missing label fails closed so the rehearsal never
 * invents a baseline.
 *
 * @param compose - Parsed development Compose document.
 * @returns The whole-host rule.
 * @throws Error If the label is absent.
 */
export function baselineRule(compose: ComposeDocument): string {
  const rule = serviceLabels(compose, "frontend-fe6vn")["traefik.http.routers.frontend-local.rule"];
  if (rule === undefined) {
    throw new Error("The frontend routing label is missing.");
  }
  return rule;
}

/**
 * Request a bounded local runtime seam.
 *
 * Applies the host used by the frontend and Django routers and a ten-second timeout.
 * Only the standalone listener and isolated testing backend are reachable.
 *
 * @param url - Loopback URL.
 * @returns Served response.
 */
function request(url: string): Promise<Response> {
  return new Promise((resolve, reject) => {
    const pending = httpRequest(
      url,
      {
        headers: { Host: "localforge.localhost" },
        signal: AbortSignal.timeout(10_000),
      },
      (message) => {
        const chunks: Buffer[] = [];
        message.on("data", (chunk: Buffer) => chunks.push(chunk));
        message.on("error", reject);
        message.on("end", () => {
          const headers = new Headers();
          for (const [name, value] of Object.entries(message.headers)) {
            if (value !== undefined) {
              headers.set(name, Array.isArray(value) ? value.join(", ") : value);
            }
          }
          const body = Buffer.concat(chunks).toString();
          resolve(
            new Response(body === "" ? null : body, { status: message.statusCode ?? 500, headers }),
          );
        });
      },
    );
    pending.on("error", reject);
    pending.end();
  });
}

/**
 * Wait for a local server without masking its final failure.
 *
 * Bounds startup to sixty attempts and requires the chosen readiness status.
 * Connection refusal is accepted only while startup remains within that bound.
 *
 * @param url - Readiness URL.
 * @returns Nothing.
 * @throws Error If startup never finishes.
 */
async function ready(url: string): Promise<void> {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const response = await request(url).catch(() => null);
    if (response?.ok === true) {
      return;
    }
    await pause();
  }
  throw new Error(`Readiness failed: ${url}`);
}

/**
 * Prove the running image and the committed development routing rules.
 *
 * Starts the isolated host backend and a disposable proxy in the image's network namespace.
 * Exercises fallback, headers, cache, health, and an unknown API path, then rehearses rollback by
 * restarting the proxy with the frontend-free routing. Reads both logs after each routing.
 *
 * @param env - Isolated testing environment prepared by the shared runner.
 * @returns Zero only when every assertion passes.
 * @throws Error If a runtime contract or cleanup fails.
 */
async function prove(env: Record<string, string>): Promise<number> {
  const since = new Date().toISOString();
  const backend = spawn(
    process.execPath,
    [path.join(root, "frontend", "e2e", "support", "server.ts")],
    {
      cwd: root,
      env: {
        ...process.env,
        ...env,
        E2E_SERVER_LOG: serverLog,
        DJANGO_ALLOWED_HOSTS: "127.0.0.1,localhost,host.docker.internal,localforge.localhost",
      },
      stdio: "inherit",
    },
  );
  let image = "";
  let proxy = "";
  try {
    await ready(`${DEFAULT_SETTINGS.backendOrigin}/api/v1/browser/csrf/`);
    image = await docker([
      "run",
      "--detach",
      "--name",
      "frontend-fe6vn",
      "--read-only",
      "--cap-drop=ALL",
      "--security-opt=no-new-privileges",
      "--tmpfs",
      "/tmp:rw,noexec,nosuid,size=16m,uid=101,gid=101",
      "--publish",
      "127.0.0.1::8080",
      "--publish",
      "127.0.0.1::8081",
      "localforge/frontend:0.1.0",
    ]);
    const inspection: unknown = JSON.parse(await docker(["inspect", image]));
    const records = inspection as {
      HostConfig: { ReadonlyRootfs: boolean };
      Mounts: unknown[];
      Config: { User: string };
      NetworkSettings: { Ports: Record<string, { HostPort: string }[]> };
    }[];
    const record = records[0];
    if (
      record === undefined ||
      !record.HostConfig.ReadonlyRootfs ||
      record.Config.User !== "101:101"
    ) {
      throw new Error("The runtime must be read-only and non-root.");
    }
    const origin = `http://127.0.0.1:${record.NetworkSettings.Ports["8080/tcp"]?.[0]?.HostPort ?? ""}`;
    const edge = `http://127.0.0.1:${record.NetworkSettings.Ports["8081/tcp"]?.[0]?.HostPort ?? ""}`;
    await ready(`${origin}/healthz`);
    const health = await docker(["inspect", "--format", "{{.State.Health.Status}}", image]);
    for (let attempt = 0; health !== "healthy" && attempt < 15; attempt += 1) {
      if (
        (await docker(["inspect", "--format", "{{.State.Health.Status}}", image])) === "healthy"
      ) {
        break;
      }
      await pause();
    }
    if ((await docker(["inspect", "--format", "{{.State.Health.Status}}", image])) !== "healthy") {
      throw new Error("Image health check did not pass.");
    }
    await docker([
      "exec",
      image,
      "sh",
      "-c",
      "! command -v apk && ! command -v npm && ! command -v pnpm && ! command -v node",
    ]);
    const fixture = String.raw`[notice] frontend formatter proof: "quoted" \path`;
    await docker(["exec", image, "sh", "-c", `printf '%s\\n' '${fixture}' > /tmp/nginx-errors`]);
    let formatted = false;
    for (let attempt = 0; attempt < 10 && !formatted; attempt += 1) {
      const output = await docker(["logs", "--since", since, image]);
      formatted = output.split(/\r?\n/).some((line) => {
        const record: unknown = JSON.parse(line);
        return (
          typeof record === "object" &&
          record !== null &&
          "level" in record &&
          record.level === "NOTICE" &&
          "message" in record &&
          record.message === fixture
        );
      });
      if (!formatted) {
        await pause();
      }
    }
    if (!formatted) {
      throw new Error("nginx error records were not escaped as structured JSON.");
    }
    const servedDocument = await request(`${origin}/index.html`);
    const html = await servedDocument.text();
    const problems = checkHeaders(servedDocument.headers, "document");
    const asset = /src="(\/assets\/[^"]+)"/.exec(html)?.[1];
    if (servedDocument.status !== 200 || asset === undefined) {
      throw new Error("Missing SPA document or asset.");
    }
    const built = await request(`${origin}${asset}`);
    problems.push(...checkHeaders(built.headers, "asset"));
    if (built.status !== 200 || problems.length > 0) {
      throw new Error(problems.join(", "));
    }
    if ((await (await request(`${origin}/account/password`)).text()) !== html) {
      throw new Error("SPA fallback failed.");
    }
    const browser = await chromium.launch();
    try {
      const page = await browser.newPage();
      await page.goto(`${origin}/healthz`);
      const blocked = await page.evaluate(async () => {
        const violation = new Promise<string>((resolve) => {
          document.addEventListener(
            "securitypolicyviolation",
            (event) => {
              resolve(event.violatedDirective);
            },
            { once: true },
          );
          setTimeout(() => {
            resolve("missing");
          }, 5000);
        });
        const script = document.createElement("script");
        script.textContent = 'document.documentElement.dataset.injected = "executed"';
        document.body.append(script);
        return {
          directive: await violation,
          executed: document.documentElement.dataset.injected,
        };
      });
      if (blocked.directive !== "script-src-elem" || blocked.executed !== undefined) {
        throw new Error("Served CSP failed to block an injected inline script.");
      }
      console.log("Served CSP blocked injected inline script in Chromium.");
    } finally {
      await browser.close();
    }
    const development = parse(
      await readFile(path.join(root, "compose.development.yaml"), "utf8"),
    ) as ComposeDocument;
    const labels = serviceLabels(development, "django-uv5n2");
    const frontendLabels = serviceLabels(development, "frontend-fe6vn");
    const startProxy = async (): Promise<string> =>
      docker([
        "run",
        "--detach",
        "--network",
        `container:${image}`,
        "--mount",
        `type=bind,src=${proxyFile},dst=/routing.yaml,readonly`,
        "traefik:v3.7.13",
        "--entrypoints.web.address=:8081",
        "--providers.file.filename=/routing.yaml",
        "--global.checknewversion=false",
        "--global.sendanonymoususage=false",
        "--log.format=json",
        "--accesslog.format=json",
      ]);
    await writeFile(
      proxyFile,
      routingDocument(labels["traefik.http.routers.localforge-local.rule"] ?? "", {
        rule: frontendLabels["traefik.http.routers.frontend-local.rule"] ?? "",
        healthPath:
          frontendLabels["traefik.http.services.frontend.loadbalancer.healthcheck.path"] ?? "",
      }),
      "utf8",
    );
    proxy = await startProxy();
    await ready(`${edge}/sign-in`);
    if ((await (await request(`${edge}/account`)).text()) !== html) {
      throw new Error("Committed SPA routing failed.");
    }
    const unknown = await request(`${edge}/api/v1/image-proof-missing/`);
    if (unknown.status !== 404 || (await unknown.text()).includes('<div id="root">')) {
      throw new Error("Unknown API route reached the SPA.");
    }
    const assertCleanLogs = async (container: string): Promise<void> => {
      const logs = await spawnRunner("docker", ["logs", "--since", since, container]);
      if (logs.code !== 0 || checkRuntimeLogs(logs.stdout + logs.stderr).length > 0) {
        throw new Error("Standalone runtime log window is not clean.");
      }
    };
    if ((await request(`${edge}/api/v1/browser/csrf/`)).status !== 204) {
      throw new Error("The backend seam failed under the committed routing.");
    }
    if (record.Mounts.length !== 0) {
      throw new Error("The frontend must be stateless, with no volume or bind mount.");
    }
    await assertCleanLogs(proxy);
    await docker(["rm", "--force", proxy]);
    await writeFile(proxyFile, routingDocument(baselineRule(development)), "utf8");
    proxy = await startProxy();
    await ready(`${edge}/api/v1/browser/csrf/`);
    const rolledBack = await request(`${edge}/account`);
    if (rolledBack.status !== 404 || (await rolledBack.text()).includes('<div id="root">')) {
      throw new Error("Rolled-back routing still served the single-page application.");
    }
    if ((await request(`${edge}/api/v1/browser/csrf/`)).status !== 204) {
      throw new Error("The backend seam failed after rollback.");
    }
    await assertCleanLogs(proxy);
    await assertCleanLogs(image);
    console.log(`Image, committed routing, and rollback passed; log window starts ${since}.`);
    return 0;
  } finally {
    if (process.platform === "win32" && backend.pid !== undefined) {
      await spawnRunner("taskkill", ["/PID", String(backend.pid), "/T", "/F"]);
    } else {
      backend.kill();
    }
    if (proxy !== "") {
      await docker(["rm", "--force", proxy]);
    }
    if (image !== "") {
      await docker(["rm", "--force", image]);
    }
  }
}

if (import.meta.main) {
  await mkdir(results, { recursive: true });
  const outcome = await orchestrate(
    {
      run: spawnRunner,
      readFile: (file) => readFile(file, "utf8"),
      root,
      settings: DEFAULT_SETTINGS,
      build: () => Promise.resolve(0),
      playwright: prove,
      writeCertificate: () => Promise.resolve(),
      removeCertificate: () => Promise.resolve(),
      readServerLog: () => readFile(serverLog, "utf8"),
      clearMailbox: (baseUrl, auth) => clearMailbox({ baseUrl, auth }),
      pause,
      now: () => new Date(),
      report: console.log,
    },
    [],
  );
  process.exitCode = outcome.code;
}
