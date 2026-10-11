/**
 * Static runtime response tests.
 *
 * Pins the security and cache assertions used against the running image.
 * A broken header must fail even when the server answers successfully.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from "vitest";

import { checkHeaders, checkRuntimeLogs } from "./check-image.ts";

const POLICY = "default-src 'self'; script-src 'self'; frame-ancestors 'none'";

describe("checkRuntimeLogs", () => {
  it("uses structured severity, never ordinary words in an informational record", () => {
    expect(
      checkRuntimeLogs('{"level":"NOTICE","logger":"nginx.error","message":"formatter proof"}\n'),
    ).toEqual([]);
  });

  it("rejects structured failures, missing levels, and native errors", () => {
    expect(checkRuntimeLogs('{"level":"error"}\n{}\n2026 [crit] broken')).toHaveLength(3);
  });

  it("accepts an empty window and ordinary startup text", () => {
    expect(checkRuntimeLogs("\nstarting\n")).toEqual([]);
  });
});

describe("checkHeaders", () => {
  it("accepts the served document policy and cache contract", () => {
    expect(
      checkHeaders(
        new Headers({
          "Content-Security-Policy": POLICY,
          "Referrer-Policy": "no-referrer",
          "X-Content-Type-Options": "nosniff",
          "X-Frame-Options": "DENY",
          "Cache-Control": "no-cache",
        }),
        "document",
      ),
    ).toEqual([]);
  });

  it("fails closed on missing security and cache headers", () => {
    expect(checkHeaders(new Headers(), "document").length).toBeGreaterThan(0);
  });

  it("rejects unsafe script policies and non-immutable assets", () => {
    expect(
      checkHeaders(
        new Headers({ "Content-Security-Policy": `${POLICY}; script-src 'unsafe-eval'` }),
        "asset",
      ),
    ).toContain("unsafe CSP");
  });

  it("accepts a one-year immutable asset with inherited security headers", () => {
    expect(
      checkHeaders(
        new Headers({
          "Content-Security-Policy": POLICY,
          "Referrer-Policy": "no-referrer",
          "X-Content-Type-Options": "nosniff",
          "X-Frame-Options": "DENY",
          "Cache-Control": "max-age=31536000, public, immutable",
        }),
        "asset",
      ),
    ).toEqual([]);
  });
});
