/**
 * Frontend supply-chain audit.
 *
 * Checks the manifest, workspace policy, and lockfile for exact pins, the release-age gate, the
 * project licence, the absence of install scripts, and registry-only resolution.
 *
 * @packageDocumentation
 */

import { createHash } from "node:crypto";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";

import { parse } from "yaml";

import { spawnRunner } from "../e2e/support/environment.ts";

const EXACT_VERSION = /^\d+\.\d+\.\d+$/;
const MINIMUM_RELEASE_AGE_MINUTES = 10_080;
const ALLOWED_OVERRIDES: ReadonlyMap<string, string> = new Map([
  ["eslint-plugin-jsx-a11y>eslint", "10.12.0"],
  ["fsevents", "-"],
]);
const DAY_MS = 86_400_000;
const ACCEPTED_LICENSES = new Set([
  "MIT",
  "MIT-0",
  "ISC",
  "Apache-2.0",
  "BSD-2-Clause",
  "BSD-3-Clause",
  "0BSD",
  "Python-2.0",
  "BlueOak-1.0.0",
  "MPL-2.0",
  "(MIT OR CC0-1.0)",
]);

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
    for (const [name, value] of Object.entries(overrides)) {
      if (ALLOWED_OVERRIDES.get(name) !== value) {
        problems.push(`override ${name} is not recorded in the toolchain ADR.`);
      }
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
  const parsed: unknown = parse(lockfile);
  if (
    !isRecord(parsed) ||
    !isRecord(parsed.packages) ||
    Object.keys(parsed.packages).length === 0
  ) {
    return ["the lockfile has no resolved package map."];
  }
  const snapshots = isRecord(parsed.snapshots) ? parsed.snapshots : {};
  for (const metadata of [...Object.values(parsed.packages), ...Object.values(snapshots)]) {
    if (isRecord(metadata) && metadata.requiresBuild === true) {
      problems.push("the lockfile contains a package that requires a build script.");
    }
  }
  for (const metadata of Object.values(parsed.packages)) {
    const resolution = isRecord(metadata) ? metadata.resolution : undefined;
    if (
      !isRecord(resolution) ||
      typeof resolution.integrity !== "string" ||
      resolution.integrity.length === 0 ||
      Object.keys(resolution).some((key) => key !== "integrity")
    ) {
      problems.push("the lockfile contains a non-registry resolution.");
    }
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

/**
 * Bind audit evidence to every pinned input.
 *
 * Includes the manifest, policy, lockfile, and Node version so edits invalidate cached evidence.
 * The hash is an integrity/staleness guard, not an external signature.
 *
 * @param input - Current dependency inputs.
 * @returns SHA-256 evidence fingerprint.
 */
export function auditFingerprint(input: AuditInput): string {
  return createHash("sha256").update(JSON.stringify(input)).digest("hex");
}

/**
 * Validate resolved licence, advisory, script, and release evidence.
 *
 * Rejects incomplete or stale evidence, unaccepted licences, install hooks, and young direct pins.
 * High and critical advisory counts and records both fail the gate.
 *
 * @param input - Current pinned inputs.
 * @param evidence - Cached registry evidence, treated as untrusted data.
 * @param now - Verification time in milliseconds.
 * @returns Every audit finding.
 */
export function auditEvidence(input: AuditInput, evidence: unknown, now: number): string[] {
  if (
    !isRecord(evidence) ||
    !Array.isArray(evidence.packages) ||
    evidence.packages.length === 0 ||
    !isRecord(evidence.releases)
  ) {
    return ["Dependency evidence is incomplete."];
  }
  const problems: string[] = [];
  const captured = typeof evidence.capturedAt === "string" ? Date.parse(evidence.capturedAt) : NaN;
  if (!Number.isFinite(captured) || now < captured || now - captured > DAY_MS) {
    problems.push("Dependency evidence is stale or has an invalid timestamp.");
  }
  if (evidence.fingerprint !== auditFingerprint(input)) {
    problems.push("Dependency evidence does not match the pinned inputs.");
  }
  const resolved: unknown = parse(input.lockfile);
  const required =
    isRecord(resolved) && isRecord(resolved.packages) ? Object.keys(resolved.packages) : [];
  const observed = new Set<string>();
  for (const item of evidence.packages) {
    if (
      !isRecord(item) ||
      typeof item.name !== "string" ||
      typeof item.version !== "string" ||
      typeof item.license !== "string" ||
      !Array.isArray(item.installScripts)
    ) {
      problems.push("Malformed package evidence.");
      continue;
    }
    const identity = `${item.name}@${item.version}`;
    if (observed.has(identity)) {
      problems.push(`Duplicate package evidence: ${identity}.`);
    }
    observed.add(identity);
    if (
      !ACCEPTED_LICENSES.has(item.license) &&
      !(item.name === "caniuse-lite" && item.license === "CC-BY-4.0") &&
      !(["language-subtag-registry", "mdn-data"].includes(item.name) && item.license === "CC0-1.0")
    ) {
      problems.push(`Unaccepted licence: ${identity} (${item.license}).`);
    }
    if (item.installScripts.length > 0) {
      problems.push(`Install script: ${identity}.`);
    }
  }
  if (
    required.length === 0 ||
    required.length !== observed.size ||
    required.some((identity) => !observed.has(identity))
  ) {
    problems.push("Package evidence does not cover the entire resolved lockfile.");
  }
  for (const section of [input.manifest.dependencies, input.manifest.devDependencies]) {
    for (const [name, version] of Object.entries(isRecord(section) ? section : {})) {
      const value = evidence.releases[`${name}@${String(version)}`];
      const released = typeof value === "string" ? Date.parse(value) : NaN;
      if (!Number.isFinite(released) || now - released < 7 * DAY_MS) {
        problems.push(
          `Direct pin is younger than seven days or missing release evidence: ${name}.`,
        );
      }
    }
  }
  const audit = evidence.advisories;
  const metadata = isRecord(audit) && isRecord(audit.metadata) ? audit.metadata : {};
  const counts = isRecord(metadata.vulnerabilities) ? metadata.vulnerabilities : {};
  if (
    !isRecord(audit) ||
    (audit.error !== undefined && audit.error !== null) ||
    !isRecord(audit.advisories) ||
    !Number.isSafeInteger(counts.high) ||
    !Number.isSafeInteger(counts.critical) ||
    Number(counts.high) < 0 ||
    Number(counts.critical) < 0
  ) {
    problems.push("Advisory evidence is incomplete or unavailable.");
  } else {
    if (Number(counts.high) > 0 || Number(counts.critical) > 0) {
      problems.push("High or critical dependency advisory.");
    }
    for (const advisory of Object.values(audit.advisories)) {
      if (
        !isRecord(advisory) ||
        typeof advisory.severity !== "string" ||
        !["info", "low", "moderate", "high", "critical"].includes(advisory.severity)
      ) {
        problems.push("Malformed advisory record.");
      } else if (["high", "critical"].includes(advisory.severity)) {
        problems.push("High or critical dependency advisory record.");
      }
    }
  }
  return problems;
}

/**
 * Fetch one bounded registry document.
 *
 * Only dependency metadata from the package registry is retrieved during explicit refresh.
 * Missing documents or malformed identity data cannot become successful evidence.
 *
 * @param route - Encoded registry-relative dependency path.
 * @returns Parsed metadata object.
 * @throws Error If retrieval or parsing fails.
 */
async function registry(route: string): Promise<Record<string, unknown>> {
  const response = await fetch(`https://registry.npmjs.org/${route}`, {
    signal: AbortSignal.timeout(20_000),
  });
  const body: unknown = await response.json();
  if (!response.ok || !isRecord(body)) {
    throw new Error("Registry evidence retrieval failed.");
  }
  return body;
}

/**
 * Refresh the offline audit evidence at the dependency-resolution boundary.
 *
 * Resolves every lockfile package licence and install-hook declaration with twelve bounded workers.
 * Reads direct release dates and pnpm advisories, then binds the snapshot to current pinned inputs.
 *
 * @param input - Current pinned inputs.
 * @param root - Frontend project directory.
 * @returns Complete evidence for subsequent offline checks.
 * @throws Error If registry, identity, or advisory evidence is unavailable.
 */
async function collectEvidence(input: AuditInput, root: string): Promise<Record<string, unknown>> {
  const resolved: unknown = parse(input.lockfile);
  if (!isRecord(resolved) || !isRecord(resolved.packages)) {
    throw new Error("Missing lockfile packages.");
  }
  const names = Object.keys(resolved.packages);
  const packages: Record<string, unknown>[] = [];
  let next = 0;
  await Promise.all(
    Array.from({ length: 12 }, async () => {
      while (next < names.length) {
        const identity = names[next++];
        if (identity === undefined) {
          break;
        }
        const separator = identity.lastIndexOf("@");
        const name = identity.slice(0, separator);
        const version = identity.slice(separator + 1);
        const metadata = await registry(
          `${encodeURIComponent(name)}/${encodeURIComponent(version)}`,
        );
        if (metadata.name !== name || metadata.version !== version) {
          throw new Error("Registry package identity mismatch.");
        }
        const scripts = isRecord(metadata.scripts) ? metadata.scripts : {};
        const license =
          typeof metadata.license === "string"
            ? metadata.license
            : isRecord(metadata.license) && typeof metadata.license.type === "string"
              ? metadata.license.type
              : "UNKNOWN";
        packages.push({
          name,
          version,
          license,
          installScripts: ["preinstall", "install", "postinstall"].filter((hook) =>
            Object.hasOwn(scripts, hook),
          ),
        });
      }
    }),
  );
  const releases: Record<string, unknown> = {};
  for (const section of [input.manifest.dependencies, input.manifest.devDependencies]) {
    await Promise.all(
      Object.entries(isRecord(section) ? section : {}).map(async ([name, version]) => {
        const metadata = await registry(encodeURIComponent(name));
        releases[`${name}@${String(version)}`] = isRecord(metadata.time)
          ? metadata.time[String(version)]
          : undefined;
      }),
    );
  }
  const result = await spawnRunner(
    "make",
    ["--no-print-directory", "-s", "frontend-advisory-evidence"],
    { cwd: path.resolve(root, "..") },
  );
  if (![0, 2].includes(result.code)) {
    throw new Error("Advisory command failed.");
  }
  const advisories: unknown = JSON.parse(result.stdout);
  return {
    fingerprint: auditFingerprint(input),
    capturedAt: new Date().toISOString(),
    packages,
    releases,
    advisories,
  };
}

if (import.meta.main) {
  const root = path.resolve(import.meta.dirname, "..");
  const input = await loadAuditInput(root);
  const directory = path.join(root, ".e2e-run");
  const file = path.join(directory, "supply-evidence.json");
  const refresh = process.argv.includes("--refresh");
  if (refresh) {
    await rm(file, { force: true });
  }
  const evidence: unknown = refresh
    ? await collectEvidence(input, root)
    : JSON.parse(await readFile(file, "utf8"));
  const problems = [...auditSupplyChain(input), ...auditEvidence(input, evidence, Date.now())];
  if (refresh && problems.length === 0) {
    await mkdir(directory, { recursive: true });
    await writeFile(file, JSON.stringify(evidence), "utf8");
  }
  for (const problem of problems) {
    console.error(problem);
  }
  if (problems.length > 0) {
    process.exitCode = 1;
  } else {
    console.log("Supply-chain audit: no problems.");
  }
}
