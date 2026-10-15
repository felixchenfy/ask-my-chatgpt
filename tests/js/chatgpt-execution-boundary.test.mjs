// The shared execution helper is used outside ChatGPT too. Preserve its defaults.
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const worker = fs.readFileSync(new URL("../../chrome_extension/service-worker.js", import.meta.url), "utf8");
const helper = worker.slice(worker.indexOf("async function execute(tabId,"), worker.indexOf("async function prepareChatGptText("));
function transport(injection) {
  const scope = { chrome: { scripting: { executeScript: async () => [injection] } } };
  vm.runInNewContext(helper, scope);
  return scope.execute;
}

test("shared execution still throws page errors by default", async () => {
  await assert.rejects(transport({ result: { error: "page failed" } })(1, () => {}), /page failed/);
  await assert.rejects(transport({ error: { message: "injection failed" } })(1, () => {}), /Injected script failed: injection failed/);
});
test("composer can retain a structured error for observation before throwing", async () => {
  const result = { ready: false, error: "manual verification", errorCode: "manual_verification_required" };
  assert.equal(await transport({ result })(1, () => {}, [], { preserveErrorResult: true }), result);
});
test("legitimate void cleanup remains allowed by the shared helper", async () => {
  assert.equal(await transport({ result: undefined })(1, () => {}), undefined);
});
