/**
 * Tests for the Mailpit client.
 *
 * Covers link extraction, settings, and polling against a stubbed fetch.
 * No real Mailpit is contacted, so every branch of the client is reachable.
 *
 * @packageDocumentation
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { clearMailbox, extractLink, mailSettings, waitForLink } from "./mail.ts";

const SETTINGS = { baseUrl: "http://127.0.0.1:28025", auth: "user:pass" };
const LINK =
  "https://127.0.0.1:4443/activate#account=11111111-1111-4111-8111-111111111111&token=abc.def";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("extractLink", () => {
  it("finds the link for the requested path", () => {
    expect(extractLink(`Open ${LINK} now.`, "/activate")).toBe(LINK);
  });

  it("ignores links to other paths and text without a link", () => {
    expect(extractLink(LINK, "/reset-password")).toBeNull();
    expect(extractLink("nothing here", "/activate")).toBeNull();
  });

  it("escapes characters in the path", () => {
    expect(extractLink("https://x.test/a.b#account=1&token=2", "/a.b")).toBe(
      "https://x.test/a.b#account=1&token=2",
    );
    expect(extractLink("https://x.test/aXb#account=1&token=2", "/a.b")).toBeNull();
  });
});

describe("mailSettings", () => {
  it("reads both values", () => {
    expect(mailSettings({ E2E_MAILPIT_URL: "u", E2E_MAILPIT_AUTH: "a" })).toEqual({
      baseUrl: "u",
      auth: "a",
    });
  });

  it("fails when the runner provided nothing", () => {
    expect(() => mailSettings({})).toThrow(/did not provide/);
  });
});

describe("waitForLink", () => {
  it("returns the link from the newest matching message and sends basic auth", async () => {
    const fetchMock = vi.fn((url: string) =>
      Promise.resolve(
        url.includes("/search")
          ? json({ messages: [{ ID: "1" }, { ID: "2" }] })
          : json({ Text: url.endsWith("/1") ? "no link" : LINK }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    expect(await waitForLink(SETTINGS, "a@example.test", "/activate", 2)).toBe(LINK);

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBe(
      `Basic ${Buffer.from("user:pass").toString("base64")}`,
    );
    expect(fetchMock.mock.calls[0]?.[0]).toContain("query=to%3Aa%40example.test");
  });

  it("gives up when no message carries the link", async () => {
    vi.stubGlobal("fetch", () => Promise.resolve(json({})));

    await expect(waitForLink(SETTINGS, "a@example.test", "/activate", 1)).rejects.toThrow(
      /No \/activate email/,
    );
  });

  it("tolerates a message without text", async () => {
    vi.stubGlobal("fetch", (url: string) =>
      Promise.resolve(url.includes("/search") ? json({ messages: [{ ID: "1" }] }) : json({})),
    );

    await expect(waitForLink(SETTINGS, "a@example.test", "/activate", 1)).rejects.toThrow();
  });

  it("fails loudly when Mailpit answers with an error", async () => {
    vi.stubGlobal("fetch", () => Promise.resolve(json({}, 401)));

    await expect(waitForLink(SETTINGS, "a@example.test", "/activate", 1)).rejects.toThrow(
      /answered 401/,
    );
  });
});

describe("clearMailbox", () => {
  it("accepts the plain-text reply Mailpit sends to a delete", async () => {
    vi.stubGlobal("fetch", () => Promise.resolve(new Response("ok", { status: 200 })));

    await expect(clearMailbox(SETTINGS)).resolves.toBeUndefined();
  });

  it("deletes every message", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(json({})));
    vi.stubGlobal("fetch", fetchMock);

    await clearMailbox(SETTINGS);

    expect(fetchMock.mock.calls[0]).toEqual([
      "http://127.0.0.1:28025/api/v1/messages",
      expect.objectContaining({ method: "DELETE" }),
    ]);
  });
});
