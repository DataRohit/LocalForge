/**
 * Tests for the environment file parser.
 *
 * Covers plain pairs, quoting, comments, line endings, and values that contain equals signs.
 * The parser feeds the testing credentials to child processes, so its edge cases matter.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from "vitest";

import { parseDotenv } from "./dotenv.ts";

describe("parseDotenv", () => {
  it("reads key and value pairs in order", () => {
    expect(parseDotenv("A=1\nB=two")).toEqual({ A: "1", B: "two" });
  });

  it("keeps equals signs and spaces inside a value", () => {
    expect(parseDotenv("A=x=y z")).toEqual({ A: "x=y z" });
  });

  it("removes one pair of matching quotes only", () => {
    expect(parseDotenv('A="quoted"\nB=\'single\'\nC="open\nD="mismatched\'')).toEqual({
      A: "quoted",
      B: "single",
      C: '"open',
      D: "\"mismatched'",
    });
  });

  it("skips blank lines, comments, and lines without an equals sign", () => {
    expect(parseDotenv("\n# note\nnovalue\n  # indented\nA=1\r\nB=2\r\n")).toEqual({
      A: "1",
      B: "2",
    });
  });

  it("allows an empty value", () => {
    expect(parseDotenv("A=")).toEqual({ A: "" });
  });
});
