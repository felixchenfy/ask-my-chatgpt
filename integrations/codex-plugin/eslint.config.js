import js from "@eslint/js";

export default [
  // Saved independent-test source snapshots are evidence, not this plugin's code.
  { ignores: ["node_modules/**", "output/**"] },
  js.configs.recommended,
  {
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        Buffer: "readonly",
        console: "readonly",
        document: "readonly",
        getComputedStyle: "readonly",
        process: "readonly",
        setTimeout: "readonly",
        URL: "readonly",
      },
    },
  },
];
