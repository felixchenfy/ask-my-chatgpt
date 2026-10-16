// Reduced September 24 DOM contract. Synthetic text/IDs, isolated offline page.
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { chromium } from "playwright";

const agent = new URL("../../../", import.meta.url);
const worker = fs.readFileSync(new URL("chrome_extension/service-worker.js", agent), "utf8");
const inspect = worker.slice(worker.indexOf("function inspectChatGptResponse"), worker.indexOf("function summarizeChatGptObservation"));
const url = "https://chatgpt.com/c/offline-search-unit";
const userId = "11111111-1111-4111-8111-111111111111";
const replyId = "22222222-2222-4222-8222-222222222222";
const otherId = "33333333-3333-4333-8333-333333333333";
const question = "Review this article:\nhttps://example.test/article\nKeep the evidence.";
const html = `<main><div data-turn-key="${userId}">
  <div data-chatgpt-search-unit-key="fallback-turn-0:0:user" data-chatgpt-search-message-ids="${userId}">
    <div data-markdown-text-tone="user-message"><p>Review this article:</p><p><span>https://example.test/article</span></p><p>Keep the evidence.</p></div>
    <button aria-label="Copy message">Copy message</button>
  </div>
  <div data-markdown-text-tone="primary">Intermediate research UI</div>
  <div data-markdown-text-tone="tertiary">Intermediate status</div>
  <div data-chatgpt-search-unit-key="fallback-turn-0:2:assistant" data-chatgpt-search-message-ids="${replyId} ${replyId}">
    <h2>Findings</h2><p>Explain the input.</p><table><tr><td>State</td><td>Verified</td></tr></table>
    <div data-markdown-copy="exclude">Plain text<button aria-label="Copy">Copy</button></div>
    <button aria-label="Copy table">Copy table</button>
  </div>
  <button aria-label="Copy" id="reply-copy">Copy</button>
</div></main>
<form><div contenteditable="true" role="textbox" aria-label="Ask ChatGPT" aria-multiline="true" style="min-height:40px"></div><button type="submit" aria-label="Send">Send</button></form>
<button aria-label="Start dictation">Voice</button>`;

test("new message units preserve identity, response ownership and completion", async t => {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const context = await browser.newContext({ serviceWorkers: "block" });
  await context.route("**/*", route => route.fulfill({ contentType: "text/html", body: html }));
  const page = await context.newPage();
  const reset = async () => {
    await page.goto(url);
    for (const name of ["chatgpt-text", "chatgpt-tabs", "chatgpt-submission"]) {
      await page.addScriptTag({ content: fs.readFileSync(new URL(`chrome_extension/${name}.js`, agent), "utf8") });
    }
    await page.addScriptTag({ content: inspect });
  };
  const read = () => page.evaluate(({ userId, url }) => {
    const state = globalThis.inspectChatGptResponse(0, true, 0, "", userId, url);
    return { ...state, selected: globalThis.ChatGptText.chooseBestResponse(state.responseCandidates || []).text };
  }, { userId, url });
  await t.test("full original prompt and deduplicated assistant UUID; no UI text", async () => {
    await reset();
    const before = await page.locator("main").innerHTML();
    const identity = await page.evaluate(() => {
      const text = globalThis.ChatGptText;
      return { question: text.normalizeComposerText(text.userPromptText(text.messageNodes("user")[0])),
        replyId: text.messageId(text.messageNodes("assistant")[0]) };
    });
    assert.equal(identity.question, question.replace(/\s+/g, " "));
    assert.equal(identity.replyId, replyId);
    const result = await read();
    assert.equal(result.submittedUserPresent, true);
    assert.equal(result.responseComplete, true);
    assert.equal(result.generating, false);
    assert.equal(result.completionEvidence.currentTurnCopyActionCount, 1);
    assert.equal(result.selected, "Findings\n\nExplain the input.\n\nState Verified");
    assert.doesNotMatch(result.selected, /Copy|Intermediate|Review this article/);
    assert.equal(await page.locator("main").innerHTML(), before);
  });
  for (const mode of ["missing-copy", "hidden-copy", "user-copy", "table-copy", "copy-before-answer", "foreign-copy"]) {
    await t.test(`${mode} is not final-answer completion`, async () => {
      await reset();
      await page.evaluate(mode => {
        const copy = document.querySelector("#reply-copy");
        if (mode === "missing-copy") copy.remove();
        if (mode === "hidden-copy") copy.style.display = "none";
        if (mode === "user-copy") document.querySelector('[data-chatgpt-search-unit-key$=":user"]').append(copy);
        if (mode === "table-copy") copy.setAttribute("aria-label", "Copy table");
        if (mode === "copy-before-answer") document.querySelector('[data-chatgpt-search-unit-key$=":assistant"]').before(copy);
        if (mode === "foreign-copy") document.body.append(copy);
      }, mode);
      assert.equal((await read()).responseComplete, false);
    });
  }
  for (const mode of ["multiple-ids", "missing-id", "wrong-turn", "duplicate-assistant", "duplicate-user", "wrong-role"]) {
    await t.test(`${mode} cannot bind a reply`, async () => {
      await reset();
      await page.evaluate(({ mode, otherId }) => {
        const assistant = document.querySelector('[data-chatgpt-search-unit-key$=":assistant"]');
        const user = document.querySelector('[data-chatgpt-search-unit-key$=":user"]');
        if (mode === "multiple-ids") assistant.setAttribute("data-chatgpt-search-message-ids", `${assistant.getAttribute("data-chatgpt-search-message-ids")} ${otherId}`);
        if (mode === "missing-id") assistant.removeAttribute("data-chatgpt-search-message-ids");
        if (mode === "wrong-turn") assistant.closest('[data-turn-key]').setAttribute("data-turn-key", otherId);
        if (mode === "duplicate-assistant") assistant.after(assistant.cloneNode(true));
        if (mode === "duplicate-user") user.after(user.cloneNode(true));
        if (mode === "wrong-role") assistant.setAttribute("data-chatgpt-search-unit-key", "fallback-turn-0:2:tool");
      }, { mode, otherId });
      const result = await read();
      assert.equal(result.responseComplete, false);
      assert.equal(result.selected, "");
    });
  }
  await t.test("Stop vetoes completion and a newer user rejects extraction", async () => {
    await reset();
    await page.evaluate(() => document.body.insertAdjacentHTML("beforeend", '<button aria-label="Stop">Stop</button>'));
    assert.equal((await read()).generating, true);
    await page.evaluate(otherId => {
      const newer = document.querySelector('[data-chatgpt-search-unit-key$=":user"]').cloneNode(true);
      newer.setAttribute("data-chatgpt-search-message-ids", otherId);
      document.querySelector("main").append(newer);
    }, otherId);
    assert.match((await read()).error, /newer user/);
  });
  await t.test("new user submission is witnessed and exactly compared in the same markup", async () => {
    await reset();
    const state = await page.evaluate(({ userId, question, url }) => {
      const turn = document.querySelector('[data-turn-key]');
      turn.remove();
      globalThis.history.replaceState({}, "", "/");
      globalThis.ChatGptSubmission.begin("offline", question, "", "https://chatgpt.com/");
      document.querySelector("main").append(turn);
      globalThis.history.replaceState({}, "", "/c/local-chatgpt%3A44444444-4444-4444-8444-444444444444");
      const submission = globalThis.ChatGptSubmission.inspect("offline", question, "https://chatgpt.com/");
      const mismatch = globalThis.ChatGptSubmission.inspect("offline", question.replace("evidence", "assumptions"), "https://chatgpt.com/");
      globalThis.history.replaceState({}, "", url);
      const response = globalThis.inspectChatGptResponse(0, true, 0, "", userId, submission.conversationUrl);
      globalThis.ChatGptSubmission.end("offline");
      return { submission, mismatch, response };
    }, { userId, question, url });
    assert.equal(state.submission.submitted, true);
    assert.equal(state.submission.messageId, userId);
    assert.equal(state.mismatch.submitted, false);
    assert.equal(state.response.responseComplete, true);
    assert.equal(state.response.conversationUrlCanonical, true);
  });
  await t.test("raw pre-wrap search body confirms the exact prompt and reads its own reply", async () => {
    await reset();
    const state = await page.evaluate(({ question, userId, url }) => {
      const turn = document.querySelector('[data-turn-key]');
      const body = turn.querySelector('[data-markdown-text-tone="user-message"]');
      body.removeAttribute("data-markdown-text-tone");
      body.className = "text-size-chat whitespace-pre-wrap";
      body.textContent = question;
      turn.remove();
      globalThis.history.replaceState({}, "", "/");
      globalThis.ChatGptSubmission.begin("pre-wrap", question, null, "https://chatgpt.com/");
      document.querySelector("main").append(turn);
      globalThis.history.replaceState({}, "", url);
      const submission = globalThis.ChatGptSubmission.inspect("pre-wrap", question, "https://chatgpt.com/");
      const mismatch = globalThis.ChatGptSubmission.inspect("pre-wrap", question.replace("evidence", "evidencX"), "https://chatgpt.com/");
      const response = globalThis.inspectChatGptResponse(0, true, 0, "", userId, url);
      const stop = document.createElement("button");
      stop.setAttribute("aria-label", "Stop"); document.body.append(stop);
      const generating = globalThis.inspectChatGptResponse(0, true, 0, "", userId, url).generating;
      globalThis.ChatGptSubmission.end("pre-wrap");
      return { submission, mismatch, response, generating };
    }, { question, userId, url });
    assert.equal(state.submission.submitted, true);
    assert.equal(state.submission.messageId, userId);
    assert.equal(state.submission.observedPromptLength, question.replace(/\s+/g, " ").length);
    assert.equal(state.mismatch.submitted, false);
    assert.equal(state.response.submittedUserPresent, true);
    assert.equal(state.response.responseComplete, true);
    assert.equal(state.response.completionEvidence.currentTurnCopyActionCount, 1);
    assert.doesNotMatch(state.response.responseCandidates.join("\n"), /Plain text|Copy/);
    assert.equal(state.generating, true);
  });
  for (const mode of ["duplicate-pre-wrap", "button-body", "attachment-body", "duplicate-marked"]) {
    await t.test(`ambiguous or control-scoped ${mode} is not a prompt`, async () => {
      await reset();
      const prompt = await page.evaluate(mode => {
        const user = globalThis.ChatGptText.messageNodes("user")[0];
        const body = user.querySelector('[data-markdown-text-tone="user-message"]');
        if (mode === "duplicate-marked") {
          body.className = "whitespace-pre-wrap";
          user.append(body.cloneNode(true));
        } else {
          body.removeAttribute("data-markdown-text-tone"); body.className = "whitespace-pre-wrap";
          if (mode === "duplicate-pre-wrap") user.append(body.cloneNode(true));
          else {
            const wrapper = document.createElement(mode === "button-body" ? "button" : "div");
            if (mode === "attachment-body") wrapper.setAttribute("role", "group");
            body.before(wrapper); wrapper.append(body);
          }
        }
        return globalThis.ChatGptText.userPromptText(user);
      }, mode);
      assert.equal(prompt, "");
    });
  }
});
