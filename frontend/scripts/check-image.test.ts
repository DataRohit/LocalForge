/**
 * Static runtime response tests.
 *
 * Pins the security and cache assertions used against the running image.
 * A broken header must fail even when the server answers successfully.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from "vitest";

import { parse } from "yaml";

import { baselineRule, checkHeaders, checkRuntimeLogs, routingDocument } from "./check-image.ts";

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

describe("routingDocument", () => {
  const frontend = { rule: "Host(`a`)", healthPath: "/healthz" };

  it("gives the Django router priority over the frontend router in the candidate", () => {
    const { http } = parse(routingDocument("Host(`a`) && PathPrefix(`/api/`)", frontend)) as {
      http: { routers: Record<string, { priority: number }>; services: Record<string, unknown> };
    };
    expect(http.routers.backend?.priority).toBe(100);
    expect(http.routers.frontend?.priority).toBe(10);
    expect(Object.keys(http.services)).toEqual(["backend", "frontend"]);
  });

  it("omits every frontend router and service in the rolled-back routing", () => {
    const { http } = parse(routingDocument("Host(`a`)")) as {
      http: { routers: Record<string, unknown>; services: Record<string, unknown> };
    };
    expect(Object.keys(http.routers)).toEqual(["backend"]);
    expect(Object.keys(http.services)).toEqual(["backend"]);
  });
});

describe("baselineRule", () => {
  it("reads the whole-host rule from the development Django labels", () => {
    expect(
      baselineRule({
        services: {
          "django-uv5n2": {
            labels: ["traefik.enable=true", "traefik.http.routers.localforge-local.rule=Host(`a`)"],
          },
        },
      }),
    ).toBe("Host(`a`)");
  });

  it("fails closed when the label is absent", () => {
    expect(() => baselineRule({ services: { "django-uv5n2": { labels: [] } } })).toThrow(
      "routing label is missing",
    );
    expect(() => baselineRule({ services: {} })).toThrow("routing label is missing");
  });
});
