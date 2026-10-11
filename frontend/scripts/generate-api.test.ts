/**
 * Tests for the OpenAPI generator.
 *
 * Covers operation extraction, module rendering, writing, and the drift check against a tiny
 * specification so failures name the exact generator branch.
 *
 * @packageDocumentation
 */

import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  readExamples,
  readOperations,
  renderExamplesModule,
  renderOperationsModule,
  run,
} from "./generate-api.ts";

const SPEC = `openapi: 3.1.0
info:
  title: Tiny
  version: 1.0.0
paths:
  /things/:
    get:
      operationId: things_list
      responses:
        "200":
          description: ok
          content:
            application/json:
              example:
                total: 1
        "429":
          description: slow down
          content:
            application/json:
              examples:
                Slow:
                  value:
                    code: throttled
    post:
      operationId: things_create
      security:
        - jwtAuth: []
      responses:
        "201":
          description: made
        default:
          description: other
components:
  securitySchemes:
    jwtAuth:
      type: http
      scheme: bearer
`;

describe("readOperations", () => {
  it("collects identifiers, numeric statuses, and authentication", () => {
    const entries = readOperations({
      paths: {
        "/a/": {
          get: { operationId: "a_get", responses: { "404": {}, "200": {}, default: {} } },
          post: {
            operationId: "a_post",
            security: [{ jwtAuth: [] }],
            requestBody: { content: {} },
            responses: { "204": {} },
          },
          delete: { operationId: "a_delete", security: [{}], responses: { "204": {} } },
        },
      },
    });

    expect(entries).toEqual([
      {
        id: "a_get",
        method: "get",
        path: "/a/",
        statuses: [200, 404],
        authenticated: false,
        hasBody: false,
      },
      {
        id: "a_post",
        method: "post",
        path: "/a/",
        statuses: [204],
        authenticated: true,
        hasBody: true,
      },
      {
        id: "a_delete",
        method: "delete",
        path: "/a/",
        statuses: [204],
        authenticated: false,
        hasBody: false,
      },
    ]);
  });

  it.each([
    ["a missing document", undefined],
    ["a document without paths", {}],
    ["a path that is not an object", { paths: { "/a/": 1 } }],
    ["an operation without an identifier", { paths: { "/a/": { get: { responses: {} } } } }],
    ["an operation that is not an object", { paths: { "/a/": { get: "nope" } } }],
    ["an operation without responses", { paths: { "/a/": { get: { operationId: "a_get" } } } }],
  ])("rejects %s", (_name, document) => {
    expect(() => readOperations(document)).toThrow(Error);
  });

  it("returns no entries for a path with no supported method", () => {
    expect(readOperations({ paths: { "/a/": { parameters: [] } } })).toEqual([]);
  });
});

describe("readExamples", () => {
  it("collects single examples and named examples by operation and status", () => {
    const table = readExamples({
      paths: {
        "/a/": {
          get: {
            operationId: "a_get",
            responses: {
              "200": { content: { "application/json": { example: { ok: true } } } },
              "400": {
                content: {
                  "application/json": {
                    examples: { First: { value: { code: "a" } }, Second: { value: { code: "b" } } },
                  },
                },
              },
              "404": { content: { "text/plain": { example: "no" } } },
              "500": { description: "no content" },
              "503": "not an object",
            },
          },
          post: { operationId: "a_post" },
          parameters: [],
        },
        "/b/": "not an object",
        "/c/": { get: { responses: {} } },
      },
    });

    expect(table).toEqual({
      a_get: { "200": [{ ok: true }], "400": [{ code: "a" }, { code: "b" }] },
      a_post: {},
    });
  });

  it("skips an example entry that has no value", () => {
    const table = readExamples({
      paths: {
        "/a/": {
          get: {
            operationId: "a_get",
            responses: {
              "200": { content: { "application/json": { examples: { Empty: {}, Odd: 1 } } } },
            },
          },
        },
      },
    });

    expect(table).toEqual({ a_get: {} });
  });

  it("rejects a document without paths", () => {
    expect(() => readExamples({})).toThrow(Error);
    expect(() => readExamples(undefined)).toThrow(Error);
  });
});

describe("renderExamplesModule", () => {
  it("renders the table as a typed constant", () => {
    const text = renderExamplesModule({ a_get: { "200": [{ ok: true }] } });

    expect(text).toContain("export const examples: Readonly<");
    expect(text).toContain('"a_get"');
    expect(text).toContain('"ok": true');
  });
});

describe("renderOperationsModule", () => {
  it("renders a typed constant keyed by identifier", () => {
    const text = renderOperationsModule([
      {
        id: "a_get",
        method: "get",
        path: "/a/",
        statuses: [200, 404],
        authenticated: true,
        hasBody: false,
      },
    ]);

    expect(text).toContain("a_get: {");
    expect(text).toContain('path: "/a/",');
    expect(text).toContain("statuses: [200, 404],");
    expect(text).toContain("authenticated: true,");
    expect(text).toContain("hasBody: false,");
    expect(text).toContain("export type OperationId = keyof typeof operations;");
  });
});

describe("run", () => {
  let directory = "";

  beforeEach(async () => {
    directory = await mkdtemp(path.join(os.tmpdir(), "lf-api-"));
    await writeFile(path.join(directory, "spec.yaml"), SPEC, "utf8");
  });

  afterEach(async () => {
    await rm(directory, { recursive: true, force: true });
  });

  it("writes both files and then reports no drift", async () => {
    const options = {
      specPath: path.join(directory, "spec.yaml"),
      outputDirectory: path.join(directory, "out"),
      check: false,
    };

    const written = await run(options);
    const checked = await run({ ...options, check: true });

    expect(written).toEqual({ ok: true, changed: ["schema.d.ts", "operations.ts", "examples.ts"] });
    expect(checked).toEqual({ ok: true, changed: [] });
    expect(await readFile(path.join(directory, "out", "operations.ts"), "utf8")).toContain(
      "things_create",
    );
  });

  it("fails the check when a committed file is missing or stale", async () => {
    const out = path.join(directory, "out");
    await mkdir(out);
    await writeFile(path.join(out, "operations.ts"), "stale", "utf8");

    const result = await run({
      specPath: path.join(directory, "spec.yaml"),
      outputDirectory: out,
      check: true,
    });

    expect(result).toEqual({ ok: false, changed: ["schema.d.ts", "operations.ts", "examples.ts"] });
    expect(await readFile(path.join(out, "operations.ts"), "utf8")).toBe("stale");
  });
});
