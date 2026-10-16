import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { chromium } from "playwright";

const agentRoot = new URL("../../../", import.meta.url);
const fixture = fs.readFileSync(new URL("tests/fixtures/chatgpt-file-citation-json.html", agentRoot), "utf8");
const worker = fs.readFileSync(new URL("chrome_extension/service-worker.js", agentRoot), "utf8");
const inspect = worker.slice(worker.indexOf("function inspectChatGptResponse"), worker.indexOf("function summarizeChatGptObservation"));
const url = "https://chatgpt.com/c/offline-citation-response";

test("production response extraction excludes file-citation layout from authored text", async t => {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const context = await browser.newContext({ serviceWorkers: "block" });
  await context.route("**/*", route => route.fulfill({ contentType: "text/html", body: fixture }));
  const page = await context.newPage();
  const reset = async () => {
    await page.goto(url);
    for (const name of ["chatgpt-text", "chatgpt-tabs"]) {
      await page.addScriptTag({ content: fs.readFileSync(new URL(`chrome_extension/${name}.js`, agentRoot), "utf8") });
    }
    await page.addScriptTag({ content: inspect });
  };
  const read = () => page.evaluate(url => {
    const state = globalThis.inspectChatGptResponse(0, false, 0, "", "fixture-user", url);
    return { ...state, selected: globalThis.ChatGptText.chooseBestResponse(state.responseCandidates || []) };
  }, url);
  const setBody = html => page.locator(".markdown").evaluate((node, html) => { node.innerHTML = html; }, html);
  const citation = '<span data-file-citation-group-identity="fixture"><button><p>UI-source-label</p></button></span>';

  await t.test("observed inline citation preserves strict JSON without editing the live DOM", async () => {
    await reset();
    const node = page.locator('[data-message-id="fixture-reply"]');
    const before = await node.innerHTML();
    const dirty = await node.innerText();
    assert.throws(() => JSON.parse(dirty), SyntaxError);
    assert.equal(Object.keys(JSON.parse(await node.textContent())).length, 1);
    const selected = await read();
    const result = JSON.parse(selected.selected.text);
    assert.equal(result.Q1.answer, "A complete source-grounded answer.");
    assert.equal(result.Q1.reason.trim(), "The policy has inclusive bounds.");
    assert.equal(selected.responseComplete, true);
    assert.equal(selected.generating, false);
    assert.equal(selected.responseTextEvidence.structuredFileCitationExtraction, true);
    assert.equal(selected.responseTextEvidence.fileCitationGroupCount, 1);
    assert.equal(await node.innerHTML(), before);
  });

  await t.test("multiple citation groups cannot win by making the UI candidate longer", async () => {
    await reset();
    await setBody(`<p>{"reason":"One source. ${citation} Another source. ${citation}"}</p>`);
    const result = await read();
    assert.equal(JSON.parse(result.selected.text).reason, "One source. Another source. ");
    assert.equal(result.responseTextEvidence.fileCitationGroupCount, 2);
    assert.doesNotMatch(result.selected.text, /UI-source-label/);
  });
  await t.test("literal filenames and authored source-code classes are not citation widgets", async () => {
    await reset();
    await setBody(`<p>Keep <code class="source-code">example-registration.pdf</code> exactly. ${citation}</p>`);
    assert.equal((await read()).selected.text, "Keep example-registration.pdf exactly.");
  });
  await t.test("paragraphs, headings, lists and table cells keep readable boundaries", async () => {
    await reset();
    await setBody(`<h2>Findings</h2><p>First fact.${citation}</p><p>Second fact.</p><ul><li>Alpha.</li><li>Beta.</li></ul><table><tr><th>Name</th><th>Value</th></tr><tr><td>Alder</td><td>12</td></tr></table>`);
    const value = (await read()).selected.text;
    for (const line of ["Findings", "First fact.", "Second fact.", "Alpha.", "Beta.", "Name Value", "Alder 12"]) {
      assert.ok(value.split("\n").includes(line), value);
    }
  });
  await t.test("BR and preformatted authored line breaks survive without double insertion", async () => {
    await reset();
    await setBody(`<p>Alpha.<br>Beta.<br>\nGamma.${citation}</p><pre><code>one\ntwo</code></pre>`);
    const value = (await read()).selected.text;
    assert.match(value, /Alpha\.\nBeta\.\nGamma\./);
    assert.match(value, /one\ntwo/);
  });
  await t.test("authored invalid JSON is not repaired or silently accepted as structured output", async () => {
    await reset();
    await setBody(`<p>{"reason":"An actual<br>line break.${citation}"}</p>`);
    const invalid = (await read()).selected.text;
    assert.throws(() => JSON.parse(invalid), SyntaxError);
  });
  await t.test("ordinary replies retain the existing two-candidate fallback", async () => {
    await reset();
    await setBody('<p>First complete fact.</p><p>Second complete fact.</p>');
    const result = await read();
    assert.equal(result.responseCandidates.length, 2);
    assert.equal(result.responseTextEvidence.structuredFileCitationExtraction, false);
    assert.match(result.selected.text, /First complete fact\./);
    assert.match(result.selected.text, /Second complete fact\./);
  });
  await t.test("removing citation UI does not manufacture completion", async () => {
    await reset();
    await page.locator('button[aria-label="Copy response"]').evaluate(node => node.remove());
    assert.equal((await read()).responseComplete, false);
  });
  await t.test("visible generation still blocks completion acceptance", async () => {
    await reset();
    await page.locator("article").evaluate(node => node.setAttribute("aria-busy", "true"));
    assert.equal((await read()).generating, true);
  });
  await t.test("unmounted submitted identity and a concurrent newer user cannot recover an older answer", async () => {
    await reset();
    await page.locator('[data-message-id="fixture-user"]').evaluate(node => node.remove());
    assert.equal((await read()).hasNewResponse, false);
    await reset();
    await page.locator("article").evaluate(node => node.insertAdjacentHTML('afterend', '<div data-message-author-role="user" data-message-id="newer">Another question</div>'));
    assert.match((await read()).error, /newer user message/);
  });
});
