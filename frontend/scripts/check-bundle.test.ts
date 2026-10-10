/**
 * Tests for the production bundle checks.
 *
 * Builds small output directories to prove each budget and each forbidden pattern is detected.
 * The allowlist is tested in both directions.
 *
 * @packageDocumentation
 */

import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  analyzeBundle,
  CSS_BUDGET_BYTES,
  findRemoteUrls,
  JAVASCRIPT_BUDGET_BYTES,
  readDocumentAssets,
} from "./check-bundle.ts";

const PAGE = `<!doctype html><html><head>
<script type="module" crossorigin src="/assets/app.js"></script>
<link rel="modulepreload" crossorigin href="/assets/vendor.js">
<link rel="stylesheet" crossorigin href="/assets/app.css">
</head><body><div id="root"></div></body></html>`;

function randomText(bytes: number): string {
  let state = 12345;
  let text = "";
  while (text.length < bytes) {
    state = (state * 1103515245 + 12345) % 2147483648;
    text += state.toString(36);
  }
  return text.slice(0, bytes);
}

describe("readDocumentAssets", () => {
  it("collects scripts, preloads, and stylesheets", () => {
    expect(readDocumentAssets(PAGE)).toEqual({
      scripts: ["/assets/app.js", "/assets/vendor.js"],
      styles: ["/assets/app.css"],
      inlineScript: false,
      inlineHandler: false,
    });
  });

  it("flags inline scripts and inline handlers but not empty script elements", () => {
    expect(readDocumentAssets("<script>alert(1)</script>").inlineScript).toBe(true);
    expect(readDocumentAssets('<script src="/a.js"></script>').inlineScript).toBe(false);
    expect(readDocumentAssets('<script src="/a.js"></script><script> </script>').inlineScript).toBe(
      false,
    );
    expect(readDocumentAssets('<body onload="x()">').inlineHandler).toBe(true);
    expect(readDocumentAssets('<a href="/x">one</a>').inlineHandler).toBe(false);
  });
});

describe("findRemoteUrls", () => {
  it.each([
    "http://www.w3.org/2000/svg",
    "http://www.w3.org/1999/xlink",
    "http://www.w3.org/XML/1998/namespace",
    "http://www.w3.org/1998/Math/MathML",
    "http://www.w3.org/1999/xhtml",
    "https://react.dev/errors/",
    "https://reactrouter.com/en/main/routers/picking-a-router",
    "https://json-schema.org/draft/2020-12/schema",
    "http://json-schema.org/draft-07/schema#",
    "http://json-schema.org/draft-04/schema#",
    "http://localhost",
    "http://[${e}]",
  ])("allows the inert identifier %s", (url) => {
    expect(findRemoteUrls("app.js", `const a = "${url}";`)).toEqual([]);
  });

  it("ignores trailing sentence punctuation when matching", () => {
    expect(
      findRemoteUrls("app.js", "see https://reactrouter.com/en/main/routers/picking-a-router."),
    ).toEqual([]);
    expect(findRemoteUrls("app.js", "go to https://cdn.example.test/x.")).toEqual([
      "https://cdn.example.test/x",
    ]);
  });

  it("allows the Tailwind banner only in a stylesheet", () => {
    const banner = "/*! tailwindcss v4 | MIT License | https://tailwindcss.com */";

    expect(findRemoteUrls("app.css", banner)).toEqual([]);
    expect(findRemoteUrls("app.js", banner)).toEqual(["https://tailwindcss.com"]);
  });

  it.each([
    "https://cdn.example.test/lib.js",
    "http://www.google-analytics.com/collect",
    "https://o1.ingest.sentry.io/api",
    "http://www.w3.org/2000/other",
  ])("rejects %s", (url) => {
    expect(findRemoteUrls("app.js", `fetch("${url}")`)).toEqual([url]);
  });
});

describe("analyzeBundle", () => {
  let directory = "";

  beforeEach(async () => {
    directory = await mkdtemp(path.join(os.tmpdir(), "lf-bundle-"));
    await mkdir(path.join(directory, "assets"));
  });

  afterEach(async () => {
    await rm(directory, { recursive: true, force: true });
  });

  async function write(file: string, text: string): Promise<void> {
    await writeFile(path.join(directory, file), text, "utf8");
  }

  it("passes a small clean bundle", async () => {
    await write("index.html", PAGE);
    await write("assets/app.js", "console.log(1);");
    await write("assets/vendor.js", "console.log(2);");
    await write("assets/app.css", "body{margin:0}");

    const report = await analyzeBundle(directory);

    expect(report.problems).toEqual([]);
    expect(report.javascriptGzipBytes).toBeGreaterThan(0);
    expect(report.cssGzipBytes).toBeGreaterThan(0);
  });

  it("fails when the JavaScript or CSS budget is exceeded", async () => {
    await write("index.html", PAGE);
    await write("assets/app.js", randomText(JAVASCRIPT_BUDGET_BYTES * 2));
    await write("assets/vendor.js", "x");
    await write("assets/app.css", randomText(CSS_BUDGET_BYTES * 2));

    const report = await analyzeBundle(directory);

    expect(report.problems.join(" ")).toContain("initial JavaScript is");
    expect(report.problems.join(" ")).toContain("initial CSS is");
  });

  it("fails on a missing asset, inline code, and a remote URL", async () => {
    await write("index.html", `${PAGE}<script>boot()</script><body onclick="x()">`);
    await write("assets/app.js", 'fetch("https://cdn.example.test/x.js")');
    await write("assets/app.css", "body{margin:0}");

    const report = await analyzeBundle(directory);

    expect(report.problems).toEqual(
      expect.arrayContaining([
        "index.html contains an inline script.",
        "index.html contains an inline event handler.",
        "index.html references /assets/vendor.js, which is missing.",
        "assets/app.js contains the remote URL https://cdn.example.test/x.js.",
      ]),
    );
  });

  it("ignores binary files when scanning for URLs", async () => {
    await write("index.html", PAGE);
    await write("assets/app.js", "1");
    await write("assets/vendor.js", "2");
    await write("assets/app.css", "3");
    await write("assets/logo.png", "https://cdn.example.test/hidden");

    expect((await analyzeBundle(directory)).problems).toEqual([]);
  });
});
