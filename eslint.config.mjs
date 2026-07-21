const browserGlobals = {
  console: "readonly",
  document: "readonly",
  fetch: "readonly",
  Intl: "readonly",
  URL: "readonly",
};

const nodeGlobals = {
  Buffer: "readonly",
  console: "readonly",
  process: "readonly",
};

export default [
  {
    files: ["public/*.js"],
    languageOptions: {ecmaVersion: 2024, sourceType: "module", globals: browserGlobals},
    rules: {
      "no-undef": "error",
      "no-unused-vars": ["error", {argsIgnorePattern: "^_"}],
      eqeqeq: ["error", "always"],
      "no-var": "error",
      "prefer-const": "error"
    }
  },
  {
    files: ["scripts/*.mjs", "tests/js/*.mjs"],
    languageOptions: {ecmaVersion: 2024, sourceType: "module", globals: nodeGlobals},
    rules: {
      "no-undef": "error",
      "no-unused-vars": ["error", {argsIgnorePattern: "^_"}],
      eqeqeq: ["error", "always"],
      "no-var": "error",
      "prefer-const": "error"
    }
  }
];
