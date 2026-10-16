// User message presentation consumes single-backtick delimiters. No live traffic.
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { chromium } from "playwright";

const source = fs.readFileSync(new URL("../../../chrome_extension/chatgpt-text.js", import.meta.url), "utf8");
const prompt = "Read `alpha.txt`, `beta.txt`, and `gamma.txt`. Keep **literal marks**.";
const markup = '<div>Read <code class="user-message-inline-code">alpha.txt</code>, <code class="user-message-inline-code">beta.txt</code>, and <code class="user-message-inline-code">gamma.txt</code>. Keep **literal marks**.</div>';

test("inline-code prompt representation preserves exact input without relaxing comparison", async t => {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const context = await browser.newContext({ serviceWorkers: "block" });
  await context.route("**/*", route => route.fulfill({ contentType: "text/html", body: "<main></main>" }));
  const page = await context.newPage();
  await page.goto("https://chatgpt.com/c/offline-inline-code");
  await page.addScriptTag({ content: source });
  for (const mode of ["exact", "collapsed", "changed-content", "unknown-code", "double-delimiter", "missing-code", "ambiguous-body", "ordinary-inline"]) {
    await t.test(mode, async () => {
      const result = await page.evaluate(({ markup, prompt, mode }) => {
        const user = document.createElement("div");
        user.innerHTML = '<div data-testid="collapsible-user-message-content"></div>';
        const body = user.firstElementChild; body.innerHTML = markup;
        let expected = prompt;
        if (mode === "collapsed") body.hidden = true;
        if (mode === "changed-content") body.querySelector("code").textContent = "alpha.bin";
        if (mode === "unknown-code") body.querySelector("code").className = "unrecognized";
        if (mode === "double-delimiter") expected = prompt.replaceAll("`", "``");
        if (mode === "missing-code") body.querySelector("code").remove();
        if (mode === "ambiguous-body") user.append(body.cloneNode(true));
        if (mode === "ordinary-inline") {
          body.innerHTML = '<span>Read</span><a>able</a> text'; expected = "Readable text";
        }
        const extracted = globalThis.ChatGptText.normalizeComposerText(globalThis.ChatGptText.userPromptText(user));
        return { matches: extracted === expected, extractedLength: extracted.length, expectedLength: expected.length };
      }, { markup, prompt, mode });
      assert.equal(result.matches, ["exact", "collapsed", "ordinary-inline"].includes(mode), mode);
    });
  }
});
