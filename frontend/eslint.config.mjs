import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";
import formatjsPlugin from "eslint-plugin-formatjs";

export default [
  ...coreWebVitals,
  ...typescript,
  {
    rules: {
      // These strict experimental react-hooks rules flag common intentional patterns
      // (Date.now() in render, setState-in-effect) that work correctly in this codebase.
      // Downgraded to warn until the codebase is audited for full compliance.
      "react-hooks/purity": "warn",
      "react-hooks/set-state-in-effect": "warn",
    },
  },
  // i18n guard: flag untranslated literal strings inside JSX.
  // Scoped to app/** and components/** only (excludes tests, lib, scripts).
  // Plugin: eslint-plugin-formatjs (unscoped) v6.x — @formatjs/eslint-plugin-formatjs
  // is not published to npm; this package exposes the same rule set.
  // Note: the rule has no built-in string allowlist; legitimate brand/symbol
  // literals ("Applire", "DELETE", "—", "→", "·", "|") are handled via
  // eslint-disable comments added during the sweep tasks (3.3–3.10).
  {
    plugins: {
      formatjs: formatjsPlugin,
    },
    files: ["app/**/*.{ts,tsx}", "components/**/*.{ts,tsx}"],
    ignores: [
      "**/__tests__/**",
      "**/*.test.{ts,tsx}",
      "**/*.spec.{ts,tsx}",
    ],
    rules: {
      "formatjs/no-literal-string-in-jsx": "error",
    },
  },
];
