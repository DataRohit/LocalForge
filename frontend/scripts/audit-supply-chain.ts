/**
 * Frontend supply-chain audit.
 *
 * Checks the manifest, workspace policy, and lockfile for exact pins, the release-age gate, the
 * project licence, the absence of install scripts, and registry-only resolution.
 *
 * @packageDocumentation
 */

import { readFile } from "node:fs/promises";
import path from "node:path";

import { parse } from "yaml";

const EXACT_VERSION = /^\d+\.\d+\.\d+$/;
const MINIMUM_RELEASE_AGE_MINUTES = 10_080;
const ALLOWED_OVERRIDES: ReadonlySet<string> = new Set(["eslint-plugin-jsx-a11y>eslint"]);

/**
 * Files the audit reads.
 *
 * Holds the parsed manifest and the raw text of the workspace file, lockfile, and Node pin.
 */
export interface AuditInput {
  readonly manifest: Record<string, unknown>;
  readonly workspace: string;
  readonly lockfile: string;
  readonly nodeVersion: string;
}

/**
 * Narrow an unknown value to a plain object.
 *
 * @param value - Value to test.
 * @returns True when the value is a non-null, non-array object.
 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Check the manifest fields.
 *
 * Requires the MIT licence, a private package, a pinned package manager, exact dependency
 * versions, and an engines range that starts at the pinned Node version.
 *
 * @param manifest - Parsed package.json.
 * @param nodeVersion - Contents of .node-version.
 * @returns One message per problem.
 */
function auditManifest(manifest: Record<string, unknown>, nodeVersion: string): string[] {
  const problems: string[] = [];
  if (manifest.license !== "MIT") {
    problems.push("package.json must declare the MIT licence.");
  }
  if (manifest.private !== true) {
    problems.push("package.json must be private.");
  }
  if (
    typeof manifest.packageManager !== "string" ||
    !/^pnpm@\d+\.\d+\.\d+$/.test(manifest.packageManager)
  ) {
    problems.push("package.json must pin pnpm in packageManager.");
  }
  const engines = manifest.engines;
  if (
    !isRecord(engines) ||
    typeof engines.node !== "string" ||
    !engines.node.includes(`>=${nodeVersion}`)
  ) {
    problems.push(".node-version must equal the lower bound of engines.node.");
  }
  for (const section of ["dependencies", "devDependencies"]) {
    const dependencies = manifest[section];
    if (!isRecord(dependencies)) {
      problems.push(`package.json has no ${section} object.`);
      continue;
    }
    for (const [name, version] of Object.entries(dependencies)) {
      if (typeof version !== "string" || !EXACT_VERSION.test(version)) {
        problems.push(`${name} is not an exact version: ${String(version)}.`);
      }
    }
  }
  return problems;
}

/**
 * Check the workspace policy file.
 *
 * Requires a strict release-age gate of at least seven days, no exclusions, and only the
 * recorded peer override.
 *
 * @param workspace - Raw pnpm-workspace.yaml text.
 * @returns One message per problem.
 */
function auditWorkspace(workspace: string): string[] {
  const parsed: unknown = parse(workspace);
  const document = isRecord(parsed) ? parsed : {};
  const problems: string[] = [];
  const age = document.minimumReleaseAge;
  if (typeof age !== "number" || age < MINIMUM_RELEASE_AGE_MINUTES) {
    problems.push("minimumReleaseAge must be at least 10080 minutes.");
  }
  if (document.minimumReleaseAgeStrict !== true) {
    problems.push("minimumReleaseAgeStrict must be true.");
  }
  const excluded = document.minimumReleaseAgeExclude;
  if (excluded !== undefined && (!Array.isArray(excluded) || excluded.length > 0)) {
    problems.push("minimumReleaseAgeExclude must be empty.");
  }
  const overrides = document.overrides;
  if (isRecord(overrides)) {
    for (const name of Object.keys(overrides).filter((key) => !ALLOWED_OVERRIDES.has(key))) {
      problems.push(`override ${name} is not recorded in the toolchain ADR.`);
    }
  }
  return problems;
}

/**
 * Check the lockfile.
 *
 * Fails on any entry that runs a build script and on any resolution that is not a plain registry
 * tarball, such as a git or direct URL dependency.
 *
 * @param lockfile - Raw pnpm-lock.yaml text.
 * @returns One message per problem.
 */
function auditLockfile(lockfile: string): string[] {
  const problems: string[] = [];
  if (/^\s*requiresBuild:\s*true/m.test(lockfile)) {
    problems.push("the lockfile contains a package that requires a build script.");
  }
  if (/^\s*resolution:\s*\{[^}]*(tarball:|type: git|repo:)/m.test(lockfile)) {
    problems.push("the lockfile contains a non-registry resolution.");
  }
  return problems;
}

/**
 * Audit the supply-chain inputs.
 *
 * Combines the manifest, workspace, and lockfile checks.
 * Every problem is returned so one run lists everything to fix.
 *
 * @param input - The files to audit.
 * @returns One message per problem, empty when the project passes.
 */
export function auditSupplyChain(input: AuditInput): string[] {
  return [
    ...auditManifest(input.manifest, input.nodeVersion.trim()),
    ...auditWorkspace(input.workspace),
    ...auditLockfile(input.lockfile),
  ];
}

/**
 * Read the audit inputs from a project directory.
 *
 * Parses package.json and reads the workspace file, lockfile, and Node pin as text, leaving the
 * interpretation to the audit functions.
 *
 * @param root - Absolute project root.
 * @returns The parsed manifest and raw text of the other files.
 * @throws Error If package.json is not an object.
 */
export async function loadAuditInput(root: string): Promise<AuditInput> {
  const manifest: unknown = JSON.parse(await readFile(path.join(root, "package.json"), "utf8"));
  if (!isRecord(manifest)) {
    throw new Error("package.json is not an object.");
  }
  return {
    manifest,
    workspace: await readFile(path.join(root, "pnpm-workspace.yaml"), "utf8"),
    lockfile: await readFile(path.join(root, "pnpm-lock.yaml"), "utf8"),
    nodeVersion: await readFile(path.join(root, ".node-version"), "utf8"),
  };
}

if (import.meta.main) {
  const problems = auditSupplyChain(await loadAuditInput(path.resolve(import.meta.dirname, "..")));
  for (const problem of problems) {
    console.error(problem);
  }
  if (problems.length > 0) {
    process.exitCode = 1;
  } else {
    console.log("Supply-chain audit: no problems.");
  }
}
