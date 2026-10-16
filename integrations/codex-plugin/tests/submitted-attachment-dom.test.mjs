// Reduced October 4 submitted card + accessible composer + search-unit identity.
// Offline Chromium only; all traffic intercepted and inputs are synthetic.
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { chromium } from "playwright";

const agent = new URL("../../../", import.meta.url);
const filename = "review-系统复盘.pdf";
const question = "Review the exact PDF and explain its design lessons.";
const url = "https://chatgpt.com/c/offline-submitted-resource";
const html = `<main><div data-turn-key="11111111-1111-4111-8111-111111111111">
  <div data-chatgpt-search-unit-key="fallback-turn-0:0:user" data-chatgpt-search-message-ids="11111111-1111-4111-8111-111111111111"><div class="whitespace-pre-wrap">History</div></div>
  <div data-chatgpt-search-unit-key="fallback-turn-0:2:assistant" data-chatgpt-search-message-ids="22222222-2222-4222-8222-222222222222">Earlier response</div><button aria-label="Copy">Copy</button>
  </div></main><form><div contenteditable="true" role="textbox" aria-label="Ask ChatGPT" style="min-height:40px"></div><button type="submit" aria-label="Send">Send</button></form>`;

test("submitted resource-card PDF needs complete same-turn identity", async t => {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const context = await browser.newContext({ serviceWorkers: "block" });
  await context.route("**/*", route => route.fulfill({ contentType: "text/html", body: html }));
  const page = await context.newPage();
  const reset = async mode => {
    await page.goto(url);
    for (const name of ["chatgpt-text", "chatgpt-tabs", "chatgpt-submission"]) {
      await page.addScriptTag({ content: fs.readFileSync(new URL(`chrome_extension/${name}.js`, agent), "utf8") });
    }
    await page.evaluate(({ filename, question, mode }) => {
      globalThis.ChatGptSubmission.begin("resource-fixture", question, filename, globalThis.location.href);
      const turn = document.createElement("div");
      turn.setAttribute("data-turn-key", "33333333-3333-4333-8333-333333333333");
      const user = document.createElement("div");
      user.setAttribute("data-chatgpt-search-unit-key", "fallback-turn-1:0:user");
      user.setAttribute("data-chatgpt-search-message-ids", "33333333-3333-4333-8333-333333333333");
      user.innerHTML = `<div class="flex w-full flex-wrap justify-end gap-2"><span class="group/resource-card relative">
        <button type="button" class="peer/resource-card" aria-label="${filename}" aria-busy="false"></button>
        <span class="flex min-w-0 items-center"><span class="flex min-w-0 flex-col">
        <span class="truncate text-default text-sm font-semibold">${filename}</span><span class="truncate text-secondary text-sm">PDF</span></span></span>
        </span></div><div class="whitespace-pre-wrap"></div>`;
      const body = user.querySelector(".whitespace-pre-wrap"); body.textContent = question;
      const card = user.querySelector('.group\\/resource-card');
      const open = card.querySelector("button");
      const label = card.querySelector(".font-semibold");
      const type = card.querySelector(".text-secondary");
      if (mode === "wrong-open") open.setAttribute("aria-label", "wrong.pdf");
      if (mode === "wrong-display") label.textContent = "wrong.pdf";
      if (mode === "wrong-type") type.textContent = "PNG";
      if (mode === "missing-type") type.remove();
      if (mode === "missing-open") open.remove();
      if (mode === "duplicate-open") open.after(open.cloneNode(true));
      if (mode === "duplicate-label") label.after(label.cloneNode(true));
      if (mode === "duplicate-card") card.after(card.cloneNode(true));
      if (mode === "hidden") card.style.display = "none";
      if (mode === "busy") open.setAttribute("aria-busy", "true");
      if (mode === "failed") card.insertAdjacentHTML("beforeend", '<span role="alert">Upload failed</span>');
      if (mode === "inside-prompt") body.append(card);
      if (mode === "inside-marked-prompt") { body.setAttribute("data-markdown-text-tone", "user-message"); body.append(card); }
      if (mode === "outside-user") document.querySelector("main").append(card);
      if (mode === "composer") document.querySelector("form").append(card);
      if (mode === "wrong-prompt") body.textContent = "Another question.";
      if (mode === "missing-id") user.removeAttribute("data-chatgpt-search-message-ids");
      if (mode === "old-id") user.setAttribute("data-chatgpt-search-message-ids", "11111111-1111-4111-8111-111111111111");
      if (mode === "extra-legacy") card.insertAdjacentHTML("afterend", `<div role="group" aria-label="${filename}"></div>`);
      turn.append(user);
      if (mode === "history") document.querySelector("main").prepend(turn);
      else document.querySelector("main").append(turn);
    }, { filename, question, mode });
  };
  for (const mode of ["ready", "wrong-open", "wrong-display", "wrong-type", "missing-type", "missing-open", "duplicate-open", "duplicate-label", "duplicate-card", "hidden", "busy", "failed", "inside-prompt", "inside-marked-prompt", "outside-user", "composer", "wrong-prompt", "missing-id", "old-id", "extra-legacy", "history"]) {
    await t.test(mode, async () => {
      await reset(mode);
      const result = await page.evaluate(({ question, url }) => globalThis.ChatGptSubmission.inspect("resource-fixture", question, url), { question, url });
      assert.equal(result.submitted, mode === "ready", JSON.stringify(result));
      assert.equal(result.messageId, mode === "ready" ? "33333333-3333-4333-8333-333333333333" : "");
      if (mode === "ready") {
        assert.equal(result.attachment.filenameMatch, "exact");
        assert.equal(result.attachment.observedFilename, filename);
        assert.equal(result.attachment.visible, true);
      }
      if (mode === "composer") {
        const staged = await page.evaluate(filename => globalThis.ChatGptSubmission.attachments(document.querySelector("form"), filename), filename);
        assert.equal(staged.ready, false);
      }
    });
  }
});
