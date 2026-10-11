/**
 * Tests for the API client.
 *
 * Drives the client against the mock backend: success and error parsing, CSRF seeding, timeouts,
 * cancellation, session refresh, and an exhaustive pass over every documented status of every
 * operation the interface calls.
 *
 * @packageDocumentation
 */

import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { envelope, fail, handler, profile, sequence, server, succeed } from "../../test/backend";
import { setCookies } from "../../test/cookies";
import { call, renewSession } from "./client";
import { exposedOperations } from "./endpoints";
import { ApiError, NetworkError, UnexpectedResponseError } from "./errors";
import { operations } from "./generated/operations";
import { notifyUnauthorized, subscribeUnauthorized } from "./unauthorized";

const TOKEN = "token-123";

function watchSessionLoss(): { count: () => number; stop: () => void } {
  let calls = 0;
  const stop = subscribeUnauthorized(() => {
    calls += 1;
  });
  return { count: () => calls, stop };
}

beforeEach(() => {
  setCookies(`__Host-lf-csrf=${TOKEN}`);
});

describe("successful calls", () => {
  it("returns the decoded body of a safe request without a CSRF header", async () => {
    let seen: Request | undefined;
    server.use(
      handler("user_profile_retrieve", 200, profile(), {}, (request) => {
        seen = request;
      }),
    );

    const body = await call("user_profile_retrieve");

    expect(body).toEqual(profile());
    expect(seen?.method).toBe("GET");
    expect(seen?.headers.get("Accept")).toBe("application/json");
    expect(seen?.headers.get("X-CSRFToken")).toBeNull();
    expect(seen?.headers.get("Content-Type")).toBeNull();
  });

  it("sends a JSON body with the CSRF header on an unsafe request", async () => {
    let seen: Request | undefined;
    let sent: unknown;
    server.use(
      http.post(operations.browser_login.path, async ({ request }) => {
        seen = request.clone();
        sent = await request.json();
        return HttpResponse.json(profile());
      }),
    );

    const body = await call("browser_login", { body: { username: "ada", password: "pw" } });

    expect(body).toEqual(profile());
    expect(seen?.headers.get("X-CSRFToken")).toBe(TOKEN);
    expect(seen?.headers.get("Content-Type")).toBe("application/json");
    expect(sent).toEqual({ username: "ada", password: "pw" });
  });

  it("returns undefined for a bodiless success", async () => {
    succeed("browser_logout", 204);

    await expect(call("browser_logout")).resolves.toBeUndefined();
  });
});

describe("CSRF seeding", () => {
  it("seeds the cookie once for concurrent unsafe requests", async () => {
    setCookies("");
    let seeds = 0;
    server.use(
      handler("browser_csrf", 204, undefined, {}, () => {
        seeds += 1;
        setCookies(`__Host-lf-csrf=${TOKEN}`);
      }),
    );
    succeed("browser_logout", 204);
    succeed("user_activation_resend", 202, { detail: "ok" });

    await Promise.all([
      call("browser_logout"),
      call("user_activation_resend", { body: { email: "a@example.test" } }),
    ]);

    expect(seeds).toBe(1);
  });

  it("fails when the browser does not keep the seeded cookie", async () => {
    setCookies("");
    succeed("browser_csrf", 204);

    await expect(call("browser_logout")).rejects.toThrow(UnexpectedResponseError);
  });

  it("surfaces a seeding failure", async () => {
    setCookies("");
    fail("browser_csrf", 429, envelope("throttled", "Slow down."), { "Retry-After": "9" });

    await expect(call("browser_logout")).rejects.toMatchObject({ status: 429 });
  });

  it("seeds again after a previous seeding finished", async () => {
    setCookies("");
    let seeds = 0;
    server.use(
      handler("browser_csrf", 204, undefined, {}, () => {
        seeds += 1;
        setCookies(`__Host-lf-csrf=${TOKEN}`);
      }),
    );
    succeed("browser_logout", 204);
    await call("browser_logout");
    setCookies("");

    await call("browser_logout");

    expect(seeds).toBe(2);
  });
});

describe("error responses", () => {
  it("parses a documented error into an ApiError", async () => {
    fail(
      "user_activation_resend",
      400,
      envelope("validation_error", "Bad.", { email: ["Invalid."] }),
    );

    const error = await call("user_activation_resend", { body: { email: "x" } }).catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 400,
      code: "validation_error",
      details: { email: ["Invalid."] },
      retryAfterSeconds: null,
    });
  });

  it("parses Retry-After seconds and ignores any other form", async () => {
    fail("user_activation_resend", 429, envelope("throttled", "Slow."), { "Retry-After": "42" });
    await expect(call("user_activation_resend", { body: { email: "x" } })).rejects.toMatchObject({
      retryAfterSeconds: 42,
    });

    server.resetHandlers();
    fail("user_activation_resend", 429, envelope("throttled", "Slow."), {
      "Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT",
    });
    await expect(call("user_activation_resend", { body: { email: "x" } })).rejects.toMatchObject({
      retryAfterSeconds: null,
    });
  });

  it("treats an undocumented status as unexpected", async () => {
    server.use(
      http.get(
        operations.user_profile_retrieve.path,
        () => new HttpResponse(null, { status: 418 }),
      ),
    );

    await expect(call("user_profile_retrieve")).rejects.toMatchObject({
      name: "UnexpectedResponseError",
      status: 418,
    });
  });

  it("treats a documented error status without an envelope as unexpected", async () => {
    server.use(
      http.get(
        operations.user_profile_retrieve.path,
        () => new HttpResponse("<html>Bad gateway</html>", { status: 500 }),
      ),
    );

    await expect(call("user_profile_retrieve")).rejects.toBeInstanceOf(UnexpectedResponseError);
  });

  it("treats an envelope-shaped error with the wrong fields as unexpected", async () => {
    server.use(
      http.get(operations.user_profile_retrieve.path, () =>
        HttpResponse.json({ detail: "Not found." }, { status: 500 }),
      ),
    );

    await expect(call("user_profile_retrieve")).rejects.toBeInstanceOf(UnexpectedResponseError);
  });

  it("treats an invalid JSON success body as unexpected", async () => {
    server.use(
      http.get(
        operations.user_profile_retrieve.path,
        () => new HttpResponse("{not json", { status: 200 }),
      ),
    );

    await expect(call("user_profile_retrieve")).rejects.toBeInstanceOf(UnexpectedResponseError);
  });
});

describe("transport failures", () => {
  it("maps a network failure to NetworkError", async () => {
    server.use(http.get(operations.user_profile_retrieve.path, () => HttpResponse.error()));

    const error = await call("user_profile_retrieve").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(NetworkError);
  });

  it("maps a timeout to NetworkError", async () => {
    vi.spyOn(AbortSignal, "timeout").mockReturnValue(AbortSignal.abort());
    server.use(handler("user_profile_retrieve", 200, profile()));

    await expect(call("user_profile_retrieve")).rejects.toBeInstanceOf(NetworkError);
  });

  it("rethrows a caller abort instead of reporting offline", async () => {
    const controller = new AbortController();
    controller.abort();
    server.use(handler("user_profile_retrieve", 200, profile()));

    const error = await call("user_profile_retrieve", { signal: controller.signal }).catch(
      (caught: unknown) => caught,
    );

    expect(error).not.toBeInstanceOf(NetworkError);
    expect(error).toBeInstanceOf(Error);
  });
});

describe("session recovery", () => {
  it("refreshes once and retries once after a 401", async () => {
    const lost = watchSessionLoss();
    const profileCalls = sequence("user_profile_retrieve", [
      { status: 401, body: envelope("not_authenticated", "Expired.") },
      { status: 200, body: profile() },
    ]);
    const refreshCalls = sequence("browser_refresh", [{ status: 204 }]);

    await expect(call("user_profile_retrieve")).resolves.toEqual(profile());

    expect(profileCalls()).toBe(2);
    expect(refreshCalls()).toBe(1);
    expect(lost.count()).toBe(0);
    lost.stop();
  });

  it("does not try to refresh when no CSRF cookie exists, because no session was started", async () => {
    setCookies("");
    const lost = watchSessionLoss();
    const refreshCalls = sequence("browser_refresh", [{ status: 204 }]);
    fail("user_profile_retrieve", 401, envelope("not_authenticated", "No session."));

    await expect(call("user_profile_retrieve")).rejects.toMatchObject({ status: 401 });

    expect(refreshCalls()).toBe(0);
    expect(lost.count()).toBe(1);
    lost.stop();
  });

  it("announces session loss when the refresh is rejected", async () => {
    const lost = watchSessionLoss();
    const profileCalls = sequence("user_profile_retrieve", [
      { status: 401, body: envelope("not_authenticated", "Expired.") },
    ]);
    fail("browser_refresh", 401, envelope("not_authenticated", "No session."));

    await expect(call("user_profile_retrieve")).rejects.toMatchObject({ status: 401 });

    expect(profileCalls()).toBe(1);
    expect(lost.count()).toBe(1);
    lost.stop();
  });

  it("announces session loss when the retry is rejected too, without looping", async () => {
    const lost = watchSessionLoss();
    const profileCalls = sequence("user_profile_retrieve", [
      { status: 401, body: envelope("not_authenticated", "Expired.") },
    ]);
    const refreshCalls = sequence("browser_refresh", [{ status: 204 }]);

    await expect(call("user_profile_retrieve")).rejects.toMatchObject({ status: 401 });

    expect(profileCalls()).toBe(2);
    expect(refreshCalls()).toBe(1);
    expect(lost.count()).toBe(1);
    lost.stop();
  });

  it("does not announce loss when the retry fails for another reason", async () => {
    const lost = watchSessionLoss();
    sequence("user_profile_retrieve", [
      { status: 401, body: envelope("not_authenticated", "Expired.") },
      { status: 429, body: envelope("throttled", "Slow.") },
    ]);
    succeed("browser_refresh", 204);

    await expect(call("user_profile_retrieve")).rejects.toMatchObject({ status: 429 });

    expect(lost.count()).toBe(0);
    lost.stop();
  });

  it("shares one refresh among concurrent authenticated calls", async () => {
    const profileCalls = sequence("user_profile_retrieve", [
      { status: 401, body: envelope("not_authenticated", "Expired.") },
      { status: 401, body: envelope("not_authenticated", "Expired.") },
      { status: 200, body: profile() },
    ]);
    const refreshCalls = sequence("browser_refresh", [{ status: 204 }]);

    const results = await Promise.all([
      call("user_profile_retrieve"),
      call("user_profile_retrieve"),
    ]);

    expect(results).toEqual([profile(), profile()]);
    expect(refreshCalls()).toBe(1);
    expect(profileCalls()).toBe(4);
  });

  it("propagates a refresh failure that is not a 401", async () => {
    const lost = watchSessionLoss();
    sequence("user_profile_retrieve", [{ status: 401, body: envelope("not_authenticated", "x") }]);
    fail("browser_refresh", 403, envelope("permission_denied", "CSRF failed."));

    await expect(call("user_profile_retrieve")).rejects.toMatchObject({ status: 403 });

    expect(lost.count()).toBe(0);
    lost.stop();
  });

  it("does not refresh after a 401 from an unauthenticated operation", async () => {
    const lost = watchSessionLoss();
    const refreshCalls = sequence("browser_refresh", [{ status: 204 }]);
    fail("browser_login", 401, envelope("invalid_credentials", "Wrong."));

    await expect(
      call("browser_login", { body: { username: "a", password: "b" } }),
    ).rejects.toMatchObject({ status: 401 });

    expect(refreshCalls()).toBe(0);
    expect(lost.count()).toBe(0);
    lost.stop();
  });

  it("does not announce loss on success", async () => {
    const lost = watchSessionLoss();
    succeed("user_profile_retrieve", 200, profile());

    await call("user_profile_retrieve");

    expect(lost.count()).toBe(0);
    notifyUnauthorized();
    expect(lost.count()).toBe(1);
    lost.stop();
  });
});

describe("renewing the session on demand", () => {
  it("returns true when the cookies were renewed and does not announce loss", async () => {
    const lost = watchSessionLoss();
    const refreshCalls = sequence("browser_refresh", [{ status: 204 }]);

    await expect(renewSession()).resolves.toBe(true);

    expect(refreshCalls()).toBe(1);
    expect(lost.count()).toBe(0);
    lost.stop();
  });

  it("returns false and announces session loss when the refresh is refused", async () => {
    const lost = watchSessionLoss();
    fail("browser_refresh", 401, envelope("not_authenticated", "No session."));

    await expect(renewSession()).resolves.toBe(false);

    expect(lost.count()).toBe(1);
    lost.stop();
  });

  it("propagates a failure that is not a 401 without announcing loss", async () => {
    const lost = watchSessionLoss();
    fail("browser_refresh", 503, envelope("service_unavailable", "Down."));

    await expect(renewSession()).rejects.toMatchObject({ status: 503 });

    expect(lost.count()).toBe(0);
    lost.stop();
  });

  it("shares the refresh with a concurrent authenticated call", async () => {
    const refreshCalls = sequence("browser_refresh", [{ status: 204 }]);
    sequence("user_profile_retrieve", [
      { status: 401, body: envelope("not_authenticated", "x") },
      { status: 200, body: profile() },
    ]);

    await Promise.all([renewSession(), call("user_profile_retrieve")]);

    expect(refreshCalls()).toBe(1);
  });
});

describe("every documented status of every exposed operation", () => {
  const cases = exposedOperations.flatMap((id) =>
    operations[id].statuses.map((status) => [id, status] as const),
  );

  it("covers every exposed operation and at least one error and one success each", () => {
    expect(new Set(cases.map(([id]) => id)).size).toBe(exposedOperations.length);
    for (const id of exposedOperations) {
      const statuses = operations[id].statuses as readonly number[];
      expect(statuses.some((status) => status < 300)).toBe(true);
      expect(statuses.some((status) => status >= 400)).toBe(true);
    }
  });

  it.each(cases)("%s answers %i with a handled outcome", async (id, status) => {
    const options = (operations[id].method === "get" ? {} : { body: {} }) as never;
    if (status < 300) {
      server.use(handler(id, status, status === 204 ? undefined : { ok: true }));

      await expect(call(id, options)).resolves.toEqual(status === 204 ? undefined : { ok: true });
    } else {
      const sent = envelope(`code_${String(status)}`, `Message ${String(status)}.`);
      server.use(handler(id, status, sent, status === 429 ? { "Retry-After": "5" } : {}));
      if (status === 401 && operations[id].authenticated) {
        server.use(handler("browser_refresh", 401, envelope("not_authenticated", "No session.")));
      }

      const error = await call(id, options).catch((caught: unknown) => caught);

      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ status, code: sent.code, message: sent.message });
    }
  });
});
