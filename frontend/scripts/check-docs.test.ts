/**
 * Tests for the documentation checker.
 *
 * Gives every rule a failing and a passing fixture so a rule that silently stops firing is caught.
 * A final test runs the real file listing against a temporary tree.
 *
 * @packageDocumentation
 */

import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { checkBlockShape, checkSource, checkTree, listSources, parseDoc } from "./check-docs.ts";

const HEADER = [
  "/**",
  " * Title line.",
  " *",
  " * First description line.",
  " * Second description line.",
  " *",
  " * @packageDocumentation",
  " */",
  "",
  "",
].join("\n");

const DOC = (lines: string[]): string =>
  ["/**", ...lines.map((line) => ` * ${line}`), " */"].join("\n");

const FULL_DOC = DOC([
  "Add numbers.",
  "",
  "Adds two numbers together.",
  "Never overflows in practice.",
  "",
  "@param left - First number.",
  "@param right - Second number.",
  "@returns The sum.",
]);

function messages(text: string, name = "sample.ts"): string[] {
  return checkSource(name, text).map((violation) => violation.message);
}

describe("parseDoc", () => {
  it("splits title, description, and tags", () => {
    const doc = parseDoc(FULL_DOC);

    expect(doc.title).toBe("Add numbers.");
    expect(doc.titleIsSingleLine).toBe(true);
    expect(doc.description).toEqual(["Adds two numbers together.", "Never overflows in practice."]);
    expect(doc.tags).toEqual([
      "@param left - First number.",
      "@param right - Second number.",
      "@returns The sum.",
    ]);
  });

  it("detects a title that runs into the description", () => {
    expect(parseDoc(DOC(["Title", "Directly following line."])).titleIsSingleLine).toBe(false);
  });

  it("handles an empty block", () => {
    expect(parseDoc("/** */")).toMatchObject({ title: "", description: [], tags: [] });
  });
});

describe("checkBlockShape", () => {
  it("accepts a well-formed block", () => {
    expect(checkBlockShape(FULL_DOC, "add", 2)).toEqual([]);
  });

  it("rejects a missing title", () => {
    expect(checkBlockShape(DOC(["@returns Nothing."]), "x", 1)).toContain(
      "x: the block needs a one-line title.",
    );
  });

  it("rejects a title without a separating blank line", () => {
    expect(checkBlockShape(DOC(["Title", "Body."]), "x", 1).join(" ")).toContain("blank line");
  });

  it("rejects too few and too many description lines", () => {
    expect(checkBlockShape(DOC(["Title", "", "One."]), "x", 2).join(" ")).toContain("found 1");
    expect(checkBlockShape(DOC(["Title", "", "a", "b", "c", "d"]), "x", 1).join(" ")).toContain(
      "found 4",
    );
  });

  it("rejects a line over the limit", () => {
    const long = "x".repeat(120);

    expect(checkBlockShape(DOC(["Title", "", long, "Second."]), "x", 2).join(" ")).toContain(
      "exceeds",
    );
  });
});

describe("comments", () => {
  it("accepts a clean file", () => {
    expect(messages(`${HEADER}const value = 1;\nconsole.log(value);\n`)).toEqual([]);
  });

  it.each([
    ["a line comment", `${HEADER}// note\nconst a = 1;\nconsole.log(a);\n`],
    ["a trailing line comment", `${HEADER}const a = 1; // note\nconsole.log(a);\n`],
    ["a block comment", `${HEADER}/* note */\nconst a = 1;\nconsole.log(a);\n`],
    ["a lint directive", `${HEADER}// eslint-disable-next-line\nconst a = 1;\nconsole.log(a);\n`],
  ])("rejects %s", (_name, text) => {
    expect(messages(text)).toContain(
      "comments are not allowed; use a TSDoc block on a declaration.",
    );
  });

  it("rejects a comment inside JSX braces", () => {
    const text = `${HEADER}const view = <div>{/* note */}</div>;\nconsole.log(view);\n`;

    expect(messages(text, "view.tsx")).toContain(
      "comments are not allowed; use a TSDoc block on a declaration.",
    );
  });

  it("ignores comment-like text in strings, templates, regular expressions, and JSX text", () => {
    const text = [
      HEADER,
      'const url = "http://example.test/a";',
      "const template = `// not a comment ${url}`;",
      "const pattern = /\\/\\/ still not/;",
      "const view = <a href={url}>see http://example.test // or /* this */</a>;",
      "console.log(template, pattern, view);",
      "",
    ].join("\n");

    expect(messages(text, "view.tsx")).toEqual([]);
  });

  it("rejects a documentation block that does not precede a declaration", () => {
    const text = `${HEADER}const a = 1;\n${DOC(["Stray.", "", "Body."])}\nconsole.log(a);\n`;

    expect(messages(text)).toContain("a documentation block must precede a declaration.");
  });
});

describe("file header", () => {
  it("requires a header block", () => {
    expect(messages("const a = 1;\nconsole.log(a);\n")).toContain(
      "the file needs a header block tagged @packageDocumentation.",
    );
  });

  it("requires the packageDocumentation tag", () => {
    const text = `${DOC(["Title.", "", "One.", "Two."])}\n\nconst a = 1;\nconsole.log(a);\n`;

    expect(messages(text)).toContain("the file needs a header block tagged @packageDocumentation.");
  });

  it("checks the header description length", () => {
    const text =
      "/**\n * Title.\n *\n * Only one line.\n *\n * @packageDocumentation\n */\n\nconsole.log(1);\n";

    expect(messages(text).join(" ")).toContain("file header: the description needs 2 to 3 lines");
  });
});

describe("exported symbols", () => {
  it("accepts a fully documented function", () => {
    const text = `${HEADER}${FULL_DOC}\nexport function add(left: number, right: number): number {\n  return left + right;\n}\n`;

    expect(messages(text)).toEqual([]);
  });

  it("requires a block on an exported function", () => {
    expect(messages(`${HEADER}export function add(): number {\n  return 1;\n}\n`)).toContain(
      "add: exported symbols need a documentation block.",
    );
  });

  it("does not treat the file header as the symbol block", () => {
    expect(messages(`${HEADER}export const value = 1;\n`)).toContain(
      "value: exported symbols need a documentation block.",
    );
  });

  it("requires every identifier parameter, the result, and thrown errors", () => {
    const doc = DOC([
      "Do work.",
      "",
      "Does the work once.",
      "Fails loudly.",
      "",
      "@param left - First.",
    ]);
    const text = `${HEADER}${doc}\nexport function work(left: number, right: number): number {\n  if (left > right) {\n    throw new Error("no");\n  }\n  return left;\n}\n`;

    const found = messages(text);

    expect(found).toContain("work: missing @param right.");
    expect(found).toContain("work: missing @returns.");
    expect(found).toContain("work: missing @throws.");
  });

  it("does not require a result tag for void or Promise<void>", () => {
    const doc = DOC(["Do work.", "", "Does the work once.", "Returns nothing."]);
    const text = `${HEADER}${doc}\nexport function a(): void {\n  return;\n}\n\n${doc}\nexport async function b(): Promise<void> {\n  await Promise.resolve();\n}\n`;

    expect(messages(text)).toEqual([]);
  });

  it("requires a result tag for an expression-bodied arrow function without a type", () => {
    const doc = DOC(["Make one.", "", "Builds a number.", "Pure.", "", "@param seed - Seed."]);
    const text = `${HEADER}${doc}\nexport const make = (seed: number) => seed + 1;\n`;

    expect(messages(text)).toContain("make: missing @returns.");
  });

  it("looks for returned values only in the function's own body", () => {
    const doc = DOC(["Run.", "", "Runs a callback.", "Returns nothing itself."]);
    const text = `${HEADER}${doc}\nexport function run() {\n  const inner = () => {\n    return 1;\n  };\n  inner();\n}\n`;

    expect(messages(text)).toEqual([]);
  });

  it("accepts a destructured props parameter with one tag and rejects none", () => {
    const base = ["Show.", "", "Shows a thing.", "Pure view.", ""];
    const good = `${HEADER}${DOC([...base, "@param props - Component props.", "@returns The element."])}\nexport const Show = ({ a }: { a: string }): string => a;\n`;
    const bad = `${HEADER}${DOC([...base, "@returns The element."])}\nexport const Show = ({ a }: { a: string }): string => a;\n`;

    expect(messages(good)).toEqual([]);
    expect(messages(bad)).toContain("Show: document the destructured parameter with @param.");
  });

  it("applies the lighter minimum to types, interfaces, and constants", () => {
    const text = `${HEADER}${DOC(["A shape.", "", "Describes one thing."])}\nexport interface Shape {\n  readonly a: number;\n}\n\n${DOC(["A name.", "", "The product name."])}\nexport type Name = string;\n\n${DOC(["Limit.", "", "The largest size."])}\nexport const LIMIT = 3;\n`;

    expect(messages(text)).toEqual([]);
  });

  it("rejects a type with no description at all", () => {
    const text = `${HEADER}${DOC(["A shape."])}\nexport interface Shape {\n  readonly a: number;\n}\n`;

    expect(messages(text).join(" ")).toContain("Shape: the description needs 1 to 3 lines");
  });

  it("requires a block on a default export", () => {
    expect(messages(`${HEADER}export default { a: 1 };\n`)).toContain(
      "default export: exported symbols need a documentation block.",
    );
  });

  it("names classes and enums", () => {
    const text = `${HEADER}export class Thing {}\nexport enum Level {\n  Low,\n}\n`;

    const found = messages(text);

    expect(found).toContain("Thing: exported symbols need a documentation block.");
    expect(found).toContain("Level: exported symbols need a documentation block.");
  });

  it("does not require a block on a symbol that is not exported", () => {
    expect(
      messages(`${HEADER}function hidden(): number {\n  return 1;\n}\nconsole.log(hidden());\n`),
    ).toEqual([]);
  });

  it("ignores a re-export", () => {
    expect(messages(`${HEADER}export { a } from "./a";\n`)).toEqual([]);
  });
});

describe("file listing", () => {
  it("lists scanned sources and skips generated, dependency, and declaration files", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "lf-docs-"));
    try {
      for (const directory of ["src/api/generated", "src/ui", "node_modules/x", "test"]) {
        await mkdir(path.join(root, directory), { recursive: true });
      }
      const files = [
        "src/ui/Button.tsx",
        "src/ui/Button.d.ts",
        "src/api/generated/schema.d.ts",
        "src/api/generated/operations.ts",
        "node_modules/x/index.ts",
        "test/setup.ts",
        "test/notes.txt",
        "vite.config.ts",
      ];
      for (const file of files) {
        await writeFile(path.join(root, file), `${HEADER}console.log(1);\n`, "utf8");
      }

      const listed = (await listSources(root)).map((file) =>
        path.relative(root, file).split(path.sep).join("/"),
      );
      const violations = await checkTree(root);

      expect(listed).toEqual(["src/ui/Button.tsx", "test/setup.ts", "vite.config.ts"]);
      expect(violations).toEqual([]);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });

  it("reports violations with relative paths", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "lf-docs-"));
    try {
      await mkdir(path.join(root, "src"));
      await writeFile(path.join(root, "src", "bad.ts"), "// nope\n", "utf8");

      const violations = await checkTree(root);

      expect(violations.map((violation) => violation.file)).toEqual(["src/bad.ts", "src/bad.ts"]);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
});
