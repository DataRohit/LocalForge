/**
 * Tests for the testing isolation check.
 *
 * Starts from a passing input and breaks one property at a time, so each guard is proven to fire.
 * No message may carry a value.
 *
 * @packageDocumentation
 */

import { describe, expect, it } from "vitest";

import {
  checkIsolation,
  OPTIONAL_CONTAINERS,
  REQUIRED_CONTAINERS,
  type IsolationInput,
} from "./isolation.ts";

const SECRETS = {
  POSTGRES_USER: "pg-user",
  POSTGRES_PASSWORD: "pg-secret-value",
  VALKEY_CACHE_PASSWORD: "cache-secret-value",
  VALKEY_CHANNELS_PASSWORD: "channels-secret-value",
  RABBITMQ_DEFAULT_USER: "rabbit-user",
  RABBITMQ_DEFAULT_PASS: "rabbit-secret-value",
  MP_UI_AUTH: "mail:mail-secret-value",
};

const HOST = {
  ...SECRETS,
  COMPOSE_PROJECT_NAME: "localforge-test",
  DJANGO_SETTINGS_MODULE: "config.settings.testing",
  POSTGRES_HOST: "127.0.0.1",
  VALKEY_CACHE_HOST: "127.0.0.1",
  VALKEY_CHANNELS_HOST: "127.0.0.1",
  RABBITMQ_HOST: "127.0.0.1",
  EMAIL_HOST: "127.0.0.1",
  POSTGRES_PORT: "25432",
  VALKEY_CACHE_PORT: "26379",
  VALKEY_CHANNELS_PORT: "26380",
  RABBITMQ_PORT: "25672",
  EMAIL_PORT: "21025",
  MAILPIT_WEB_PORT: "28025",
};

const GOOD: IsolationInput = {
  hostEnv: HOST,
  containerEnv: { ...SECRETS, COMPOSE_PROJECT_NAME: "localforge-test" },
  containers: [...REQUIRED_CONTAINERS, ...OPTIONAL_CONTAINERS],
};

function check(change: Partial<IsolationInput>): string[] {
  return checkIsolation({ ...GOOD, ...change });
}

describe("checkIsolation", () => {
  it("passes the testing stack", () => {
    expect(check({})).toEqual([]);
    expect(check({ containers: [...REQUIRED_CONTAINERS] })).toEqual([]);
  });

  it("rejects the wrong project name in either file", () => {
    expect(check({ hostEnv: { ...HOST, COMPOSE_PROJECT_NAME: "localforge-dev" } })).toEqual([
      "host env file: COMPOSE_PROJECT_NAME is not localforge-test.",
    ]);
    expect(check({ containerEnv: { ...SECRETS } })).toEqual([
      "container env file: COMPOSE_PROJECT_NAME is not localforge-test.",
    ]);
  });

  it("rejects a settings module other than testing", () => {
    expect(
      check({ hostEnv: { ...HOST, DJANGO_SETTINGS_MODULE: "config.settings.development" } }),
    ).toEqual(["host env file: DJANGO_SETTINGS_MODULE is not config.settings.testing."]);
  });

  it("rejects a host that is not loopback", () => {
    expect(check({ hostEnv: { ...HOST, POSTGRES_HOST: "postgres-pg3ka" } })).toEqual([
      "host env file: POSTGRES_HOST is not 127.0.0.1.",
    ]);
  });

  it.each([
    ["POSTGRES_PORT", "5432"],
    ["VALKEY_CACHE_PORT", "6379"],
    ["VALKEY_CHANNELS_PORT", "6380"],
    ["RABBITMQ_PORT", "5672"],
    ["EMAIL_PORT", "1025"],
    ["MAILPIT_WEB_PORT", "8025"],
  ])("rejects a development port for %s", (name, port) => {
    expect(check({ hostEnv: { ...HOST, [name]: port } })).toEqual([
      `host env file: ${name} is not the testing port ${HOST[name as keyof typeof HOST]}.`,
    ]);
  });

  it("rejects credentials that differ, are empty, or are missing", () => {
    expect(check({ containerEnv: { ...GOOD.containerEnv, POSTGRES_PASSWORD: "other" } })).toEqual([
      "POSTGRES_PASSWORD differs between the host and container env files.",
    ]);
    expect(check({ hostEnv: { ...HOST, MP_UI_AUTH: "" } })).toEqual([
      "MP_UI_AUTH differs between the host and container env files.",
    ]);
    expect(check({ hostEnv: { ...HOST, POSTGRES_USER: undefined as unknown as string } })).toEqual([
      "POSTGRES_USER differs between the host and container env files.",
    ]);
  });

  it("reports a missing required container and an unregistered one", () => {
    expect(check({ containers: REQUIRED_CONTAINERS.slice(1) })).toEqual([
      "testing container postgres-tp8vn is not running.",
    ]);
    expect(check({ containers: [...REQUIRED_CONTAINERS, "postgres-pg3ka"] })).toEqual([
      "container postgres-pg3ka is not a registered testing container.",
    ]);
  });

  it("never puts a credential value in a message", () => {
    const messages = check({
      containerEnv: { ...GOOD.containerEnv, POSTGRES_PASSWORD: "other-secret-value" },
    }).join(" ");

    expect(messages).not.toContain("secret-value");
  });
});
