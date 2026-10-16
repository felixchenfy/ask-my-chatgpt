import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

// Offline only: fresh browser context, every request intercepted, no user profile.
const agentRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const worker = fs.readFileSync(path.join(agentRoot, "chrome_extension/service-worker.js"), "utf8");
const block = (start, end) => worker.slice(worker.indexOf(start), worker.indexOf(end));
const inspect = block("function inspectChatGptSubmission", "function inspectChatGptResponse");
const inspectResponse = block("function inspectChatGptResponse", "function summarizeChatGptObservation");
const submit = block("async function submitChatGptForm", "async function trustedAttachFile");
const fixture = fs.readFileSync(path.join(agentRoot, "tests/fixtures/chatgpt-pdf-user-turn.html"), "utf8");
const url = "https://chatgpt.com/c/fixture-test";
const question = "Read bridge-unicode-10000-regression.pdf and return every line.";

test("production Companion against observed PDF-turn DOM (no ChatGPT traffic)", async (t) => {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const context = await browser.newContext({ serviceWorkers: "block" });
  await context.route("**/*", route => route.fulfill({ contentType: "text/html", body: fixture }));
  const page = await context.newPage();
  const reset = async (pdf = true) => {
    await page.goto(url);
    for (const name of ["chatgpt-text", "chatgpt-tabs", "chatgpt-submission"]) {
      await page.addScriptTag({ content: fs.readFileSync(path.join(agentRoot, `chrome_extension/${name}.js`), "utf8") });
    }
    await page.addScriptTag({ content: inspect });
    await page.addScriptTag({ content: inspectResponse });
    await page.evaluate(([text, pdf]) => {
      const next = document.querySelector('[data-message-id="pdf-user"]');
      if (!pdf) next.querySelector('[role="group"]').remove();
      next.remove();
      globalThis.ChatGptSubmission.begin("fixture", text, pdf ? "bridge-unicode-10000-regression.pdf" : "", globalThis.location.href);
      document.querySelector('[data-message-id="old-user"]').after(next);
    }, [question, pdf]);
  };
  const observe = (before = 1, prompt = question, destination = url) => page.evaluate(
    ([count, text, target]) => {
      if (count === 2) globalThis.ChatGptSubmission.begin("historical", text, "", target);
      return globalThis.inspectChatGptSubmission(count === 2 ? "historical" : "fixture", text, target);
    },
    [before, prompt, destination],
  );

  await t.test("PDF tile and Show more cause the legacy false negative; body-only check succeeds", async () => {
    await reset();
    const legacy = await page.locator('[data-message-id="pdf-user"]').innerText();
    assert.match(legacy, /PDF/);
    assert.match(legacy, /Show more/);
    assert.notEqual(legacy.trim(), question);
    assert.equal((await observe()).submitted, true);
    assert.equal((await observe()).messageId, "pdf-user");
    assert.equal((await observe(2)).submitted, false); // Historical repeated prompt.
    assert.equal((await observe(1, "Different question")).submitted, false);
    assert.equal((await observe(1, question, "https://chatgpt.com/c/wrong")).destinationMatches, false);
  });
  await t.test("collapsed text uses complete textContent, not only visible lines", async () => {
    await reset();
    await page.locator('[data-testid="collapsible-user-message-content"]').evaluate(node => { node.style.display = "none"; });
    assert.equal((await observe()).submitted, true);
  });
  await t.test("text-only older markup remains valid", async () => {
    await reset(false);
    await page.locator('[data-message-id="pdf-user"]').evaluate((node, text) => {
      node.innerHTML = '<div class="whitespace-pre-wrap"></div>';
      node.firstChild.textContent = text;
    }, question);
    assert.equal((await observe()).submitted, true);
  });
  await t.test("additional prompt content, multiple bodies and unknown markup fail closed", async () => {
    for (const variant of ["prefix", "suffix", "ambiguous", "unknown"]) {
      await reset();
      await page.locator('[data-message-id="pdf-user"]').evaluate((node, mode) => {
        const body = node.querySelector('[data-testid="collapsible-user-message-content"]');
        if (mode === "prefix") body.prepend("unexpected prefix ");
        if (mode === "suffix") body.append(" unexpected suffix");
        if (mode === "ambiguous") node.append(body.cloneNode(true));
        if (mode === "unknown") { body.removeAttribute("data-testid"); body.firstElementChild.className = "new-markup"; }
      }, variant);
      assert.equal((await observe()).submitted, false, variant);
    }
  });
  await t.test("missing ID and concurrent user message cannot confirm this submission", async () => {
    await reset();
    await page.locator('[data-message-id="pdf-user"]').evaluate(node => node.removeAttribute("data-message-id"));
    assert.equal((await observe()).submitted, false);
    await reset();
    await page.locator('[data-message-id="pdf-user"]').evaluate(node => node.after(node.cloneNode(true)));
    assert.equal((await observe()).submitted, false);
  });
  await t.test("guard runs inside the actual submission expression before requestSubmit", async () => {
    await reset();
    await page.evaluate(() => {
      globalThis.submits = 0;
      document.querySelector("form").addEventListener("submit", event => { event.preventDefault(); globalThis.submits++; });
    });
    const helperContext = { self: {}, URL };
    vm.runInNewContext(fs.readFileSync(path.join(agentRoot, "chrome_extension/chatgpt-tabs.js"), "utf8"), helperContext);
    vm.runInNewContext(fs.readFileSync(path.join(agentRoot, "chrome_extension/chatgpt-text.js"), "utf8"), helperContext);
    const sandbox = {
      ChatGptTabs: helperContext.self.ChatGptTabs,
      ChatGptText: helperContext.self.ChatGptText,
      chrome: { debugger: {
        attach: async () => {}, detach: async () => {},
        sendCommand: async (_target, method, params) => {
          assert.equal(method, "Runtime.evaluate");
          return { result: { value: await page.evaluate(params.expression) } };
        },
      } },
    };
    vm.runInNewContext(submit, sandbox);
    await assert.rejects(sandbox.submitChatGptForm(1, "https://chatgpt.com/c/other"), /conversation_mismatch/);
    assert.equal(await page.evaluate(() => globalThis.submits), 0);
    await sandbox.submitChatGptForm(1, url);
    assert.equal(await page.evaluate(() => globalThis.submits), 1);
  });
  for (const provisional of ["https://chatgpt.com/c/WEB:00c508cd-5561-4478-9a09-b339329f5946", "https://chatgpt.com/c/local-chatgpt%3A00000000-0000-4000-8000-000000000001"]) await t.test(`new PDF submission follows provisional to canonical: ${provisional}`, async () => {
    await reset();
    await page.evaluate(target => globalThis.history.replaceState({}, "", target), provisional);
    const submission = await observe(1, question, "https://chatgpt.com/");
    assert.equal(submission.submitted, true);
    assert.equal(submission.messageId, "pdf-user");
    await page.evaluate(() => {
      const turn = document.createElement("article");
      turn.innerHTML = '<div data-message-author-role="assistant" data-message-id="reply">Full review.</div><button aria-label="Copy response">Copy</button>';
      document.querySelector('[data-message-id="pdf-user"]').after(turn);
    });
    const read = (target, id = "pdf-user") => page.evaluate(
      ([expected, messageId]) => globalThis.inspectChatGptResponse(0, false, 1, "", messageId, expected), [target, id],
    );
    const pending = await read(provisional);
    assert.equal(pending.hasNewResponse, true);
    assert.equal(pending.conversationUrlCanonical, false);
    await page.evaluate(target => globalThis.history.replaceState({}, "", target), url);
    const missing = await read(provisional, "wrong-user");
    assert.equal(missing.awaitingSubmittedMessage, true);
    assert.equal(missing.conversationUrlCanonical, false);
    assert.equal(missing.hasNewResponse, false);
    const resolved = await read(provisional);
    assert.equal(resolved.submittedUserPresent, true);
    assert.equal(resolved.conversationUrlCanonical, true);
    assert.equal(resolved.conversationUrl, url);
    await page.evaluate(() => globalThis.history.replaceState({}, "", "/c/wrong-conversation"));
    assert.match((await read(resolved.conversationUrl)).error, /conversation_mismatch/);
  });
});
