/**
 * Production bundle checks.
 *
 * Measures the gzip size of the initial JavaScript and CSS against the budgets, and rejects inline
 * scripts, inline event handlers, and any absolute URL that is not an inert known identifier.
 *
 * @packageDocumentation
 */

import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { gzipSync } from "node:zlib";

/**
 * Initial JavaScript budget.
 *
 * Gzip bytes the scripts referenced by index.html may total, using kilobytes of 1000 bytes.
 */
export const JAVASCRIPT_BUDGET_BYTES = 200_000;

/**
 * Initial CSS budget.
 *
 * Gzip bytes the stylesheets referenced by index.html may total, using kilobytes of 1000 bytes.
 */
export const CSS_BUDGET_BYTES = 25_000;

const TEXT_EXTENSIONS = new Set([".js", ".css", ".html", ".svg", ".json", ".txt", ".webmanifest"]);

const INERT_URLS: readonly RegExp[] = [
  /^http:\/\/www\.w3\.org\/(1999\/xhtml|2000\/svg|1998\/Math\/MathML|1999\/xlink|XML\/1998\/namespace|2000\/xmlns\/?)$/,
  /^https:\/\/react\.dev\/errors\/?$/,
];

const CSS_BANNER_URL = /^https:\/\/tailwindcss\.com$/;

/**
 * Result of analysing a built bundle.
 *
 * Carries the measured sizes and every problem found, empty when the bundle passes.
 */
export interface BundleReport {
  readonly javascriptGzipBytes: number;
  readonly cssGzipBytes: number;
  readonly problems: readonly string[];
}

/**
 * Assets and inline code referenced by the document.
 *
 * Lists the local script and stylesheet paths and flags inline scripts and handlers.
 */
export interface DocumentAssets {
  readonly scripts: readonly string[];
  readonly styles: readonly string[];
  readonly inlineScript: boolean;
  readonly inlineHandler: boolean;
}

/**
 * Read the assets an HTML document loads.
 *
 * Collects module scripts, module preloads, and stylesheets, and reports inline scripts and
 * inline event handler attributes, which the content security policy forbids.
 *
 * @param html - Built index.html text.
 * @returns The referenced assets and the inline-code flags.
 */
export function readDocumentAssets(html: string): DocumentAssets {
  const scripts = [
    ...html.matchAll(/<script[^>]*\ssrc="([^"]+)"/g),
    ...html.matchAll(/<link[^>]*rel="modulepreload"[^>]*\shref="([^"]+)"/g),
  ].map((match) => match[1] ?? "");
  const styles = [...html.matchAll(/<link[^>]*rel="stylesheet"[^>]*\shref="([^"]+)"/g)].map(
    (match) => match[1] ?? "",
  );
  const inlineScript = [...html.matchAll(/<script(?![^>]*\ssrc=)[^>]*>([\s\S]*?)<\/script>/g)].some(
    (match) => (match[1] ?? "").trim() !== "",
  );
  const inlineHandler = /\son[a-z]+\s*=/i.test(html);
  return { scripts, styles, inlineScript, inlineHandler };
}

/**
 * Find absolute URLs that are not on the inert allowlist.
 *
 * Extracts every http or https URL from the text and keeps those that are neither a known
 * namespace or message prefix nor, in a stylesheet, the Tailwind licence banner.
 *
 * @param file - File name, used to apply the stylesheet-only banner allowance.
 * @param text - File text.
 * @returns The offending URLs.
 */
export function findRemoteUrls(file: string, text: string): string[] {
  const urls = text.match(/https?:\/\/[^\s"'`)<>\\]+/g) ?? [];
  return urls.filter(
    (url) =>
      !INERT_URLS.some((pattern) => pattern.test(url)) &&
      !(file.endsWith(".css") && CSS_BANNER_URL.test(url)),
  );
}

/**
 * Measure the gzip size of a set of built files.
 *
 * @param directory - The build output directory.
 * @param assets - Asset paths as written in the document, starting with a slash.
 * @returns The total gzip bytes and the assets that are missing.
 */
async function gzipTotal(
  directory: string,
  assets: readonly string[],
): Promise<{ bytes: number; missing: string[] }> {
  let bytes = 0;
  const missing: string[] = [];
  for (const asset of assets) {
    const content = await readFile(path.join(directory, asset)).catch(() => null);
    if (content === null) {
      missing.push(asset);
    } else {
      bytes += gzipSync(content, { level: 9 }).length;
    }
  }
  return { bytes, missing };
}

/**
 * Analyse a built bundle.
 *
 * Reads index.html, measures the referenced assets against the budgets, and scans every text file
 * in the output for inline code and remote URLs.
 *
 * @param directory - The build output directory.
 * @returns The sizes and the problems found.
 */
export async function analyzeBundle(directory: string): Promise<BundleReport> {
  const problems: string[] = [];
  const html = await readFile(path.join(directory, "index.html"), "utf8");
  const assets = readDocumentAssets(html);
  if (assets.inlineScript) {
    problems.push("index.html contains an inline script.");
  }
  if (assets.inlineHandler) {
    problems.push("index.html contains an inline event handler.");
  }
  const script = await gzipTotal(directory, assets.scripts);
  const style = await gzipTotal(directory, assets.styles);
  for (const asset of [...script.missing, ...style.missing]) {
    problems.push(`index.html references ${asset}, which is missing.`);
  }
  if (script.bytes > JAVASCRIPT_BUDGET_BYTES) {
    problems.push(`initial JavaScript is ${String(script.bytes)} gzip bytes, over the budget.`);
  }
  if (style.bytes > CSS_BUDGET_BYTES) {
    problems.push(`initial CSS is ${String(style.bytes)} gzip bytes, over the budget.`);
  }
  const entries = await readdir(directory, { recursive: true, withFileTypes: true });
  for (const entry of entries) {
    if (entry.isFile() && TEXT_EXTENSIONS.has(path.extname(entry.name))) {
      const full = path.join(entry.parentPath, entry.name);
      const relative = path.relative(directory, full).split(path.sep).join("/");
      for (const url of findRemoteUrls(entry.name, await readFile(full, "utf8"))) {
        problems.push(`${relative} contains the remote URL ${url}.`);
      }
    }
  }
  return { javascriptGzipBytes: script.bytes, cssGzipBytes: style.bytes, problems };
}

if (import.meta.main) {
  const report = await analyzeBundle(path.resolve(import.meta.dirname, "..", "dist"));
  console.log(
    `Initial JavaScript ${String(report.javascriptGzipBytes)} gzip bytes ` +
      `of ${String(JAVASCRIPT_BUDGET_BYTES)}; CSS ${String(report.cssGzipBytes)} of ${String(CSS_BUDGET_BYTES)}.`,
  );
  for (const problem of report.problems) {
    console.error(problem);
  }
  if (report.problems.length > 0) {
    process.exitCode = 1;
  } else {
    console.log("Bundle checks: no problems.");
  }
}
