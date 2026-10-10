/**
 * OpenAPI type and operation generator.
 *
 * Writes the schema types and the runtime operation table from the committed OpenAPI document into
 * src/api/generated. With --check it compares instead of writing, so drift fails the type check.
 *
 * @packageDocumentation
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import openapiTS, { astToString } from "openapi-typescript";
import { parse } from "yaml";

const HTTP_METHODS = ["get", "head", "post", "put", "patch", "delete", "options"] as const;

/**
 * One generated operation entry.
 *
 * Holds the identifier, method, path, documented statuses, and authentication need.
 */
export interface OperationEntry {
  readonly id: string;
  readonly method: string;
  readonly path: string;
  readonly statuses: readonly number[];
  readonly authenticated: boolean;
}

/**
 * Outcome of a generation run.
 *
 * Reports whether the run passed and which generated files were written or differ.
 */
export interface GenerationResult {
  readonly ok: boolean;
  readonly changed: readonly string[];
}

/**
 * Options controlling a generation run.
 *
 * Names the specification, the output directory, and whether to compare instead of write.
 */
export interface GenerationOptions {
  readonly specPath: string;
  readonly outputDirectory: string;
  readonly check: boolean;
}

/**
 * Narrow an unknown value to a plain object.
 *
 * Accepts only non-null, non-array objects so the parser can read properties without casts.
 *
 * @param value - Value to test.
 * @returns True when the value is a record.
 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Read the operations out of a parsed OpenAPI document.
 *
 * Visits every path in document order and every supported method, collecting the identifier,
 * documented numeric statuses, and whether the operation declares a security requirement.
 *
 * @param document - Parsed OpenAPI document.
 * @returns The operations in document order.
 * @throws Error If the document has no paths or an operation has no identifier or statuses.
 */
export function readOperations(document: unknown): OperationEntry[] {
  if (!isRecord(document) || !isRecord(document.paths)) {
    throw new Error("The OpenAPI document has no paths object.");
  }
  const entries: OperationEntry[] = [];
  for (const [route, item] of Object.entries(document.paths)) {
    if (!isRecord(item)) {
      throw new Error(`Path ${route} is not an object.`);
    }
    for (const method of HTTP_METHODS) {
      const operation = item[method];
      if (operation === undefined) {
        continue;
      }
      if (!isRecord(operation) || typeof operation.operationId !== "string") {
        throw new Error(`${method.toUpperCase()} ${route} has no operationId.`);
      }
      if (!isRecord(operation.responses)) {
        throw new Error(`${operation.operationId} has no responses.`);
      }
      const statuses = Object.keys(operation.responses)
        .map(Number)
        .filter((status) => Number.isInteger(status))
        .sort((left, right) => left - right);
      const security = operation.security;
      const authenticated =
        Array.isArray(security) &&
        security.length > 0 &&
        security.every(
          (alternative) => isRecord(alternative) && Object.keys(alternative).length > 0,
        );
      entries.push({ id: operation.operationId, method, path: route, statuses, authenticated });
    }
  }
  return entries;
}

/**
 * Render the runtime operation table module.
 *
 * Emits a typed constant keyed by operation identifier so callers resolve method, path, documented
 * statuses, and authentication need from one generated source.
 *
 * @param entries - Operations in document order.
 * @returns The module source text.
 */
export function renderOperationsModule(entries: readonly OperationEntry[]): string {
  const rows = entries.map(
    (entry) =>
      `  ${entry.id}: {\n` +
      `    method: "${entry.method}",\n` +
      `    path: "${entry.path}",\n` +
      `    statuses: [${entry.statuses.join(", ")}],\n` +
      `    authenticated: ${String(entry.authenticated)},\n` +
      `  },`,
  );
  return [
    "/**",
    " * Generated operation table.",
    " *",
    " * Produced by scripts/generate-api.ts from docs/api/openapi-v1.yaml. Do not edit by hand;",
    " * run the generator and commit the result.",
    " *",
    " * @packageDocumentation",
    " */",
    "",
    "export const operations = {",
    ...rows,
    "} as const;",
    "",
    "export type OperationId = keyof typeof operations;",
    "",
  ].join("\n");
}

/**
 * Generate both output files in memory.
 *
 * Runs the type generator and the table renderer against the specification file.
 * Nothing is written here, so the caller decides between writing and comparing.
 *
 * @param specPath - Absolute path of the OpenAPI document.
 * @returns The schema declaration text and the operations module text.
 */
export async function generateFiles(specPath: string): Promise<Record<string, string>> {
  const source = await readFile(specPath, "utf8");
  const ast = await openapiTS(pathToFileURL(specPath));
  return {
    "schema.d.ts": astToString(ast),
    "operations.ts": renderOperationsModule(readOperations(parse(source))),
  };
}

/**
 * Write or verify the generated files.
 *
 * In check mode nothing is written and every file that differs from the committed copy is reported.
 * Otherwise each differing file is rewritten and listed as changed.
 *
 * @param options - Specification path, output directory, and mode.
 * @returns Whether the committed files match, and which files changed.
 */
export async function run(options: GenerationOptions): Promise<GenerationResult> {
  const files = await generateFiles(options.specPath);
  const changed: string[] = [];
  for (const [name, text] of Object.entries(files)) {
    const target = path.join(options.outputDirectory, name);
    const current = await readFile(target, "utf8").catch(() => null);
    if (current === text) {
      continue;
    }
    changed.push(name);
    if (!options.check) {
      await mkdir(options.outputDirectory, { recursive: true });
      await writeFile(target, text, { encoding: "utf8" });
    }
  }
  return { ok: options.check ? changed.length === 0 : true, changed };
}

if (import.meta.main) {
  const root = path.resolve(import.meta.dirname, "..");
  const check = process.argv.includes("--check");
  const result = await run({
    specPath: path.resolve(root, "..", "docs", "api", "openapi-v1.yaml"),
    outputDirectory: path.join(root, "src", "api", "generated"),
    check,
  });
  if (!result.ok) {
    console.error(`Generated API files are out of date: ${result.changed.join(", ")}`);
    console.error("Run: pnpm run api:generate");
    process.exitCode = 1;
  } else if (!check) {
    console.log(`Wrote ${String(result.changed.length)} generated file(s).`);
  }
}
