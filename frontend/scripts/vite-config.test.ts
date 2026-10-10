/**
 * Tests for the Vite configuration.
 *
 * Confirms the development server proxies every backend prefix to the testing origin, so a
 * mistyped API path in development reaches the backend and never renders the application shell.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from "vitest";

import config from "../vite.config.ts";

describe("vite configuration", () => {
  const proxy = config.server?.proxy ?? {};

  it("proxies the API, health, and socket prefixes to the testing backend", () => {
    expect(Object.keys(proxy).sort()).toEqual(["/api/", "/health/", "/ws/"]);
    for (const entry of Object.values(proxy)) {
      expect(entry).toMatchObject({ target: "http://127.0.0.1:28000" });
    }
  });

  it("proxies WebSocket upgrades only for the socket prefix", () => {
    expect(proxy["/ws/"]).toMatchObject({ ws: true });
    expect(proxy["/api/"]).not.toMatchObject({ ws: true });
  });

  it("binds the development server to loopback and builds with no inlined assets", () => {
    expect(config.server?.host).toBe("127.0.0.1");
    expect(config.build?.assetsInlineLimit).toBe(0);
  });
});
