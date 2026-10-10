/**
 * TSDoc and no-comments checker.
 *
 * Enforces the frontend documentation standard with the TypeScript compiler API: no comments other
 * than attached TSDoc blocks, a file header, and a structured block on every exported symbol.
 *
 * @packageDocumentation
 */

import { readdir, readFile } from "node:fs/promises";
import path from "node:path";

import ts from "typescript";

const MAX_LINE_LENGTH = 100;
const MIN_FUNCTION_DESCRIPTION_LINES = 2;
const MIN_DECLARATION_DESCRIPTION_LINES = 1;
const MAX_DESCRIPTION_LINES = 3;
const SCANNED_DIRECTORIES = ["src", "test", "scripts", "e2e"];
const SCANNED_ROOT_FILES = ["eslint.config.js", "vite.config.ts", "playwright.config.ts"];
const SKIPPED_SEGMENTS = new Set(["node_modules", "generated", "dist", "coverage"]);
const NO_RESULT_TYPES = new Set(["void", "never", "Promise<void>", "Promise<never>"]);

const DOCUMENTABLE_KINDS: ReadonlySet<ts.SyntaxKind> = new Set([
  ts.SyntaxKind.FunctionDeclaration,
  ts.SyntaxKind.VariableStatement,
  ts.SyntaxKind.InterfaceDeclaration,
  ts.SyntaxKind.TypeAliasDeclaration,
  ts.SyntaxKind.ClassDeclaration,
  ts.SyntaxKind.EnumDeclaration,
  ts.SyntaxKind.PropertySignature,
  ts.SyntaxKind.MethodSignature,
  ts.SyntaxKind.PropertyDeclaration,
  ts.SyntaxKind.MethodDeclaration,
  ts.SyntaxKind.EnumMember,
  ts.SyntaxKind.ImportDeclaration,
  ts.SyntaxKind.ExportDeclaration,
  ts.SyntaxKind.ExportAssignment,
]);

/**
 * One documentation violation.
 *
 * Carries the file, one-based line, and message printed by the checker.
 */
export interface Violation {
  readonly file: string;
  readonly line: number;
  readonly message: string;
}

type FunctionLike = ts.FunctionDeclaration | ts.ArrowFunction | ts.FunctionExpression;

/**
 * Parsed structure of one documentation block.
 */
interface ParsedDoc {
  readonly title: string;
  readonly titleIsSingleLine: boolean;
  readonly description: readonly string[];
  readonly tags: readonly string[];
}

/**
 * Split a documentation block into title, description, and tags.
 *
 * Removes the comment delimiters and leading asterisks, reads the first line as the title, and
 * collects non-empty lines up to the first tag as the description.
 *
 * @param block - Full text of one block comment, including its delimiters.
 * @returns The parsed structure.
 */
export function parseDoc(block: string): ParsedDoc {
  const lines = block
    .replace(/^\/\*\*/, "")
    .replace(/\*\/$/, "")
    .split("\n")
    .map((line) => line.replace(/^\s*\*? ?/, "").trimEnd());
  while (lines.length > 0 && lines[0] === "") {
    lines.shift();
  }
  while (lines.length > 0 && lines.at(-1) === "") {
    lines.pop();
  }
  const title = lines[0] ?? "";
  const afterTitle = lines.slice(1);
  const firstTag = afterTitle.findIndex((line) => line.startsWith("@"));
  const body = firstTag === -1 ? afterTitle : afterTitle.slice(0, firstTag);
  const tags =
    firstTag === -1 ? [] : afterTitle.slice(firstTag).filter((line) => line.startsWith("@"));
  return {
    title,
    titleIsSingleLine: body[0] === undefined || body[0] === "",
    description: body.filter((line) => line !== ""),
    tags,
  };
}

/**
 * Check the shape shared by every documentation block.
 *
 * Verifies a non-tag title on its own line, a description of the required length, and the line
 * length limit on the raw block text.
 *
 * @param block - Full text of the block comment.
 * @param label - Name used in messages, such as the file header or a symbol.
 * @param minimum - Fewest description lines the block must carry.
 * @returns One message per problem, empty when the block is well formed.
 */
export function checkBlockShape(block: string, label: string, minimum: number): string[] {
  const doc = parseDoc(block);
  const problems: string[] = [];
  if (doc.title === "" || doc.title.startsWith("@")) {
    problems.push(`${label}: the block needs a one-line title.`);
  }
  if (!doc.titleIsSingleLine) {
    problems.push(`${label}: separate the title from the description with a blank line.`);
  }
  if (doc.description.length < minimum || doc.description.length > MAX_DESCRIPTION_LINES) {
    problems.push(
      `${label}: the description needs ${String(minimum)} to ` +
        `${String(MAX_DESCRIPTION_LINES)} lines, found ${String(doc.description.length)}.`,
    );
  }
  if (block.split("\n").some((line) => line.length > MAX_LINE_LENGTH)) {
    problems.push(`${label}: a line exceeds ${String(MAX_LINE_LENGTH)} characters.`);
  }
  return problems;
}

/**
 * Find the function behind an exported declaration.
 *
 * Returns the declaration itself for a function and the initializer for a constant arrow or
 * function expression, so parameter and result rules apply to both.
 *
 * @param statement - Top-level statement to inspect.
 * @returns The function node, or undefined when the statement is not a function.
 */
function functionOf(statement: ts.Statement): FunctionLike | undefined {
  if (ts.isFunctionDeclaration(statement)) {
    return statement;
  }
  if (!ts.isVariableStatement(statement)) {
    return undefined;
  }
  const initializer = statement.declarationList.declarations[0]?.initializer;
  if (
    initializer !== undefined &&
    (ts.isArrowFunction(initializer) || ts.isFunctionExpression(initializer))
  ) {
    return initializer;
  }
  return undefined;
}

/**
 * Search a function body for a node kind without entering nested functions.
 *
 * Used to decide whether a function can throw or returns a value.
 *
 * @param node - Node to search below.
 * @param matches - Predicate selecting the nodes of interest.
 * @returns True when a matching node exists in this function's own body.
 */
function containsOwn(node: ts.Node, matches: (candidate: ts.Node) => boolean): boolean {
  let found = false;
  const visit = (child: ts.Node): void => {
    if (found) {
      return;
    }
    if (matches(child)) {
      found = true;
      return;
    }
    if (!ts.isFunctionLike(child)) {
      ts.forEachChild(child, visit);
    }
  };
  ts.forEachChild(node, visit);
  return found;
}

/**
 * Decide whether a function documents a result.
 *
 * Uses the declared return type when present and otherwise looks for a returned value.
 *
 * @param fn - Function node.
 * @param sourceFile - File containing the node.
 * @returns True when a `@returns` tag is required.
 */
function returnsValue(fn: FunctionLike, sourceFile: ts.SourceFile): boolean {
  if (fn.type !== undefined) {
    return !NO_RESULT_TYPES.has(fn.type.getText(sourceFile).replace(/\s+/g, ""));
  }
  if (ts.isArrowFunction(fn) && !ts.isBlock(fn.body)) {
    return true;
  }
  return containsOwn(
    fn,
    (candidate) => ts.isReturnStatement(candidate) && candidate.expression !== undefined,
  );
}

/**
 * Check the tags a function-like symbol must carry.
 *
 * Requires one `@param` per identifier parameter by name, at least one for destructured
 * parameters, `@returns` for a value, and `@throws` when the body contains a throw.
 *
 * @param fn - Function node.
 * @param block - Documentation block text.
 * @param sourceFile - File containing the node.
 * @param label - Name used in messages.
 * @returns One message per missing tag.
 */
function checkFunctionTags(
  fn: FunctionLike,
  block: string,
  sourceFile: ts.SourceFile,
  label: string,
): string[] {
  const tags = parseDoc(block).tags;
  const documented = tags
    .filter((tag) => tag.startsWith("@param "))
    .map((tag) => tag.slice("@param ".length).split(/[\s-]/)[0] ?? "");
  const problems: string[] = [];
  const unnamed = fn.parameters.filter((parameter) => !ts.isIdentifier(parameter.name));
  for (const parameter of fn.parameters) {
    if (ts.isIdentifier(parameter.name) && !documented.includes(parameter.name.text)) {
      problems.push(`${label}: missing @param ${parameter.name.text}.`);
    }
  }
  if (unnamed.length > 0 && documented.length < fn.parameters.length) {
    problems.push(`${label}: document the destructured parameter with @param.`);
  }
  if (returnsValue(fn, sourceFile) && !tags.some((tag) => tag.startsWith("@returns"))) {
    problems.push(`${label}: missing @returns.`);
  }
  const throws = containsOwn(fn, ts.isThrowStatement);
  if (throws && !tags.some((tag) => tag.startsWith("@throws"))) {
    problems.push(`${label}: missing @throws.`);
  }
  return problems;
}

/**
 * Name an exported declaration for messages.
 *
 * @param statement - Top-level statement.
 * @returns The declared name, or a generic label for unnamed declarations.
 */
function nameOf(statement: ts.Statement): string {
  if (ts.isVariableStatement(statement)) {
    return statement.declarationList.declarations[0]?.name.getText() ?? "export";
  }
  if (
    (ts.isFunctionDeclaration(statement) ||
      ts.isInterfaceDeclaration(statement) ||
      ts.isTypeAliasDeclaration(statement) ||
      ts.isClassDeclaration(statement) ||
      ts.isEnumDeclaration(statement)) &&
    statement.name !== undefined
  ) {
    return statement.name.text;
  }
  return "default export";
}

/**
 * Tell whether a statement is exported.
 *
 * @param statement - Top-level statement.
 * @returns True for exported declarations and default exports.
 */
function isExported(statement: ts.Statement): boolean {
  if (ts.isExportAssignment(statement)) {
    return true;
  }
  if (!ts.canHaveModifiers(statement)) {
    return false;
  }
  return (ts.getModifiers(statement) ?? []).some(
    (modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword,
  );
}

/**
 * Collect every comment in a file, token by token.
 *
 * Walks all child tokens, reading both the leading and the same-line trailing comments at each, so
 * comments after code and inside JSX braces are seen. JSX text is skipped so prose is never misread.
 *
 * @param sourceFile - Parsed file.
 * @returns Comment ranges keyed by start position.
 */
function collectComments(sourceFile: ts.SourceFile): Map<number, ts.CommentRange> {
  const comments = new Map<number, ts.CommentRange>();
  const text = sourceFile.text;
  const visit = (node: ts.Node): void => {
    for (const child of node.getChildren(sourceFile)) {
      if (child.kind !== ts.SyntaxKind.JsxText) {
        const ranges = [
          ...(ts.getLeadingCommentRanges(text, child.pos) ?? []),
          ...(ts.getTrailingCommentRanges(text, child.pos) ?? []),
        ];
        for (const range of ranges) {
          comments.set(range.pos, range);
        }
      }
      visit(child);
    }
  };
  visit(sourceFile);
  for (const range of ts.getTrailingCommentRanges(text, sourceFile.endOfFileToken.pos) ?? []) {
    comments.set(range.pos, range);
  }
  return comments;
}

/**
 * Read the documentation blocks written directly before a node.
 *
 * Filters the leading comments to TSDoc blocks that start with a double asterisk.
 *
 * @param node - Node whose leading comments are read.
 * @param text - Full source text.
 * @returns The block ranges in source order.
 */
function docRanges(node: ts.Node, text: string): ts.CommentRange[] {
  return (ts.getLeadingCommentRanges(text, node.pos) ?? []).filter(
    (range) =>
      range.kind === ts.SyntaxKind.MultiLineCommentTrivia &&
      text.startsWith("/**", range.pos) &&
      text.charAt(range.pos + 3) !== "/",
  );
}

/**
 * Collect the positions of documentation blocks attached to declarations.
 *
 * Asks the compiler for each declaration's own blocks so a block anywhere else can be rejected.
 *
 * @param sourceFile - Parsed file.
 * @returns Start positions of attached blocks.
 */
function attachedBlocks(sourceFile: ts.SourceFile): Set<number> {
  const attached = new Set<number>();
  const visit = (node: ts.Node): void => {
    if (DOCUMENTABLE_KINDS.has(node.kind)) {
      for (const range of docRanges(node, sourceFile.text)) {
        attached.add(range.pos);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return attached;
}

/**
 * Check one source file against the documentation standard.
 *
 * Reports forbidden comments, a missing or malformed file header, unattached blocks, and exported
 * symbols without a well-formed block and the tags their signature requires.
 *
 * @param fileName - File name used in violations and to choose the script kind.
 * @param text - Full source text.
 * @returns The violations found, in source order.
 */
export function checkSource(fileName: string, text: string): Violation[] {
  const scriptKind = fileName.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sourceFile = ts.createSourceFile(fileName, text, ts.ScriptTarget.ESNext, true, scriptKind);
  const violations: Violation[] = [];
  const report = (position: number, message: string): void => {
    const { line } = sourceFile.getLineAndCharacterOfPosition(position);
    violations.push({ file: fileName, line: line + 1, message });
  };

  const comments = collectComments(sourceFile);
  const attached = attachedBlocks(sourceFile);
  const blockText = (range: ts.CommentRange): string => text.slice(range.pos, range.end);
  const isDoc = (range: ts.CommentRange): boolean =>
    range.kind === ts.SyntaxKind.MultiLineCommentTrivia && text.startsWith("/**", range.pos);

  const header = [...comments.values()].find((range) => range.pos === 0 && isDoc(range));
  if (header === undefined || !blockText(header).includes("@packageDocumentation")) {
    report(0, "the file needs a header block tagged @packageDocumentation.");
  } else {
    for (const message of checkBlockShape(
      blockText(header),
      "file header",
      MIN_FUNCTION_DESCRIPTION_LINES,
    )) {
      report(header.pos, message);
    }
  }

  for (const range of [...comments.values()].sort((left, right) => left.pos - right.pos)) {
    if (!isDoc(range)) {
      report(range.pos, "comments are not allowed; use a TSDoc block on a declaration.");
    } else if (range.pos !== header?.pos && !attached.has(range.pos)) {
      report(range.pos, "a documentation block must precede a declaration.");
    }
  }

  for (const statement of sourceFile.statements) {
    if (!isExported(statement)) {
      continue;
    }
    const label = nameOf(statement);
    const ranges = docRanges(statement, text).filter((range) => range.pos !== header?.pos);
    const range = ranges.at(-1);
    if (range === undefined) {
      report(
        statement.getStart(sourceFile),
        `${label}: exported symbols need a documentation block.`,
      );
      continue;
    }
    const block = blockText(range);
    const fn = functionOf(statement);
    const minimum =
      fn !== undefined || ts.isClassDeclaration(statement)
        ? MIN_FUNCTION_DESCRIPTION_LINES
        : MIN_DECLARATION_DESCRIPTION_LINES;
    for (const message of checkBlockShape(block, label, minimum)) {
      report(range.pos, message);
    }
    if (fn !== undefined) {
      for (const message of checkFunctionTags(fn, block, sourceFile, label)) {
        report(range.pos, message);
      }
    }
  }
  return violations.sort((left, right) => left.line - right.line);
}

/**
 * List the source files to check beneath the project root.
 *
 * Reads the scanned directories recursively and adds the root configuration files, skipping
 * dependencies, build output, generated code, and declaration files.
 *
 * @param root - Absolute project root.
 * @returns Absolute file paths in a stable order.
 */
export async function listSources(root: string): Promise<string[]> {
  const found: string[] = [];
  for (const directory of SCANNED_DIRECTORIES) {
    const entries = await readdir(path.join(root, directory), {
      recursive: true,
      withFileTypes: true,
    }).catch(() => []);
    for (const entry of entries) {
      const full = path.join(entry.parentPath, entry.name);
      const segments = path.relative(root, full).split(path.sep);
      const supported = /\.(ts|tsx|js|mjs)$/.test(entry.name) && !entry.name.endsWith(".d.ts");
      if (
        entry.isFile() &&
        supported &&
        !segments.some((segment) => SKIPPED_SEGMENTS.has(segment))
      ) {
        found.push(full);
      }
    }
  }
  for (const name of SCANNED_ROOT_FILES) {
    const full = path.join(root, name);
    if (
      await readFile(full).then(
        () => true,
        () => false,
      )
    ) {
      found.push(full);
    }
  }
  return found.sort();
}

/**
 * Check every project source file.
 *
 * Lists the sources beneath the root and checks each, reporting paths relative to the root
 * with forward slashes so the output is the same on every platform.
 *
 * @param root - Absolute project root.
 * @returns All violations, with paths relative to the root.
 */
export async function checkTree(root: string): Promise<Violation[]> {
  const violations: Violation[] = [];
  for (const file of await listSources(root)) {
    const relative = path.relative(root, file).split(path.sep).join("/");
    violations.push(...checkSource(relative, await readFile(file, "utf8")));
  }
  return violations;
}

if (import.meta.main) {
  const root = path.resolve(import.meta.dirname, "..");
  const violations = await checkTree(root);
  for (const violation of violations) {
    console.error(`${violation.file}:${String(violation.line)}: ${violation.message}`);
  }
  if (violations.length > 0) {
    console.error(`${String(violations.length)} documentation violation(s).`);
    process.exitCode = 1;
  } else {
    console.log("Documentation standard: no violations.");
  }
}
