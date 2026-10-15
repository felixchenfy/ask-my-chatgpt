// Offline VM fixture: executes the real inspector, no browser or network.
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../../chrome_extension/service-worker.js", import.meta.url), "utf8");
const inspector = source.slice(source.indexOf("function inspectChatGptComposer("), source.indexOf("function inspectChatGptThinkingMode("));
function inspect({ body = "", title = "ChatGPT", present = false, hidden = false, challenge = false, filename } = {}) {
  const composer = present ? {
    tagName: "TEXTAREA", offsetParent: hidden ? null : {}, value: "", scrollIntoView() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 200, height: 50 }),
  } : null;
  const scope = { document: {
    body: { innerText: body }, title, readyState: "complete", visibilityState: "hidden",
    hasFocus: () => false,
    querySelector: selector => selector === "#prompt-textarea" ? composer
      : selector.includes('iframe[src*=') && challenge ? {} : null,
    querySelectorAll: () => [],
  } };
  scope.self = scope;
  vm.runInNewContext(fs.readFileSync(new URL("../../chrome_extension/chatgpt-text.js", import.meta.url), "utf8"), scope);
  vm.runInNewContext(fs.readFileSync(new URL("../../chrome_extension/chatgpt-submission.js", import.meta.url), "utf8"), scope);
  vm.runInNewContext(inspector, scope);
  return scope.inspectChatGptComposer("offline question", filename);
}

test("login and challenge states have distinct constant codes and no page text in evidence", () => {
  for (const [options, code] of [
    [{ body: "Log in. private-account@example.invalid" }, "not_signed_in"],
    [{ challenge: true, body: "private-account@example.invalid" }, "manual_verification_required"],
  ]) {
    const r = inspect(options);
    assert.equal(r.ready, false); assert.equal(r.errorCode, code);
    assert.equal(r.composerEvidence.present, false);
    assert.equal(r.renderEvidence.visibility, "hidden");
    assert.ok(!JSON.stringify(r).includes("private-account"));
  }
});
test("absent versus hidden selector remains distinguishable without guessing a root cause", () => {
  const absent = inspect();
  const hidden = inspect({ present: true, hidden: true });
  assert.equal(absent.error, undefined); assert.equal(hidden.error, undefined);
  assert.equal(absent.composerEvidence.present, false);
  assert.equal(hidden.composerEvidence.present, true);
  assert.equal(hidden.composerEvidence.visible, false);
});
test("ready composer preserves the original input contract", () => {
  const r = inspect({ present: true, body: "A prior discussion about log in" });
  assert.equal(r.ready, true); assert.equal(r.error, undefined);
  assert.equal(r.composerEvidence.visible, true);
  assert.equal(r.expectedQuestionNormalized, "offline question");
});
test("text-only JSON null filename reaches the real attachment helper without throwing", () => {
  const filename = JSON.parse('{"attachment_filename":null}').attachment_filename;
  const r = inspect({ present: true, filename });
  assert.equal(r.ready, true);
  assert.equal(r.composerEvidence.visible, true);
  assert.equal(r.attachment.state, "none");
  assert.equal(r.attachment.expectedFilename, "");
  assert.equal(r.attachmentReady, true);
});
for (const filename of [undefined, ""]) {
  test(`text-only ${String(filename)} filename retains the no-attachment contract`, () => {
    const r = inspect({ present: true, filename });
    assert.equal(r.ready, true);
    assert.equal(r.attachment.state, "none");
    assert.equal(r.attachment.expectedFilename, "");
  });
}
test("a requested PDF remains required, never normalized to an absent attachment", () => {
  const r = inspect({ present: true, filename: "required-review.pdf" });
  assert.equal(r.ready, true);
  assert.equal(r.attachment.expectedFilename, "required-review.pdf");
  assert.equal(r.attachment.state, "missing");
  assert.equal(r.attachmentReady, false);
});
