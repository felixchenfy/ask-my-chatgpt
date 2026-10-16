import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { chromium } from "playwright";

const source = fs.readFileSync(new URL("../../../chrome_extension/chatgpt-text.js", import.meta.url), "utf8");
const codeText = 'def example(value):\n    return value < 7\n';
const prompt = `Review code:\n\`\`\`python\n${codeText}\`\`\``;
const html = '<div data-chatgpt-search-unit-key="fallback-turn-0:0:user"><div class="whitespace-pre-wrap">Review code:\n<pre data-markdown-copy="code-block"><code data-language="python" data-markdown-copy-content="true"></code></pre></div></div>';

test("raw user fenced code restores only the observed PRE/CODE structure", async t => {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  for (const mode of ["exact", "unlabelled", "unlabelled-changed-code", "unlabelled-four-backticks", "empty-language", "changed-code", "changed-prose", "changed-language", "four-backticks", "tilde-fence", "missing-language", "invalid-language", "missing-copy", "extra-attribute", "nested-code", "extra-pre-child", "nested-pre", "missing-pre-marker", "marked-body", "assistant", "legacy-user", "ambiguous-body"]) {
    await t.test(mode, async () => {
      await page.setContent(html);
      await page.addScriptTag({ content: source });
      const result = await page.evaluate(({ mode, prompt, codeText }) => {
        const unit = document.querySelector('[data-chatgpt-search-unit-key]');
        const body = unit.firstElementChild, pre = body.querySelector('pre'), code = pre.firstElementChild;
        code.textContent = codeText;
        let expected = prompt;
        if (mode.startsWith("unlabelled") || mode === "empty-language") {
          expected = prompt.replace('```python', '```');
          code.removeAttribute('data-language');
          if (mode === "unlabelled-changed-code") code.textContent = codeText.replace('7', '8');
          if (mode === "unlabelled-four-backticks") expected = expected.replaceAll('```', '````');
          if (mode === "empty-language") code.setAttribute('data-language', '');
        }
        if (mode === "changed-code") code.textContent = codeText.replace('7', '8');
        if (mode === "changed-prose") body.firstChild.textContent = 'Different code:\n';
        if (mode === "changed-language") code.setAttribute('data-language', 'js');
        if (mode === "four-backticks") expected = prompt.replaceAll('```', '````');
        if (mode === "tilde-fence") expected = prompt.replaceAll('```', '~~~');
        if (mode === "missing-language") code.removeAttribute('data-language');
        if (mode === "invalid-language") code.setAttribute('data-language', 'python extra');
        if (mode === "missing-copy") code.removeAttribute('data-markdown-copy-content');
        if (mode === "extra-attribute") code.setAttribute('data-unknown', 'true');
        if (mode === "nested-code") { const span = document.createElement('span'); span.textContent = codeText; code.replaceChildren(span); }
        if (mode === "extra-pre-child") pre.append(document.createTextNode('extra'));
        if (mode === "nested-pre") { const div = document.createElement('div'); pre.before(div); div.append(pre); }
        if (mode === "missing-pre-marker") pre.removeAttribute('data-markdown-copy');
        if (mode === "marked-body") { body.className = ''; body.setAttribute('data-markdown-text-tone', 'user-message'); }
        if (mode === "assistant") unit.setAttribute('data-chatgpt-search-unit-key', 'fallback-turn-0:2:assistant');
        if (mode === "legacy-user") { unit.removeAttribute('data-chatgpt-search-unit-key'); unit.setAttribute('data-message-author-role', 'user'); }
        if (mode === "ambiguous-body") unit.append(body.cloneNode(true));
        const before = unit.outerHTML;
        const norm = globalThis.ChatGptText.normalizeComposerText;
        return { matches: norm(globalThis.ChatGptText.userPromptText(unit)) === norm(expected), deficit: norm(expected).length - norm(body.textContent).length, unchanged: unit.outerHTML === before };
      }, { mode, prompt, codeText });
      assert.equal(result.matches, ["exact", "unlabelled"].includes(mode), mode);
      assert.equal(result.unchanged, true);
      if (mode === "exact") assert.equal(result.deficit, 14);
    });
  }
});
