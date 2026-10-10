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

import { auditSupplyChain, loadAuditInput, type AuditInput } from "./audit-supply-chain.ts";

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
        lockfile: "  x@1.0.0:\n    resolution: {tarball: https://example.test/x.tgz}\n",
      }).join(" "),
    ).toContain("non-registry resolution");
    expect(
      audit({ lockfile: "  x@1.0.0:\n    resolution: {type: git, repo: x}\n" }).join(" "),
    ).toContain("non-registry resolution");
  });
});

describe("the real project", () => {
  it("passes the audit", async () => {
    const root = path.resolve(import.meta.dirname, "..");

    expect(auditSupplyChain(await loadAuditInput(root))).toEqual([]);
  });
});
