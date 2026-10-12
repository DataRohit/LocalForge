/**
 * ESLint flat configuration.
 *
 * Applies strict type-aware TypeScript, React hooks, and accessibility rules to the app and
 * tooling. Network, storage, and unsafe-HTML APIs are restricted to the modules that own them.
 *
 * @packageDocumentation
 */

import { defineConfig, globalIgnores } from "eslint/config";
import js from "@eslint/js";
import globals from "globals";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

const restrictedGlobals = [
  "fetch",
  "XMLHttpRequest",
  "WebSocket",
  "EventSource",
  "localStorage",
  "sessionStorage",
  "indexedDB",
].map((name) => ({
  name,
  message: `Use the api/ module instead of the ${name} global.`,
}));

/**
 * ESLint configuration array.
 *
 * Ignores build output and generated code, then layers the recommended and strict rule sets.
 * Restricted globals are lifted only for the api module, the tests, and the browser suites.
 */
export default defineConfig(
  globalIgnores([
    "dist",
    "coverage",
    "playwright-report",
    ".e2e-run",
    "test-results",
    "node_modules",
    "src/api/generated",
  ]),
  js.configs.recommended,
  tseslint.configs.strictTypeChecked,
  tseslint.configs.stylisticTypeChecked,
  reactHooks.configs.flat["recommended-latest"],
  jsxA11y.flatConfigs.strict,
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      eqeqeq: ["error", "always"],
      curly: ["error", "all"],
      "no-console": "error",
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-new-func": "error",
      "no-restricted-globals": ["error", ...restrictedGlobals],
      "no-restricted-properties": [
        "error",
        { object: "window", property: "fetch", message: "Use the api/ module." },
        { object: "globalThis", property: "fetch", message: "Use the api/ module." },
        { object: "navigator", property: "sendBeacon", message: "Telemetry is not allowed." },
      ],
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: "Backend text is rendered as text.",
        },
      ],
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-non-null-assertion": "error",
      "@typescript-eslint/explicit-function-return-type": [
        "error",
        { allowExpressions: true, allowTypedFunctionExpressions: true },
      ],
      "@typescript-eslint/explicit-module-boundary-types": "error",
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
    },
  },
  {
    files: ["src/**/*.{ts,tsx}", "test/**/*.{ts,tsx}"],
    languageOptions: { globals: globals.browser },
  },
  {
    files: ["scripts/**/*.ts", "vite.config.ts", "playwright.config.ts", "e2e/**/*.ts"],
    languageOptions: { globals: globals.node },
  },
  {
    files: ["scripts/**/*.ts", "e2e/support/run.ts"],
    rules: { "no-console": "off" },
  },
  {
    files: ["src/api/**/*.ts", "test/**/*.{ts,tsx}", "e2e/**/*.ts"],
    rules: { "no-restricted-globals": "off", "no-restricted-properties": "off" },
  },
  {
    files: ["scripts/audit-supply-chain.ts"],
    rules: {
      "no-restricted-globals": [
        "error",
        ...restrictedGlobals.filter(({ name }) => name !== "fetch"),
      ],
    },
  },
  {
    files: ["**/*.js"],
    extends: [tseslint.configs.disableTypeChecked],
    languageOptions: { globals: globals.node },
  },
);
