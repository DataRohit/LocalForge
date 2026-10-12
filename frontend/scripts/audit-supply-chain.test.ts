/**
 * Tests for the supply-chain audit.
 *
 * Starts from a passing input and breaks one property at a time. The real project files must
 * also pass, which is the install-time gate for every dependency change.
 *
 * @packageDocumentation
 */

import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  auditEvidence,
  auditFingerprint,
  auditSupplyChain,
  loadAuditInput,
  type AuditInput,
} from "./audit-supply-chain.ts";

const GOOD: AuditInput = {
  manifest: {
    license: "MIT",
    private: true,
    packageManager: "pnpm@11.21.0",
    engines: { node: ">=24.20.0 <25" },
    dependencies: { react: "19.3.0" },
    devDependencies: { vite: "8.3.2" },
  },
  workspace:
    "minimumReleaseAge: 10080\nminimumReleaseAgeStrict: true\noverrides:\n  eslint-plugin-jsx-a11y>eslint: 10.12.0\n",
  lockfile: "packages:\n  react@19.3.0:\n    resolution: {integrity: sha512-abc}\n",
  nodeVersion: "24.20.0\n",
};

function audit(change: Partial<AuditInput>): string[] {
  return auditSupplyChain({ ...GOOD, ...change });
}

describe("auditEvidence", () => {
  const now = Date.parse("2026-10-11T17:00:00Z");
  const evidence = (): Record<string, unknown> => ({
    fingerprint: auditFingerprint(GOOD),
    capturedAt: "2026-10-11T16:00:00Z",
    packages: [{ name: "react", version: "19.3.0", license: "MIT", installScripts: [] }],
    releases: { "react@19.3.0": "2026-09-01T00:00:00Z", "vite@8.3.2": "2026-10-01T00:00:00Z" },
    advisories: { advisories: {}, metadata: { vulnerabilities: { high: 0, critical: 0 } } },
  });

  it("accepts fresh complete pinned evidence", () => {
    expect(auditEvidence(GOOD, evidence(), now)).toEqual([]);
  });

  it.each([null, {}, { ...evidence(), packages: [] }])(
    "fails closed on incomplete evidence",
    (value) => {
      expect(auditEvidence(GOOD, value, now).length).toBeGreaterThan(0);
    },
  );

  it.each(["2026-10-09T00:00:00Z", "2026-10-12T00:00:00Z", "invalid"])(
    "rejects stale or invalid evidence dates",
    (capturedAt) => {
      expect(auditEvidence(GOOD, { ...evidence(), capturedAt }, now).length).toBeGreaterThan(0);
    },
  );

  it("rejects changes to the pinned inputs", () => {
    expect(
      auditEvidence(GOOD, { ...evidence(), fingerprint: "wrong" }, now).length,
    ).toBeGreaterThan(0);
  });

  it.each(["SSPL-1.0", "UNKNOWN", "CC-BY-4.0", "CC0-1.0"])(
    "rejects an unaccepted licence %s",
    (license) => {
      expect(
        auditEvidence(
          GOOD,
          {
            ...evidence(),
            packages: [{ name: "react", version: "19.3.0", license, installScripts: [] }],
          },
          now,
        ).length,
      ).toBeGreaterThan(0);
    },
  );

  it("rejects install hooks even when the lockfile omits its build flag", () => {
    expect(
      auditEvidence(
        GOOD,
        {
          ...evidence(),
          packages: [
            { name: "react", version: "19.3.0", license: "MIT", installScripts: ["postinstall"] },
          ],
        },
        now,
      ).length,
    ).toBeGreaterThan(0);
  });

  it.each([
    ["caniuse-lite", "CC-BY-4.0"],
    ["language-subtag-registry", "CC0-1.0"],
    ["mdn-data", "CC0-1.0"],
  ])("accepts only the recorded data exception %s", (name, license) => {
    const input = { ...GOOD, lockfile: `packages:\n  ${name}@1.0.0: {}\n` };
    expect(
      auditEvidence(
        input,
        {
          ...evidence(),
          fingerprint: auditFingerprint(input),
          packages: [{ name, version: "1.0.0", license, installScripts: [] }],
        },
        now,
      ),
    ).toEqual([]);
  });

  it.each([
    [null],
    [{ name: "react", version: "19.3.0", license: "MIT", installScripts: null }],
    [
      { name: "react", version: "19.3.0", license: "MIT", installScripts: [] },
      { name: "react", version: "19.3.0", license: "MIT", installScripts: [] },
    ],
    [{ name: "other", version: "19.3.0", license: "MIT", installScripts: [] }],
  ])("rejects malformed, duplicate, or incomplete package coverage", (...packages) => {
    expect(auditEvidence(GOOD, { ...evidence(), packages }, now).length).toBeGreaterThan(0);
  });

  it.each(["high", "critical", "unknown"])("rejects advisory records: %s", (severity) => {
    const advisories = {
      advisories: { advisory: { severity } },
      metadata: { vulnerabilities: { high: 0, critical: 0 } },
    };
    expect(auditEvidence(GOOD, { ...evidence(), advisories }, now).length).toBeGreaterThan(0);
  });

  it.each(["2026-10-10T00:00:00Z", "2027-01-01T00:00:00Z", "invalid"])(
    "rejects new or invalid direct release dates",
    (release) => {
      expect(
        auditEvidence(GOOD, { ...evidence(), releases: { "react@19.3.0": release } }, now).length,
      ).toBeGreaterThan(0);
    },
  );

  it.each([
    {},
    { error: "registry unavailable" },
    { metadata: { vulnerabilities: { high: 1, critical: 0 } } },
  ])("rejects missing or dangerous advisory evidence", (advisories) => {
    expect(auditEvidence(GOOD, { ...evidence(), advisories }, now).length).toBeGreaterThan(0);
  });
});

describe("auditSupplyChain", () => {
  it("passes a compliant input", () => {
    expect(audit({})).toEqual([]);
  });

  it.each([
    ["license", { ...GOOD.manifest, license: "UNLICENSED" }, "MIT licence"],
    ["private", { ...GOOD.manifest, private: false }, "must be private"],
    ["packageManager", { ...GOOD.manifest, packageManager: "npm@12.0.0" }, "pin pnpm"],
    ["missing packageManager", { ...GOOD.manifest, packageManager: undefined }, "pin pnpm"],
    ["engines", { ...GOOD.manifest, engines: { node: ">=22.0.0" } }, ".node-version"],
    ["engines shape", { ...GOOD.manifest, engines: "x" }, ".node-version"],
    [
      "range",
      { ...GOOD.manifest, dependencies: { react: "^19.3.0" } },
      "react is not an exact version",
    ],
    [
      "tag",
      { ...GOOD.manifest, devDependencies: { vite: "latest" } },
      "vite is not an exact version",
    ],
    [
      "non-string",
      { ...GOOD.manifest, devDependencies: { vite: 8 } },
      "vite is not an exact version",
    ],
    ["no dependencies", { ...GOOD.manifest, dependencies: undefined }, "no dependencies object"],
  ])("rejects a manifest problem: %s", (_name, manifest, message) => {
    expect(audit({ manifest }).join(" ")).toContain(message);
  });

  it.each([
    ["age", "minimumReleaseAge: 60\nminimumReleaseAgeStrict: true\n", "at least 10080"],
    ["no age", "minimumReleaseAgeStrict: true\n", "at least 10080"],
    ["not strict", "minimumReleaseAge: 10080\n", "must be true"],
    [
      "exclusion",
      "minimumReleaseAge: 10080\nminimumReleaseAgeStrict: true\nminimumReleaseAgeExclude:\n  - msw@3.0.3\n",
      "must be empty",
    ],
    [
      "exclusion shape",
      "minimumReleaseAge: 10080\nminimumReleaseAgeStrict: true\nminimumReleaseAgeExclude: all\n",
      "must be empty",
    ],
    [
      "override",
      "minimumReleaseAge: 10080\nminimumReleaseAgeStrict: true\noverrides:\n  left-pad: 1.0.0\n",
      "override left-pad",
    ],
    ["empty file", "", "at least 10080"],
    [
      "changed override",
      "minimumReleaseAge: 10080\nminimumReleaseAgeStrict: true\noverrides:\n  fsevents: 2.3.3\n",
      "override fsevents",
    ],
  ])("rejects a workspace problem: %s", (_name, workspace, message) => {
    expect(audit({ workspace }).join(" ")).toContain(message);
  });

  it("accepts an empty exclusion list", () => {
    expect(audit({ workspace: `${GOOD.workspace}minimumReleaseAgeExclude: []\n` })).toEqual([]);
  });

  it("rejects build scripts and non-registry resolutions in the lockfile", () => {
    expect(
      audit({ lockfile: "packages:\n  x@1.0.0:\n    requiresBuild: true\n" }).join(" "),
    ).toContain("requires a build script");
    expect(
      audit({
        lockfile: "packages:\n  x@1.0.0:\n    resolution: {tarball: https://example.test/x.tgz}\n",
      }).join(" "),
    ).toContain("non-registry resolution");
    expect(
      audit({ lockfile: "packages:\n  x@1.0.0:\n    resolution: {type: git, repo: x}\n" }).join(
        " ",
      ),
    ).toContain("non-registry resolution");
  });

  it("rejects build flags and remote resolutions regardless of YAML layout", () => {
    expect(
      audit({ lockfile: "packages:\n  x@1.0.0: {requiresBuild: true}\n" }).join(" "),
    ).toContain("requires a build script");
    expect(
      audit({
        lockfile:
          "packages:\n  x@1.0.0:\n    resolution:\n      tarball: https://example.test/x.tgz\n",
      }).join(" "),
    ).toContain("non-registry resolution");
    expect(audit({ lockfile: "packages: []\n" }).length).toBeGreaterThan(0);
    expect(
      audit({
        lockfile: `${GOOD.lockfile}snapshots:\n  react@19.3.0: {requiresBuild: true}\n`,
      }).join(" "),
    ).toContain("requires a build script");
  });
});

describe("the real project", () => {
  it("passes the audit", async () => {
    const root = path.resolve(import.meta.dirname, "..");

    expect(auditSupplyChain(await loadAuditInput(root))).toEqual([]);
  });
});
