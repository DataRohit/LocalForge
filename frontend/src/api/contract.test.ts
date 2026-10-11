/**
 * Contract replay against the committed OpenAPI examples.
 *
 * Serves every response example the document gives, for every operation the interface calls,
 * through the real client, and requires the client to parse each one. The mock backend therefore
 * cannot drift from the schema: the same examples are what the backend's own tests observe.
 *
 * @packageDocumentation
 */

import { beforeEach, describe, expect, it } from "vitest";

import { envelope, fail, handler, server } from "../../test/backend";
import { setCookies } from "../../test/cookies";
import { call } from "./client";
import { exposedOperations, fetchProfile, login, updateProfile } from "./endpoints";
import { ApiError } from "./errors";
import { examples } from "./generated/examples";
import { operations } from "./generated/operations";
import type { OperationId } from "./types";

interface Case {
  readonly id: OperationId;
  readonly status: number;
  readonly index: number;
  readonly example: unknown;
}

const CASES: Case[] = exposedOperations.flatMap((id) =>
  Object.entries(examples[id] ?? {}).flatMap(([status, values]) =>
    values.map((example, index) => ({ id, status: Number(status), index, example })),
  ),
);

beforeEach(() => {
  setCookies("__Host-lf-csrf=token");
});

describe("the committed examples", () => {
  it("cover every documented status that carries a body, for every operation the interface calls", () => {
    const missing = exposedOperations.flatMap((id) =>
      operations[id].statuses
        .filter((status) => status !== 204)
        .filter((status) => (examples[id]?.[String(status)] ?? []).length === 0)
        .map((status) => `${id} ${String(status)}`),
    );

    expect(missing).toEqual([]);
  });

  it("include a large enough set to be a real replay", () => {
    expect(CASES.length).toBeGreaterThan(100);
  });
});

describe("replaying every documented example through the client", () => {
  it.each(CASES)("$id $status example $index is understood", async ({ id, status, example }) => {
    fail("browser_refresh", 401, envelope("not_authenticated", "No session."));
    server.use(handler(id, status, example));

    const options = operations[id].hasBody ? { body: {} } : {};
    const outcome = await call(id, options as never).then(
      (value) => ({ ok: true as const, value }),
      (error: unknown) => ({ ok: false as const, error }),
    );

    if (status >= 400) {
      expect(outcome.ok).toBe(false);
      const error = outcome.ok ? null : outcome.error;
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({
        status,
        code: (example as { code: string }).code,
        details: (example as { details: object }).details,
        requestId: (example as { request_id: string }).request_id,
      });
    } else {
      expect(outcome).toEqual({ ok: true, value: example });
    }
  });
});

describe("the profile examples", () => {
  it("are accepted by the profile parser on every route that returns one", async () => {
    const profileExample = (examples.user_profile_retrieve?.["200"] ?? [])[0];
    server.use(
      handler("user_profile_retrieve", 200, profileExample),
      handler("browser_login", 200, profileExample),
      handler("user_profile_update", 200, profileExample),
    );

    await expect(fetchProfile()).resolves.toEqual(profileExample);
    await expect(login({ username: "a", password: "b" })).resolves.toEqual(profileExample);
    await expect(updateProfile({ email: "a@example.test" })).resolves.toEqual(profileExample);
  });
});
