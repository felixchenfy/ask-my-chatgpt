// Real Python JSON/HTTP -> production worker -> actual injected helpers -> DOM.
// Only Chrome extension APIs are adapted to an isolated Playwright/CDP context.
import assert from "node:assert/strict";
import fs from "node:fs";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import test from "node:test";
import vm from "node:vm";
import { chromium } from "playwright";

const agent = new URL("../../../", import.meta.url);
const source = fs.readFileSync(new URL("chrome_extension/service-worker.js", agent), "utf8");
const url = "https://chatgpt.com/c/offline-contract";
const answer = "Actions\n- Keep state\n- Test retries\n- Add integration tests covering the real browser workflow";
function block(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a + start.length);
  assert.ok(a >= 0 && b > a);
  return source.slice(a, b);
}
async function pageFixture(t) {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const context = await browser.newContext({ serviceWorkers: "block" });
  await context.route("**/*", route => route.fulfill({ contentType: "text/html", body: `
    <main><div data-message-author-role="user" data-message-id="old"><div class="whitespace-pre-wrap">History</div></div></main>
    <form><textarea id="prompt-textarea"></textarea><button data-testid="send-button">Send</button></form>
    <button aria-label="Start dictation">Voice</button>` }));
  const page = await context.newPage();
  await page.goto(url);
  return { context, page };
}

for (const scenario of ["completed-list", "accessible-units", "accessible-inline-code", "accessible-composer", "accessible-draft", "accessible-resume", "accessible-resume-send", "accessible-resume-edited", "accessible-pdf-upload", "accessible-pdf-resume", "accessible-pdf-resume-pending", "accessible-pdf-resume-restored", "accessible-pdf-resume-restored-edited", "accessible-pdf-resume-edited", "accessible-pdf-resume-wrong-tab", "existing-draft", "response-route-mismatch", "project-redirect", "project-other-chat", "inline-code", "thinking-slider", "thinking-pill", "thinking-preference"]) {
  test(`real bridge boundaries: ${scenario}`, async t => {
    const { context, page } = await pageFixture(t);
    await page.evaluate(({ answer, scenario }) => {
      globalThis.sends = 0;
      let textarea = document.querySelector("textarea");
      if (scenario.startsWith("accessible-")) {
        const send = document.querySelector('[data-testid="send-button"]');
        send.removeAttribute("data-testid"); send.setAttribute("aria-label", "Send"); send.type = "submit";
        const editable = document.createElement("div");
        editable.contentEditable = "true"; editable.setAttribute("role", "textbox");
        editable.value = ""; // New editor's value is not its visible content.
        editable.setAttribute("aria-label", "Ask ChatGPT"); editable.setAttribute("aria-multiline", "true");
        editable.style.cssText = "min-height:60px;width:500px";
        textarea.replaceWith(editable); textarea = editable;
        if (["accessible-units", "accessible-inline-code"].includes(scenario) || scenario.startsWith("accessible-pdf-")) {
          const history = document.querySelector("main");
          history.innerHTML = '<div data-turn-key="33333333-3333-4333-8333-333333333333"><div data-chatgpt-search-unit-key="fallback-turn-0:0:user" data-chatgpt-search-message-ids="33333333-3333-4333-8333-333333333333"><div data-markdown-text-tone="user-message">History</div></div></div>';
        }
        if (scenario === "accessible-draft") editable.textContent = "Unrelated user draft";
        if (scenario.startsWith("accessible-resume")) editable.textContent = scenario.endsWith("edited")
          ? "Changed user draft" : "Review this exact question.";
        if (scenario.startsWith("accessible-pdf-resume")) {
          document.querySelector("form").insertAdjacentHTML("afterbegin", `<span class="group/composer-attachment" id="queued-pdf">
            <span class="composer-attachment-surface"><span class="truncate">review.pdf</span><button type="button" class="composer-attachment-surface" aria-label="review.pdf"></button></span>
            <button type="button" aria-label="Remove review.pdf"></button></span>`);
          if (scenario.endsWith("edited")) editable.textContent = "Changed user draft";
        }
        if (scenario === "accessible-pdf-upload") {
          const fileInput = document.createElement("input"); fileInput.type = "file";
          fileInput.addEventListener("change", () => {
            document.querySelector("form").insertAdjacentHTML("afterbegin", '<span class="group/composer-attachment" id="queued-pdf"><span role="progressbar">Uploading</span></span>');
          });
          document.querySelector("form").append(fileInput);
        }
      }
      if (scenario.startsWith("thinking-")) {
        document.querySelector("form").insertAdjacentHTML("beforeend", scenario === "thinking-pill"
          ? '<button type="button" class="__composer-pill" aria-haspopup="menu">Medium</button>'
          : `<button type="button" id="thinking-trigger" aria-haspopup="menu" aria-controls="thinking-menu">Thinking effort</button>
             <div id="thinking-menu" role="menu" aria-labelledby="thinking-trigger">
               <div data-testid="composer-intelligence-picker-content">
                 <div role="menuitem" aria-label="Power" aria-describedby="power-value power-help">
                   <div data-model-reasoning-effort-slider><div data-max="true">
                     <span role="slider" aria-valuemin="0" aria-valuemax="4" aria-valuenow="4" aria-hidden="true"></span>
                   </div></div>
                 </div>
                 <span id="power-value">Pro, 5 of 5.</span><span id="power-help">Use Left and Right arrow keys to adjust power.</span>
                 <div role="menuitemradio" aria-checked="true">Latest</div>
               </div>
             </div>`);
      }
      if (scenario === "existing-draft") textarea.value = "Unrelated user draft";
      document.querySelector("form").addEventListener("submit", event => {
        event.preventDefault(); globalThis.sends++;
        const user = document.createElement("div");
        user.dataset.messageAuthorRole = "user"; user.dataset.messageId = "new-user";
        const body = document.createElement("div"); body.className = "whitespace-pre-wrap";
        body.textContent = textarea.tagName === "TEXTAREA" ? textarea.value : textarea.innerText;
        if (["inline-code", "accessible-inline-code"].includes(scenario)) {
          body.replaceChildren();
          const parts = (textarea.tagName === "TEXTAREA" ? textarea.value : textarea.innerText).split(/(`[^`]+`)/);
          for (const part of parts) {
            if (part.startsWith("`")) {
              const code = document.createElement("code");
              if (scenario === "inline-code") code.className = "user-message-inline-code";
              code.textContent = part.slice(1, -1); body.append(code);
            } else body.append(document.createTextNode(part));
          }
        }
        user.append(body); document.querySelector("main").append(user);
        if (textarea.tagName === "TEXTAREA") textarea.value = "";
        else textarea.textContent = "";
        const article = document.createElement("article");
        const response = document.createElement("div");
        response.dataset.messageAuthorRole = "assistant"; response.dataset.messageId = "new-answer";
        response.style.whiteSpace = "pre-wrap"; response.textContent = answer;
        const copy = document.createElement("button"); copy.setAttribute("aria-label", "Copy response");
        article.append(response, copy); document.querySelector("main").append(article);
        if (["accessible-units", "accessible-inline-code"].includes(scenario) || scenario.startsWith("accessible-pdf-")) {
          const userId = "11111111-1111-4111-8111-111111111111";
          const replyId = "22222222-2222-4222-8222-222222222222";
          user.removeAttribute("data-message-author-role"); user.removeAttribute("data-message-id");
          user.setAttribute("data-chatgpt-search-unit-key", "fallback-turn-1:0:user");
          user.setAttribute("data-chatgpt-search-message-ids", userId);
          if (scenario === "accessible-inline-code") body.className = "text-size-chat whitespace-pre-wrap";
          else { body.className = ""; body.setAttribute("data-markdown-text-tone", "user-message"); }
          response.removeAttribute("data-message-author-role"); response.removeAttribute("data-message-id");
          response.setAttribute("data-chatgpt-search-unit-key", "fallback-turn-1:2:assistant");
          response.setAttribute("data-chatgpt-search-message-ids", `${replyId} ${replyId}`);
          const turn = document.createElement("div"); turn.setAttribute("data-turn-key", userId);
          copy.setAttribute("aria-label", "Copy");
          turn.append(user, response, copy); article.replaceWith(turn);
        }
        if (scenario.startsWith("accessible-pdf-")) {
          // Observed October 4 submitted resource card, in the SAME user unit.
          user.insertAdjacentHTML("beforeend",'<span class="group/resource-card relative"><button type="button" class="peer/resource-card" aria-label="review.pdf" aria-busy="false"></button><span><span class="truncate font-semibold">review.pdf</span><span class="truncate">PDF</span></span></span>');
          document.querySelector("#queued-pdf").remove();
        }
      });
    }, { answer, scenario });
    const child = spawn(fileURLToPath(new URL(".venv/Scripts/python.exe", agent)), [
      fileURLToPath(new URL("fixtures/bridge-http-fixture.py", import.meta.url)),
      fileURLToPath(new URL("src/", agent)),
      scenario,
    ], { windowsHide: true, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, PYTHONUTF8: "1" } });
    t.after(() => child.kill());
    let stderr = ""; child.stderr.on("data", chunk => { stderr += chunk; });
    const lines = createInterface({ input: child.stdout })[Symbol.asyncIterator]();
    const first = await lines.next(); assert.equal(first.done, false, stderr);
    const { port, command } = JSON.parse(first.value);
    assert.equal(command.attachment_filename, scenario.startsWith("accessible-pdf-") ? "review.pdf" : null);
    const cdp = await context.newCDPSession(page);
    let insertions = 0;
    let uploads = 0;
    let pendingUploadObserved = false;
    const storage = {};
    let now = Date.now();
    const scope = {
      self: {}, URL, CHATGPT_BRIDGE: `http://127.0.0.1:${port}`, CHATGPT_TOKEN: "offline-fixture-token",
      Date: { now: () => now }, setTimeout: (callback, delay) => { now += delay; globalThis.queueMicrotask(callback); return 0; }, clearTimeout() {},
      fetch: async (...args) => {
        const response = await globalThis.fetch(...args);
        if (scenario === "accessible-pdf-upload" && String(args[0]).endsWith("/observation")) {
          const state = JSON.parse(args[1].body).last_observation;
          if (state?.phase === "attachment_staging" && !pendingUploadObserved) {
            assert.equal(state.attachment.state, "unexpected");
            assert.equal(state.attachment.observedFilename, "");
            assert.equal(state.composerDiagnostic.draftUploadInProgress, true);
            pendingUploadObserved = true;
            await page.evaluate(() => {
              document.querySelector("#queued-pdf").innerHTML = '<span class="composer-attachment-surface"><span class="truncate">review.pdf</span><button type="button" class="composer-attachment-surface" aria-label="review.pdf"></button></span><button type="button" aria-label="Remove review.pdf"></button>';
            });
          }
        }
        if (scenario.startsWith("project-") && String(args[0]).endsWith("/observation")
          && JSON.parse(args[1].body).last_observation?.phase === "composer_ready") {
          const id = scenario === "project-redirect" ? "offline-contract" : "other";
          await page.evaluate(id => globalThis.history.replaceState({}, "", `/g/g-p-example/c/${id}`), id);
        }
        if (scenario === "response-route-mismatch" && String(args[0]).endsWith("/submitted")) {
          await page.evaluate(() => globalThis.history.replaceState({}, "", "/c/other"));
        }
        return response;
      },
      chrome: {
        storage: { session: { get: async key => ({ [key]: storage[key] }), set: async value => Object.assign(storage, value) } },
        tabs: { query: async () => [{ id: 1, url, active: true }], get: async () => ({ id: 1, url: page.url(), status: "complete" }), update: async () => ({ id: 1 }) },
        scripting: { executeScript: async ({ func, args = [], files }) => {
          if (files) { for (const file of files) await page.addScriptTag({ content: fs.readFileSync(new URL(`chrome_extension/${file}`, agent), "utf8") }); return [{}]; }
          return [{ result: await page.evaluate(({ code, args }) => (0, eval)(`(${code})`)(...args), { code: func.toString(), args }) }];
        } },
        debugger: { attach: async () => {}, detach: async () => {}, sendCommand: async (_target, method, params) => {
          if (method === "Input.insertText") insertions++;
          if (method === "DOM.setFileInputFiles") uploads++;
          return cdp.send(method, params);
        } },
      },
    };
    vm.createContext(scope);
    for (const name of ["chatgpt-text", "chatgpt-tabs"]) vm.runInContext(fs.readFileSync(new URL(`chrome_extension/${name}.js`, agent), "utf8"), scope);
    Object.assign(scope, { ChatGptText: scope.self.ChatGptText, ChatGptTabs: scope.self.ChatGptTabs });
    vm.runInContext(block("async function requestChatGpt(", "async function event("), scope);
    vm.runInContext(block("async function waitForTab(", "function pageGuard("), scope);
    await scope.executeChatGpt(command);
    const final = await lines.next(); assert.equal(final.done, false, stderr);
    const result = JSON.parse(final.value);
    const sends = await page.evaluate(() => globalThis.sends);
    if (scenario.startsWith("accessible-resume")) assert.equal(insertions, 0);
    if (scenario.startsWith("accessible-pdf-resume")) {
      assert.ok(command.preserved_queued_attachment);
      assert.equal(uploads,0);
      assert.equal(insertions,["accessible-pdf-resume", "accessible-pdf-resume-pending", "accessible-pdf-resume-restored"].includes(scenario) ? 1 : 0);
    }
    if (scenario === "accessible-pdf-upload") {
      assert.equal(pendingUploadObserved, true); assert.equal(uploads, 1); assert.equal(insertions, 1);
    }
    if (["completed-list", "accessible-units", "accessible-inline-code", "accessible-pdf-upload", "accessible-pdf-resume", "accessible-pdf-resume-pending", "accessible-pdf-resume-restored", "accessible-composer", "accessible-resume", "accessible-resume-send", "project-redirect", "inline-code", "thinking-preference"].includes(scenario)) {
      assert.equal(result.ok, true, JSON.stringify(result));
      assert.equal(result.response_text, answer);
      assert.equal(result.submitted_message_id, ["accessible-units", "accessible-inline-code"].includes(scenario) || scenario.startsWith("accessible-pdf-") ? "11111111-1111-4111-8111-111111111111" : "new-user"); assert.equal(sends, 1);
      assert.equal(result.conversation_url, url);
      if (scenario === "thinking-preference") {
        assert.equal(result.thinking_mode_requested, "prefer_max");
        assert.equal(result.thinking_mode, "current");
        assert.equal(result.thinking_mode_verified, false);
        assert.equal(result.thinking_mode_selection_attempted, false);
        assert.match(result.thinking_mode_fallback_reason, /did not expose/);
        assert.equal(await page.locator('[role="slider"]').getAttribute("aria-valuenow"), "4");
      }
    } else if (scenario.startsWith("thinking-")) {
      assert.equal(result.ok, false); assert.equal(sends, 0);
      assert.equal(result.zero_submission_proven, true);
      assert.equal(result.thinking_mode_selection_attempted, false);
      assert.equal(result.thinking_mode_verified, false);
      assert.match(result.thinking_mode_fallback_reason, /did not expose/);
      assert.equal(result.last_observation.phase, "thinking_mode_validation_failed");
      const evidence = result.last_observation.thinkingEvidence;
      if (scenario === "thinking-slider") {
        assert.equal(evidence.unsupportedReason, "unsupported_thinking_effort_slider");
        assert.equal(evidence.sliderValue, "4");
        assert.equal(evidence.sliderDescription, "Pro, 5 of 5. | Use Left and Right arrow keys to adjust power.");
      } else {
        assert.equal(evidence.observedComposerLabels, "Medium");
        assert.equal(evidence.triggerCount, 0);
      }
      assert.equal(await page.locator("textarea").inputValue(), "");
    } else if (scenario === "project-other-chat") {
      assert.equal(result.ok, false); assert.match(result.error, /conversation_mismatch/);
      assert.equal(sends, 0); assert.equal(result.zero_submission_proven, true);
      assert.equal(await page.locator("textarea").inputValue(), "");
    } else if (scenario.startsWith("accessible-pdf-resume")) {
      assert.equal(result.ok,false); assert.equal(result.zero_submission_proven,true);
      assert.match(result.error,/draft_conflict/); assert.equal(sends,0);
      assert.equal(await page.locator("#queued-pdf").count(),1);
      assert.equal(await page.locator('[contenteditable="true"]').innerText(),scenario.endsWith("edited") ? "Changed user draft" : "");
    } else if (scenario === "existing-draft" || scenario === "accessible-draft" || scenario === "accessible-resume-edited") {
      assert.match(result.error, /draft_conflict/); assert.equal(sends, 0);
      assert.equal(result.zero_submission_proven, true);
      assert.equal(await page.locator('textarea, [contenteditable="true"]').evaluate(node => node.tagName === "TEXTAREA" ? node.value : node.innerText), scenario === "accessible-resume-edited" ? "Changed user draft" : "Unrelated user draft");
    } else {
      assert.equal(sends, 1); assert.equal(result.ok, false);
      assert.equal(result.last_observation.destinationEvidence.observed, "https://chatgpt.com/c/other");
      assert.equal(result.last_observation.inspectionPhase, "inspectChatGptResponse");
    }
  });
}

test("thinking verification uses only the uniquely linked control and popup", async t => {
  const { page } = await pageFixture(t);
  await page.addScriptTag({ content: fs.readFileSync(new URL("chrome_extension/chatgpt-text.js", agent), "utf8") });
  await page.addScriptTag({ content: block("function inspectChatGptThinkingMode(", "async function ensureChatGptThinkingMode(") });
  await page.evaluate(() => {
    const unrelated = document.createElement("button"); unrelated.textContent = "Max";
    unrelated.setAttribute("aria-selected", "true"); document.body.append(unrelated);
  });
  assert.equal(await page.evaluate(() => globalThis.inspectChatGptThinkingMode("max").verified), false);
  await page.evaluate(() => document.body.insertAdjacentHTML("beforeend", `
    <button aria-label="Thinking effort" aria-haspopup="menu" aria-controls="effort-menu">Standard</button>
    <div id="effort-menu" role="menu"><button role="menuitemradio" aria-checked="true">Max</button></div>`));
  assert.equal(await page.evaluate(() => globalThis.inspectChatGptThinkingMode("max").verified), true);
  await page.evaluate(() => document.querySelector('[aria-controls]').setAttribute("aria-controls", "absent"));
  const disconnected = await page.evaluate(() => globalThis.inspectChatGptThinkingMode("max"));
  assert.equal(disconnected.verified, false); assert.equal(disconnected.maxOptions.length, 0);
});
