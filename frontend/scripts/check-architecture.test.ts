/**
 * Tests for the architecture rules.
 *
 * Proves each rule fires on a fixture and that the real source tree obeys all of them.
 * The real-tree test is the enforcement the frontend standard asks for.
 *
 * @packageDocumentation
 */

import path from "node:path";

import { describe, expect, it } from "vitest";

import { checkArchitecture, loadSources } from "./check-architecture.ts";

function check(file: string, text: string): string[] {
  return checkArchitecture([{ path: file, text }]);
}

describe("import direction", () => {
  it.each([
    [
      "src/ui/Button.tsx",
      'import { x } from "../api/client";',
      "ui layer may not import the api layer",
    ],
    [
      "src/api/client.ts",
      'import { s } from "../session/state";',
      "api layer may not import the session layer",
    ],
    [
      "src/session/state.ts",
      'import { f } from "../features/a/A";',
      "session layer may not import the features layer",
    ],
    [
      "src/routes/table.tsx",
      'import { b } from "../ui/Button";',
      "routes layer may not import the ui layer",
    ],
    [
      "src/features/a/A.tsx",
      'import { b } from "../../routes/table";',
      "features layer may not import the routes layer",
    ],
    [
      "src/features/a/A.tsx",
      'import { b } from "../b/B";',
      "a feature may not import another feature",
    ],
    ["src/api/client.ts", 'import { x } from "../../vendor/x";', "outside src"],
  ])("rejects %s importing %s", (file, text, message) => {
    expect(check(file, text).join(" ")).toContain(message);
  });

  it.each([
    ["src/ui/Button.tsx", 'import { c } from "./cn";'],
    ["src/features/a/A.tsx", 'import { b } from "../../ui/Button";\nimport { c } from "./local";'],
    [
      "src/features/a/A.tsx",
      'import { b } from "../../api/client";\nimport { s } from "../../session/state";',
    ],
    ["src/session/state.ts", 'import { c } from "../api/client";'],
    [
      "src/routes/table.tsx",
      'import { a } from "../features/a/A";\nimport { s } from "../session/state";',
    ],
    ["src/App.tsx", 'import { r } from "./routes/table";\nimport { u } from "./ui/Button";'],
    ["src/ui/Button.tsx", 'import react from "react";'],
    ["src/ui/Button.test.tsx", 'import { helper } from "../../test/helper";'],
    ["src/api/generated/operations.d.ts", 'import { x } from "../../../../elsewhere";'],
  ])("accepts %s importing %s", (file, text) => {
    expect(check(file, text)).toEqual([]);
  });

  it("checks re-exports", () => {
    expect(check("src/ui/Button.tsx", 'export { x } from "../api/client";').length).toBe(1);
  });
});

describe("usage rules", () => {
  it.each([
    ["fetch", "const r = fetch('/x');"],
    ["XMLHttpRequest", "const r = new XMLHttpRequest();"],
    ["WebSocket", "const s = new WebSocket('/ws/');"],
    ["EventSource", "const s = new EventSource('/e');"],
  ])("confines %s to the api module", (name, text) => {
    expect(check("src/features/a/A.ts", text)).toEqual([
      `src/features/a/A.ts: ${name} may be used only by the api module.`,
    ]);
    expect(check("src/api/client.ts", text)).toEqual([]);
  });

  it.each(["localStorage", "sessionStorage", "indexedDB"])("forbids %s everywhere", (name) => {
    const text = `const v = ${name};`;

    expect(check("src/api/client.ts", text).join(" ")).toContain("holds no browser storage");
    expect(check("src/features/a/A.ts", text).join(" ")).toContain("holds no browser storage");
  });

  it("forbids sendBeacon", () => {
    expect(check("src/features/a/A.ts", "navigator.sendBeacon('/x');")).toEqual([
      "src/features/a/A.ts: navigator.sendBeacon is forbidden.",
    ]);
  });

  it("forbids build-time variables", () => {
    expect(check("src/features/a/A.ts", "const v = import.meta.env.MODE;").join(" ")).toContain(
      "import.meta.env is forbidden",
    );
  });

  it("does not flag property names that merely match", () => {
    expect(check("src/features/a/A.ts", "const v = other.fetch;")).toEqual([]);
  });

  it("forbids absolute URLs in application source but not in tests", () => {
    expect(check("src/features/a/A.ts", "const u = 'https://example.test/x';").join(" ")).toContain(
      "every URL must be relative",
    );
    expect(check("src/features/a/A.ts", "const u = `http://example.test/x`;").join(" ")).toContain(
      "absolute URL",
    );
    expect(check("src/features/a/A.test.ts", "const u = 'https://example.test/x';")).toEqual([]);
    expect(check("src/features/a/A.ts", "const u = '/api/v1/x/';")).toEqual([]);
  });

  it("ignores files outside src and declaration files", () => {
    expect(check("scripts/x.ts", "const r = fetch('/x');")).toEqual([]);
    expect(check("src/api/generated/schema.d.ts", "declare const r: typeof fetch;")).toEqual([]);
  });
});

describe("the real source tree", () => {
  it("obeys every architecture rule", async () => {
    const root = path.resolve(import.meta.dirname, "..");

    const problems = checkArchitecture(await loadSources(root));

    expect(problems).toEqual([]);
  });
});
