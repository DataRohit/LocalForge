/**
 * Frontend architecture rules.
 *
 * Enforces the import direction between src layers, confines network and storage APIs to the
 * modules that own them, and forbids absolute URLs in application source.
 *
 * @packageDocumentation
 */

import { readdir, readFile } from "node:fs/promises";
import path from "node:path";

import ts from "typescript";

type Layer = "ui" | "features" | "api" | "session" | "routes" | "root";

const ALLOWED_IMPORTS: Readonly<Record<Layer, readonly Layer[]>> = {
  ui: ["ui"],
  features: ["features", "ui", "api", "session"],
  api: ["api"],
  session: ["session", "api"],
  routes: ["routes", "features", "session"],
  root: ["ui", "features", "api", "session", "routes", "root"],
};

const NETWORK_IDENTIFIERS: ReadonlySet<string> = new Set([
  "fetch",
  "XMLHttpRequest",
  "WebSocket",
  "EventSource",
]);

const STORAGE_IDENTIFIERS: ReadonlySet<string> = new Set([
  "localStorage",
  "sessionStorage",
  "indexedDB",
]);

/**
 * One source file to check.
 *
 * Pairs a path relative to the project root with its text.
 */
export interface SourceFile {
  readonly path: string;
  readonly text: string;
}

/**
 * Name the layer a src path belongs to.
 *
 * @param relative - Path below src, using forward slashes.
 * @returns The layer, with files directly in src grouped as the composition root.
 */
function layerOf(relative: string): Layer {
  const first = relative.split("/")[0] ?? "";
  return first === "ui" ||
    first === "features" ||
    first === "api" ||
    first === "session" ||
    first === "routes"
    ? first
    : "root";
}

/**
 * Tell whether a path is a test file.
 *
 * @param file - File path.
 * @returns True for colocated unit and integration tests.
 */
function isTest(file: string): boolean {
  return /\.test\.tsx?$/.test(file);
}

/**
 * Read the module specifiers a file imports or re-exports.
 *
 * @param sourceFile - Parsed file.
 * @returns The specifier strings in source order.
 */
function specifiers(sourceFile: ts.SourceFile): string[] {
  const found: string[] = [];
  for (const statement of sourceFile.statements) {
    if (
      (ts.isImportDeclaration(statement) || ts.isExportDeclaration(statement)) &&
      statement.moduleSpecifier !== undefined &&
      ts.isStringLiteral(statement.moduleSpecifier)
    ) {
      found.push(statement.moduleSpecifier.text);
    }
  }
  return found;
}

/**
 * Check the import direction of one file.
 *
 * Resolves each relative import against the file and compares the layers. Feature code may import
 * only its own feature, and only tests may import from outside src.
 *
 * @param file - Path below the project root, such as src/features/a/B.tsx.
 * @param modules - Module specifiers the file uses.
 * @returns One message per forbidden import.
 */
function checkImports(file: string, modules: readonly string[]): string[] {
  const relative = file.slice("src/".length);
  const layer = layerOf(relative);
  const problems: string[] = [];
  for (const specifier of modules.filter((candidate) => candidate.startsWith("."))) {
    const target = path.posix.normalize(path.posix.join(path.posix.dirname(file), specifier));
    if (!target.startsWith("src/")) {
      if (!isTest(file)) {
        problems.push(`${file}: imports ${specifier}, which is outside src.`);
      }
      continue;
    }
    const targetRelative = target.slice("src/".length);
    const targetLayer = layerOf(targetRelative);
    if (!ALLOWED_IMPORTS[layer].includes(targetLayer)) {
      problems.push(
        `${file}: the ${layer} layer may not import the ${targetLayer} layer (${specifier}).`,
      );
    } else if (
      layer === "features" &&
      targetLayer === "features" &&
      targetRelative.split("/")[1] !== relative.split("/")[1]
    ) {
      problems.push(`${file}: a feature may not import another feature (${specifier}).`);
    }
  }
  return problems;
}

/**
 * Check identifier and literal rules for one file.
 *
 * Network globals belong to api, storage globals and build variables belong nowhere, and string
 * literals in application source must not be absolute http or https URLs.
 *
 * @param file - Path below the project root.
 * @param sourceFile - Parsed file.
 * @returns One message per violation.
 */
function checkUsage(file: string, sourceFile: ts.SourceFile): string[] {
  const problems: string[] = [];
  const inApi = file.startsWith("src/api/");
  const test = isTest(file);
  const visit = (node: ts.Node): void => {
    if (ts.isIdentifier(node) && !ts.isPropertyAccessExpression(node.parent)) {
      if (NETWORK_IDENTIFIERS.has(node.text) && !inApi) {
        problems.push(`${file}: ${node.text} may be used only by the api module.`);
      }
      if (STORAGE_IDENTIFIERS.has(node.text)) {
        problems.push(`${file}: ${node.text} is forbidden; the frontend holds no browser storage.`);
      }
    }
    if (
      ts.isPropertyAccessExpression(node) &&
      node.name.text === "env" &&
      ts.isMetaProperty(node.expression)
    ) {
      problems.push(`${file}: import.meta.env is forbidden; the build has no variables.`);
    }
    if (
      ts.isPropertyAccessExpression(node) &&
      node.name.text === "sendBeacon" &&
      node.expression.getText(sourceFile) === "navigator"
    ) {
      problems.push(`${file}: navigator.sendBeacon is forbidden.`);
    }
    if (
      !test &&
      (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) &&
      /^https?:\/\//.test(node.text)
    ) {
      problems.push(`${file}: absolute URL ${node.text}; every URL must be relative.`);
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return problems;
}

/**
 * Check a set of source files against the architecture rules.
 *
 * Applies the import-direction rule to every file below src and the usage rules to every file
 * below src, ignoring generated declarations.
 *
 * @param files - Files to check, with paths relative to the project root.
 * @returns One message per violation, sorted by file.
 */
export function checkArchitecture(files: readonly SourceFile[]): string[] {
  const problems: string[] = [];
  for (const file of files) {
    if (!file.path.startsWith("src/") || file.path.endsWith(".d.ts")) {
      continue;
    }
    const kind = file.path.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
    const parsed = ts.createSourceFile(file.path, file.text, ts.ScriptTarget.ESNext, true, kind);
    problems.push(...checkImports(file.path, specifiers(parsed)), ...checkUsage(file.path, parsed));
  }
  return problems.sort();
}

/**
 * Load every TypeScript file below src.
 *
 * Reads the tree recursively and returns each file with a root-relative path that uses forward
 * slashes, so the rules behave the same on every platform.
 *
 * @param root - Absolute project root.
 * @returns The files with root-relative forward-slash paths.
 */
export async function loadSources(root: string): Promise<SourceFile[]> {
  const entries = await readdir(path.join(root, "src"), { recursive: true, withFileTypes: true });
  const files: SourceFile[] = [];
  for (const entry of entries) {
    if (entry.isFile() && /\.tsx?$/.test(entry.name)) {
      const full = path.join(entry.parentPath, entry.name);
      files.push({
        path: path.relative(root, full).split(path.sep).join("/"),
        text: await readFile(full, "utf8"),
      });
    }
  }
  return files;
}
