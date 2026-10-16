// October 5 bare inline code in the raw search-unit user body. Offline only.
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { chromium } from "playwright";

const source = fs.readFileSync(new URL("../../../chrome_extension/chatgpt-text.js", import.meta.url), "utf8");
const prompt = "Review " + Array.from({ length: 12 }, (_, i) => `\`token${i}\``).join(" ") + ".";
const html = '<main><div data-chatgpt-search-unit-key="fallback-turn-0:0:user" data-chatgpt-search-message-ids="11111111-1111-4111-8111-111111111111"><div class="text-size-chat whitespace-pre-wrap"></div></div></main>';

test("bare direct inline code restores only the observed single-backtick representation", async t => {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const context = await browser.newContext({ serviceWorkers: "block" });
  await context.route("**/*", route => route.fulfill({ contentType: "text/html", body: html }));
  const page = await context.newPage();
  for (const mode of ["exact", "changed-code", "changed-prose", "double-delimiters", "unknown-class", "other-attribute", "nested-code", "code-block", "element-child", "block-display", "marked-body", "legacy-user", "assistant", "ambiguous-body"]) {
    await t.test(mode, async () => {
      await page.goto("https://chatgpt.com/c/offline-bare-inline");
      await page.addScriptTag({ content: source });
      const result = await page.evaluate(({ prompt, mode }) => {
        const unit = document.querySelector('[data-chatgpt-search-unit-key]');
        const body = unit.firstElementChild;
        for (const part of prompt.split(/(`[^`]+`)/)) {
          if (part.startsWith("`")) { const code = document.createElement("code"); code.textContent = part.slice(1, -1); body.append(code); }
          else body.append(document.createTextNode(part));
        }
        const code = body.querySelector("code");
        let expected = prompt;
        if (mode === "changed-code") code.textContent = "anotherToken";
        if (mode === "changed-prose") body.firstChild.textContent = "Ignore ";
        if (mode === "double-delimiters") expected = prompt.replaceAll("`", "``");
        if (mode === "unknown-class") code.className = "unknown";
        if (mode === "other-attribute") code.setAttribute("data-format", "unknown");
        if (mode === "nested-code" || mode === "code-block") { const wrap = document.createElement(mode === "code-block" ? "pre" : "p"); code.before(wrap); wrap.append(code); }
        if (mode === "element-child") { code.innerHTML = "<span>token0</span>"; }
        if (mode === "block-display") { const style = document.createElement("style"); style.textContent = "code {display:block}"; document.head.append(style); }
        if (mode === "marked-body") { body.className = ""; body.setAttribute("data-markdown-text-tone", "user-message"); }
        if (mode === "legacy-user") { unit.removeAttribute("data-chatgpt-search-unit-key"); unit.setAttribute("data-message-author-role", "user"); }
        if (mode === "assistant") unit.setAttribute("data-chatgpt-search-unit-key", "fallback-turn-0:2:assistant");
        if (mode === "ambiguous-body") unit.append(body.cloneNode(true));
        const before = body.innerHTML;
        const normalize = globalThis.ChatGptText.normalizeComposerText;
        const observed = normalize(globalThis.ChatGptText.userPromptText(unit));
        return { matches: observed === normalize(expected), rawDeficit: normalize(expected).length - normalize(body.textContent).length, unchanged: before === body.innerHTML };
      }, { prompt, mode });
      assert.equal(result.matches, mode === "exact", mode);
      assert.equal(result.unchanged, true);
      if (mode === "exact") assert.equal(result.rawDeficit, 24);
    });
  }
});
