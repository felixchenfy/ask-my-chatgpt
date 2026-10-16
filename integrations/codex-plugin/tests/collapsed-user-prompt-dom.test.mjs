import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { chromium } from "playwright";

const root = new URL("../../../chrome_extension/", import.meta.url);
const url = "https://chatgpt.com/c/offline-collapsed-prompt";
const source = ["chatgpt-text", "chatgpt-tabs", "chatgpt-submission"].map(n => fs.readFileSync(new URL(n + ".js", root), "utf8"));
const prefix = "# Review\n- preserve unknown\n| field | value |\n| --- | --- |\n| count | 3 |\ndef inspect(rows):\n    return ";
const question = prefix + "rows\n# Final sentinel.";
const user = '<div data-chatgpt-search-unit-key="fallback-turn-1:0:user" data-chatgpt-search-message-ids="22222222-2222-4222-8222-222222222222"><div data-user-message-bubble="true"><div class="whitespace-pre-wrap"><div data-markdown-text-tone="user-message"><p></p></div></div><button type="button" aria-expanded="false" data-thread-find-skip="true" data-markdown-copy="exclude">Show more</button></div></div>';
const html = '<main><div data-chatgpt-search-unit-key="fallback-turn-0:0:user" data-chatgpt-search-message-ids="11111111-1111-4111-8111-111111111111"><div class="whitespace-pre-wrap">Earlier</div></div></main><form><div id="prompt-textarea" contenteditable="true"></div></form>';

test("collapsed new user disclosure never replaces complete submission checks", async t => {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.route("**/*", route => route.fulfill({ contentType: "text/html", body: html }));
  for (const mode of ["exact", "noop", "changed-tail", "changed-prefix", "historical", "two-candidates", "missing-id", "wrong-url", "draft", "hidden", "disabled", "aria-disabled", "two-buttons", "inside-prompt", "wrong-label", "submit-button", "missing-marker", "missing-bubble", "other-arguments"]) {
    await t.test(mode, async () => {
      await page.goto(url);
      for (const content of source) await page.addScriptTag({ content });
      const result = await page.evaluate(async ({ mode, user, prefix, question, url }) => {
        const host = document.querySelector('main');
        const add = () => { host.insertAdjacentHTML('beforeend', user); return host.lastElementChild; };
        let node;
        if (mode === "historical") node = add();
        globalThis.ChatGptSubmission.begin('test', question, '', url);
        node ||= add();
        const body = node.querySelector('[data-markdown-text-tone] p');
        body.textContent = mode === "changed-prefix" ? "Different request" : prefix;
        const button = node.querySelector('button');
        let clicks = 0, sends = 0;
        document.querySelector('form').addEventListener('submit', e => { sends++; e.preventDefault(); });
        button.addEventListener('click', () => {
          clicks++;
          if (mode !== "noop") body.textContent = mode === "changed-tail" ? prefix + "incorrect tail" : question;
        });
        if (mode === "two-candidates") add();
        if (mode === "missing-id") node.removeAttribute('data-chatgpt-search-message-ids');
        if (mode === "draft") document.querySelector('#prompt-textarea').textContent = 'Unrelated draft';
        if (mode === "hidden") button.style.display = 'none';
        if (mode === "disabled") button.disabled = true;
        if (mode === "aria-disabled") button.setAttribute('aria-disabled', 'true');
        if (mode === "two-buttons") button.after(button.cloneNode(true));
        if (mode === "inside-prompt") body.append(button);
        if (mode === "wrong-label") button.textContent = 'Edit message';
        if (mode === "submit-button") button.type = 'submit';
        if (mode === "missing-marker") button.removeAttribute('data-thread-find-skip');
        if (mode === "missing-bubble") node.querySelector('[data-user-message-bubble]').removeAttribute('data-user-message-bubble');
        await Promise.resolve();
        const dest = mode === "wrong-url" ? url + '-other' : url;
        const before = globalThis.ChatGptSubmission.inspect('test', question, dest);
        const expanded = globalThis.ChatGptSubmission.expand('test', mode === "other-arguments" ? question + 'different' : question, dest);
        const after = globalThis.ChatGptSubmission.inspect('test', question, dest);
        globalThis.ChatGptSubmission.expand('test', question, dest);
        const finalClicks = clicks;
        globalThis.ChatGptSubmission.end('test');
        return { before, after, expanded, clicks: finalClicks, sends };
      }, { mode, user, prefix, question, url });
      assert.equal(result.before.submitted, false, mode);
      assert.equal(result.after.submitted, mode === "exact", mode);
      assert.equal(result.expanded.expanded, ["exact", "noop", "changed-tail"].includes(mode), mode);
      // The deliberately different-argument first call fails; the valid second
      // call may expand once, but never confirms inside expand().
      assert.equal(result.clicks, ["exact", "noop", "changed-tail", "other-arguments"].includes(mode) ? 1 : 0, mode);
      assert.equal(result.sends, 0);
    });
  }
});
