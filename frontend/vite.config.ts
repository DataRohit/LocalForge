/**
 * Vite and Vitest configuration.
 *
 * Builds the static single-page application with Tailwind CSS and defines the application and
 * tooling test projects. The development and preview servers proxy backend routes to a testing
 * backend; preview serves HTTPS only when the end-to-end runner supplies a certificate directory.
 *
 * @packageDocumentation
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

import { resolveBackendOrigin } from "./scripts/backend-origin.ts";

const backend = resolveBackendOrigin(process.env.LOCALFORGE_API_ORIGIN);
const tlsDirectory = process.env.LOCALFORGE_E2E_TLS_DIRECTORY;

const proxy = {
  "/api/": { target: backend, changeOrigin: false },
  "/health/": { target: backend, changeOrigin: false },
  "/ws/": { target: backend, ws: true, changeOrigin: false },
};

/**
 * Vite configuration object.
 *
 * Registers the React and Tailwind plugins, the local-only build settings, the development and
 * preview proxies, and the Vitest projects and 100 percent coverage thresholds.
 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    target: "es2023",
    sourcemap: false,
    assetsInlineLimit: 0,
    cssCodeSplit: false,
  },
  server: { host: "127.0.0.1", proxy },
  preview: {
    host: "127.0.0.1",
    proxy,
    ...(tlsDirectory === undefined
      ? {}
      : {
          https: {
            key: readFileSync(path.join(tlsDirectory, "key.pem")),
            cert: readFileSync(path.join(tlsDirectory, "cert.pem")),
          },
        }),
  },
  test: {
    testTimeout: 10_000,
    hookTimeout: 10_000,
    teardownTimeout: 5_000,
    projects: [
      {
        extends: true,
        test: {
          name: "app",
          environment: "jsdom",
          include: ["src/**/*.test.{ts,tsx}"],
          setupFiles: ["test/setup.ts"],
        },
      },
      {
        extends: true,
        test: {
          name: "tooling",
          environment: "node",
          include: ["scripts/**/*.test.ts", "e2e/support/**/*.test.ts"],
        },
      },
    ],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/main.tsx", "src/api/generated/**", "**/*.test.{ts,tsx}", "**/*.d.ts"],
      reporter: ["text-summary", "json-summary", "lcov"],
      reportsDirectory: "coverage",
      thresholds: { statements: 100, branches: 100, functions: 100, lines: 100 },
    },
  },
});
