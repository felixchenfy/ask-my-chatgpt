// October 4 composer structure; synthetic file/text and isolated offline DOM.
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { chromium } from "playwright";

const agent = new URL("../../../", import.meta.url);
const worker = fs.readFileSync(new URL("chrome_extension/service-worker.js", agent), "utf8");
const inspector = worker.slice(worker.indexOf("function inspectChatGptComposer("), worker.indexOf("function inspectChatGptThinkingMode("));
const html = `<main><div data-turn-key="11111111-1111-4111-8111-111111111111">
  <div data-chatgpt-search-unit-key="fallback-turn-0:0:user" data-chatgpt-search-message-ids="11111111-1111-4111-8111-111111111111"><div class="whitespace-pre-wrap">History</div><button aria-label="Copy message">Copy message</button></div>
  <div data-chatgpt-search-unit-key="fallback-turn-0:2:assistant" data-chatgpt-search-message-ids="22222222-2222-4222-8222-222222222222"><p>Earlier answer</p></div><button aria-label="Copy">Copy</button>
  </div></main>
  <form><span class="group/composer-attachment" id="card">
    <span class="composer-attachment-surface"><span class="truncate">review.pdf</span><button type="button" class="composer-attachment-surface" aria-label="review.pdf"></button></span>
    <button type="button" aria-label="Remove review.pdf"></button>
  </span><div contenteditable="true" role="textbox" aria-label="Ask ChatGPT" style="min-height:40px"></div>
  <input type="file" accept="image/*,video/*"><input type="file" accept="image/*"><input type="file">
  <button type="submit" aria-label="Send">Send</button></form>`;

test("observed composer attachment uses paired semantic controls and scoped filename", async t => {
  const browser = await chromium.launch({headless:true});
  t.after(() => browser.close());
  const context = await browser.newContext({serviceWorkers:"block"});
  await context.route("**/*", route => route.fulfill({contentType:"text/html",body:html}));
  const page = await context.newPage();
  const reset = async () => {
    await page.goto("https://chatgpt.com/c/offline-composer-attachment");
    for (const name of ["chatgpt-text","chatgpt-tabs","chatgpt-submission"]) {
      await page.addScriptTag({content:fs.readFileSync(new URL(`chrome_extension/${name}.js`,agent),"utf8")});
    }
    await page.addScriptTag({content:inspector});
  };
  const inspect = () => page.evaluate(() => globalThis.inspectChatGptComposer("Exact question","review.pdf",{readOnly:true}));
  await t.test("ready card survives empty native FileList", async () => {
    await reset();
    const before = await page.locator("form").innerHTML();
    const result = await inspect();
    assert.equal(result.attachmentReady,true);
    assert.equal(result.attachment.cardCount,1);
    assert.equal(result.attachment.filenameMatch,"exact");
    assert.equal(result.selectedFiles.length,0);
    assert.equal(result.sendReady,true);
    assert.equal(await page.locator("form").innerHTML(),before);
  });
  for (const mode of ["wrong-name","wrong-display","wrong-remove","missing-open","duplicate-open","duplicate-remove","duplicate-card","hidden","busy","failed","outside-form","inside-editor","form-busy"]) {
    await t.test(`${mode} never counts as a ready expected payload`, async () => {
      await reset();
      await page.evaluate(mode => {
        const card = document.querySelector("#card");
        const open = card.querySelector("button.composer-attachment-surface");
        const remove = card.querySelector('button[aria-label^="Remove"]');
        if (mode === "wrong-name") open.setAttribute("aria-label","other.pdf");
        if (mode === "wrong-display") card.querySelector(".truncate").textContent = "other.pdf";
        if (mode === "wrong-remove") remove.setAttribute("aria-label","Remove other.pdf");
        if (mode === "missing-open") open.remove();
        if (mode === "duplicate-open") open.after(open.cloneNode(true));
        if (mode === "duplicate-remove") remove.after(remove.cloneNode(true));
        if (mode === "duplicate-card") card.after(card.cloneNode(true));
        if (mode === "hidden") card.style.display = "none";
        if (mode === "busy") card.setAttribute("aria-busy","true");
        if (mode === "form-busy") document.querySelector("form").setAttribute("aria-busy","true");
        if (mode === "failed") card.insertAdjacentHTML("beforeend",'<span role="alert">Upload failed</span>');
        if (mode === "outside-form") document.querySelector("main").append(card);
        if (mode === "inside-editor") document.querySelector('[contenteditable="true"]').append(card);
      },mode);
      assert.equal((await inspect()).attachmentReady,false);
    });
  }
  await t.test("a removed card remains removed; stale native selection never establishes readiness", async () => {
    await reset(); await inspect();
    await page.evaluate(() => document.querySelector("#card").remove());
    await page.locator('input[type="file"]').last().setInputFiles({name:"review.pdf",mimeType:"application/pdf",buffer:Buffer.from("%PDF-1.4\nfixture")});
    const result = await inspect();
    assert.equal(result.attachment.state,"removed");
    assert.equal(result.attachment.selected,true);
    assert.equal(result.attachmentReady,false);
  });
});
