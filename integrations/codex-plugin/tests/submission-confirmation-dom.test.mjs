// Offline Chromium regression tests. All traffic intercepted, no signed-in profile.
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { chromium } from "playwright";

const agentRoot = new URL("../../../", import.meta.url);
const source = fs.readFileSync(new URL("chrome_extension/service-worker.js", agentRoot), "utf8");
const block = (a, b) => source.slice(source.indexOf(a), source.indexOf(b, source.indexOf(a)));
const fixture = fs.readFileSync(new URL("tests/fixtures/chatgpt-pdf-user-turn.html", agentRoot), "utf8");
const url = "https://chatgpt.com/c/offline-confirmation";
const filename = "bridge-unicode-10000-regression.pdf";
const question = `Read ${filename} and return every line.`;

test("submission identity and attachment staging use scoped DOM evidence", async t => {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const context = await browser.newContext({ serviceWorkers: "block" });
  await context.route("**/*", route => route.fulfill({ contentType: "text/html", body: fixture }));
  const page = await context.newPage();
  const reset = async (attachment = "") => {
    await page.goto(url);
    for (const name of ["chatgpt-text", "chatgpt-tabs", "chatgpt-submission"]) {
      const file = new URL(`chrome_extension/${name}.js`, agentRoot);
      if (fs.existsSync(file)) await page.addScriptTag({ content: fs.readFileSync(file, "utf8") });
    }
    await page.addScriptTag({ content: block("function inspectChatGptComposer", "function inspectChatGptThinkingMode") });
    await page.addScriptTag({ content: block("function inspectChatGptSubmission", "function inspectChatGptResponse") });
    await page.evaluate(([text, file]) => {
      globalThis.template = document.querySelector('[data-message-id="pdf-user"]').cloneNode(true);
      document.querySelector('[data-message-id="pdf-user"]').remove();
      document.querySelector('#prompt-textarea').value = text;
      globalThis.ChatGptSubmission?.begin("fixture", text, file, globalThis.location.href);
    }, [question, attachment]);
  };
  const add = async mode => page.evaluate(mode => {
    const old = document.querySelector('[data-message-id="old-user"]');
    const next = globalThis.template.cloneNode(true);
    if (mode === "missing-id") next.removeAttribute("data-message-id");
    if (mode === "no-pdf") next.querySelector('[role="group"]').remove();
    if (mode === "different") next.querySelector('[data-testid="collapsible-user-message-content"]').textContent = "A concurrent different question.";
    old.after(next);
    if (mode === "unmount") old.remove();
    if (mode === "history") { const history = globalThis.template.cloneNode(true); history.setAttribute("data-message-id", "historical"); old.before(history); }
    document.querySelector('#prompt-textarea').value = "";
  }, mode);
  const observe = () => page.evaluate(([text, target]) => globalThis.inspectChatGptSubmission(
    globalThis.ChatGptSubmission ? "fixture" : 1, text, target), [question, url]);

  for (const mode of ["normal", "unmount", "history"]) await t.test(`${mode}: exact new user is identified independently of mounted counts`, async () => {
    await reset(filename); await add(mode);
    const result = await observe();
    assert.equal(result.submitted, true); assert.equal(result.messageId, "pdf-user");
  });
  await t.test("history alone and a missing ordering anchor never prove a new turn", async () => {
    await reset();
    await page.evaluate(() => document.querySelector('[data-message-id="old-user"]').before(globalThis.template.cloneNode(true)));
    assert.equal((await observe()).submitted, false);
    await reset();
    await page.evaluate(() => document.querySelector('[data-message-id="old-user"]').remove());
    await page.evaluate(() => document.querySelector("form").before(globalThis.template.cloneNode(true)));
    assert.equal((await observe()).submitted, false);
  });
  await t.test("an old matching stable ID is not a new submission", async () => {
    await reset();
    await page.evaluate(() => { globalThis.template.setAttribute("data-message-id", "old-user"); });
    await add("normal");
    assert.equal((await observe()).submitted, false);
  });
  await t.test("a temporarily missing ID can become confirmed on the witnessed node", async () => {
    await reset(filename); await add("missing-id");
    assert.equal((await observe()).submitted, false);
    await page.locator('[data-message-author-role="user"]').last().evaluate(node => node.setAttribute("data-message-id", "now-stable"));
    assert.equal((await observe()).messageId, "now-stable");
    assert.equal((await observe()).submitted, true);
  });
  await t.test("a concurrent different question is never an anchor", async () => {
    await reset(); await add("different");
    assert.equal((await observe()).submitted, false);
    await page.evaluate(() => document.querySelector("form").before(globalThis.template.cloneNode(true)));
    assert.equal((await observe()).submitted, false);
  });
  await t.test("the intended PDF must be on the verified new user turn", async () => {
    await reset(filename); await add("no-pdf");
    const result = await observe();
    assert.equal(result.submitted, false);
    assert.equal(result.candidateMessageId, "pdf-user");
    assert.equal(result.candidateVerified, false);
    assert.equal(result.attachment.state, "missing");
  });
  await t.test("an unmounted candidate remains diagnostic, never confirmed", async () => {
    await reset(filename); await add("no-pdf");
    assert.equal((await observe()).candidateMessageId, "pdf-user");
    await page.locator('[data-message-id="pdf-user"]').evaluate(node => node.remove());
    const result = await observe();
    assert.equal(result.candidateMessageId, "pdf-user");
    assert.equal(result.candidateMounted, false);
    assert.equal(result.messageId, ""); assert.equal(result.submitted, false);
  });
  await t.test("removing a previously observed current card is explicit, not ready", async () => {
    await reset(filename);
    await page.evaluate(() => document.querySelector("form").append(globalThis.template.querySelector('[role="group"]').cloneNode(true)));
    assert.equal((await page.evaluate(([text, file]) => globalThis.inspectChatGptComposer(text, file), [question, filename])).attachmentReady, true);
    await page.locator('form [role="group"]').evaluate(node => node.remove());
    const result = await page.evaluate(([text, file]) => globalThis.inspectChatGptComposer(text, file), [question, filename]);
    assert.equal(result.attachmentReady, false); assert.equal(result.attachment.state, "removed");
  });
  await t.test("composer filename in prompt/history/file input is not a staged card", async () => {
    await reset(filename);
    await page.evaluate(file => {
      const input = document.createElement("input"); input.type = "file";
      const data = new globalThis.DataTransfer(); data.items.add(new globalThis.File(["%PDF"], file, { type: "application/pdf" }));
      input.files = data.files; document.querySelector("form").append(input);
      document.querySelector("form").before(globalThis.template.cloneNode(true));
    }, filename);
    const result = await page.evaluate(([text, file]) => globalThis.inspectChatGptComposer(text, file), [question, filename]);
    assert.equal(result.attachmentVisible, false);
    assert.equal(result.attachmentReady, false);
  });
  for (const state of ["ready", "disabled", "hidden", "duplicate", "button-type", "wrong-form", "other-form"]) await t.test(`accessible Send is a unique visible submitter in the composer form: ${state}`, async () => {
    await reset();
    await page.evaluate(state => {
      const send = document.querySelector('[data-testid="send-button"]');
      send.removeAttribute("data-testid"); send.setAttribute("aria-label", "Send"); send.type = "submit";
      if (state === "disabled") send.disabled = true;
      if (state === "hidden") send.style.display = "none";
      if (state === "duplicate") send.after(send.cloneNode(true));
      if (state === "button-type") send.type = "button";
      if (state === "wrong-form" || state === "other-form") {
        const other = document.createElement("form"); other.id = "other-form"; document.body.append(other);
        if (state === "wrong-form") send.setAttribute("form", "other-form");
        else other.append(send.cloneNode(true));
      }
    }, state);
    const result = await page.evaluate(() => globalThis.inspectChatGptComposer("", null, { readOnly: true }));
    assert.equal(result.sendReady, state === "ready" || state === "other-form");
    if (state === "button-type") assert.equal(result.composerDiagnostic.sendButtonType, "button");
    if (state === "disabled") assert.equal(result.composerDiagnostic.sendButtonDisabled, true);
  });
  await t.test("editor paragraph and URL wrappers preserve authored text despite empty value and layout breaks", async () => {
    await reset();
    const result = await page.evaluate(() => {
      const editor = document.createElement("div");
      editor.contentEditable = "true"; editor.setAttribute("role", "textbox");
      editor.setAttribute("aria-label", "Ask ChatGPT"); editor.value = "";
      document.querySelector('#prompt-textarea').replaceWith(editor);
      const question = "Review this version.\n目录：https://example.invalid/one/\n总报告：https://example.invalid/two/\n当前版本：https://example.invalid/three/ (same path)\nKeep real spaces here.";
      editor.innerHTML = '<p>Review this version.</p><p>目录：<span style="display:block">https://example.invalid/one/</span></p><p>总报告：<span style="display:block">https://example.invalid/two/</span></p><p>当前版本：<span style="display:block">https://example.invalid/three/</span> (same path)</p><p>Keep real spaces here.</p>';
      const normalize = globalThis.ChatGptText.normalizeComposerText;
      const observed = globalThis.inspectChatGptComposer(question, null, { readOnly: true });
      const before = editor.innerHTML;
      const exact = observed.composerValueNormalized === observed.expectedQuestionNormalized;
      const innerMatches = normalize(editor.innerText) === normalize(question);
      const contentMatches = normalize(editor.textContent) === normalize(question);
      const unchanged = before === editor.innerHTML;
      editor.querySelector('span').textContent = "https://example.invalid/changed/";
      const changed = globalThis.inspectChatGptComposer(question, null, { readOnly: true });
      return { exact, innerMatches, contentMatches, unchanged,
        editedMatches: changed.composerValueNormalized === changed.expectedQuestionNormalized };
    });
    assert.deepEqual(result, { exact: true, innerMatches: false, contentMatches: false, unchanged: true, editedMatches: false });
  });
  for (const state of ["unique", "duplicate", "hidden", "outside-form", "dialog", "history", "wrong-label"]) await t.test(`accessible composer without legacy identity: ${state}`, async () => {
    await reset();
    await page.evaluate(state => {
      const editable = document.createElement("div");
      editable.contentEditable = "true"; editable.setAttribute("role", "textbox");
      editable.setAttribute("aria-label", state === "wrong-label" ? "Search" : "Ask ChatGPT");
      editable.setAttribute("aria-multiline", "true");
      editable.style.cssText = "min-height:50px;width:400px";
      document.querySelector('#prompt-textarea').replaceWith(editable);
      if (state === "duplicate") editable.after(editable.cloneNode(true));
      if (state === "hidden") editable.style.display = "none";
      if (state === "outside-form") document.body.append(editable);
      if (state === "dialog") document.querySelector("form").setAttribute("role", "dialog");
      if (state === "history") document.querySelector("form").setAttribute("data-message-author-role", "user");
    }, state);
    const result = await page.evaluate(() => globalThis.inspectChatGptComposer("", null, { readOnly: true }));
    assert.equal(result.ready, state === "unique");
    assert.equal(result.composerEvidence.present, state === "unique");
    assert.equal(await page.locator('#prompt-textarea, [data-testid*="composer"], textarea').count(), 0);
  });
  for (const state of ["empty", "ready", "wrong", "uploading"]) await t.test(`retained native FileList with no Send control: ${state}`, async () => {
    await reset(filename);
    await page.evaluate(state => {
      const form = document.querySelector("form");
      document.querySelector('#prompt-textarea').value = "";
      document.querySelector('[data-testid="send-button"]').remove();
      const input = document.createElement("input"); input.type = "file";
      const data = new globalThis.DataTransfer();
      data.items.add(new globalThis.File(["%PDF previous upload"], "previous.pdf"));
      input.files = data.files; form.append(input);
      if (state === "ready" || state === "wrong") {
        const tile = globalThis.template.querySelector('[role="group"]').cloneNode(true);
        if (state === "wrong") tile.setAttribute("aria-label", "wrong.pdf");
        form.append(tile);
      }
      if (state === "uploading") {
        const progress = document.createElement("progress"); form.append(progress);
      }
    }, state);
    const result = await page.evaluate(file => globalThis.inspectChatGptComposer("", file, { readOnly: true }), filename);
    assert.equal(result.composerDiagnostic.formPresent, true);
    assert.equal(result.composerDiagnostic.selectedFileCount, 1);
    assert.equal(result.composerDiagnostic.selectedFilename, "previous.pdf");
    assert.equal(result.composerDiagnostic.composerCharacterCount, 0);
    assert.equal(result.draftUploadInProgress, state === "uploading");
    assert.equal(result.attachment.cardCount, state === "ready" || state === "wrong" ? 1 : 0);
    assert.equal(result.attachmentReady, state === "ready");
    assert.equal(result.attachment.state, state === "wrong" ? "unexpected" : state === "ready" ? "ready" : "missing");
    if (state === "empty") {
      const textOnly = await page.evaluate(() => globalThis.inspectChatGptComposer("", "", { readOnly: true }));
      assert.equal(textOnly.attachment.state, "none");
      assert.equal(textOnly.attachmentReady, true);
    }
    assert.equal(await page.locator('input[type="file"]').last().evaluate(input => input.files[0].name), "previous.pdf");
  });
  for (const state of ["ready", "uploading", "failed", "removed", "additional"]) await t.test(`current attachment: ${state}`, async () => {
    await reset(filename);
    await page.evaluate(([file, state]) => {
      const form = document.querySelector("form");
      const input = document.createElement("input"); input.type = "file";
      const data = new globalThis.DataTransfer(); data.items.add(new globalThis.File(["%PDF"], file, { type: "application/pdf" }));
      input.files = data.files; form.append(input);
      const tile = globalThis.template.querySelector('[role="group"]').cloneNode(true); form.append(tile);
      if (state === "uploading") tile.setAttribute("aria-busy", "true");
      if (state === "failed") { const error = document.createElement("span"); error.setAttribute("role", "alert"); error.textContent = "Upload failed"; tile.append(error); }
      if (state === "removed") tile.remove();
      if (state === "additional") { const extra = tile.cloneNode(true); extra.setAttribute("aria-label", "unexpected.pdf"); form.append(extra); }
    }, [filename, state]);
    const result = await page.evaluate(([text, file]) => globalThis.inspectChatGptComposer(text, file), [question, filename]);
    assert.equal(result.attachmentReady, state === "ready");
    assert.equal(result.attachment.state, state === "removed" ? "selected" : state === "additional" ? "unexpected" : state);
  });
});
