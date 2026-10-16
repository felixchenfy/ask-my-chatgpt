import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { ESLint } from "eslint";

test("lint ignores captured source evidence while retaining production and test files", async () => {
  const cwd = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const lint = new ESLint({ cwd });
  assert.equal(await lint.isPathIgnored(path.join(cwd, "output/continuous-pdf-tests/example/source-snapshot/service-worker.js")), true);
  assert.equal(await lint.isPathIgnored(path.join(cwd, "server.mjs")), false);
  assert.equal(await lint.isPathIgnored(path.join(cwd, "tests/response-citation-dom.test.mjs")), false);
});
