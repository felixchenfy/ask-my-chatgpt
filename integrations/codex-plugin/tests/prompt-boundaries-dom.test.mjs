// Offline reproduction of paragraph boundaries observed on 2026-09-04.
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { chromium } from "playwright";

const agent = new URL("../../../", import.meta.url);
const source = fs.readFileSync(new URL("chrome_extension/service-worker.js", agent), "utf8");
const start = source.indexOf("function inspectChatGptSubmission");
const inspect = source.slice(start, source.indexOf("function inspectChatGptResponse", start));
const url = "https://chatgpt.com/c/offline-boundaries";
const prompt = "Opening\n| affine | 20/30 |\n**Orientation improves**\n| affine | 954 ms |\nThese are medians.\n</div>\n----- END -----";
const markup = "<div><p>Opening<br>\n| affine | 20/30 |</p><p>**Orientation improves**<br>\n| affine | 954 ms |</p><p>These are medians.<br>\n&lt;/div&gt;</p>----- END -----</div>";

test("paragraph serialization preserves exact prompt and submission identity", async t => {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const context = await browser.newContext({ serviceWorkers: "block" });
  await context.route("**/*", route => route.fulfill({ contentType: "text/html", body: '<main><div data-message-author-role="user" data-message-id="old"><div class="whitespace-pre-wrap">History</div></div><form><textarea id="prompt-textarea"></textarea></form></main>' }));
  const page = await context.newPage();
  for (const mode of ["exact", "collapsed", "same-length-change", "truncated", "historical", "newer-user", "inline"]) {
    await t.test(mode, async () => {
      await page.goto(url);
      for (const name of ["chatgpt-text", "chatgpt-tabs", "chatgpt-submission"]) {
        await page.addScriptTag({ content: fs.readFileSync(new URL(`chrome_extension/${name}.js`, agent), "utf8") });
      }
      await page.addScriptTag({ content: inspect });
      const actual = await page.evaluate(({ prompt, markup, mode, url }) => {
        const node = document.createElement("div");
        node.dataset.messageAuthorRole = "user"; node.dataset.messageId = "candidate";
        node.innerHTML = '<div data-testid="collapsible-user-message-content"></div><button>Show more</button>';
        const body = node.firstElementChild; body.innerHTML = markup;
        if (mode === "same-length-change") body.innerHTML = markup.replace("20/30", "21/30");
        if (mode === "truncated") body.lastElementChild.lastChild.textContent = "----- END ----";
        if (mode === "collapsed") body.querySelectorAll("p").forEach(p => { p.style.display = "none"; });
        if (mode === "inline") body.innerHTML = '<span>Open</span><a>ing</a><br>' + markup.slice(markup.indexOf("| affine"));
        const old = document.querySelector('[data-message-id="old"]');
        globalThis.ChatGptSubmission.begin("fixture", prompt, "", url);
        if (mode === "historical") old.before(node); else old.after(node);
        if (mode === "newer-user") {
          const newer = node.cloneNode(true); newer.dataset.messageId = "newer";
          newer.firstElementChild.textContent = "Unrelated question"; node.after(newer);
        }
        return { raw: globalThis.ChatGptText.normalizeComposerText(body.textContent), extracted: globalThis.ChatGptText.normalizeComposerText(globalThis.ChatGptText.userPromptText(node)) };
      }, { prompt, markup, mode, url });
      const result = await page.evaluate(({ prompt, url }) => globalThis.inspectChatGptSubmission("fixture", prompt, url), { prompt, url });
      if (["exact", "collapsed", "inline"].includes(mode)) {
        assert.notEqual(actual.raw, prompt.replace(/\s+/g, " "));
        assert.equal(actual.extracted, prompt.replace(/\s+/g, " "));
        assert.equal(result.submitted, true); assert.equal(result.messageId, "candidate");
      } else {
        assert.equal(result.submitted, false); assert.equal(result.messageId, "");
      }
    });
  }
});
