import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { clearTimeout } from "node:timers";
import test from "node:test";
import vm from "node:vm";
import { chromium } from "playwright";

const agentRoot = new URL("../../../", import.meta.url);
const worker = fs.readFileSync(new URL("chrome_extension/service-worker.js", agentRoot), "utf8");
const helpers = { self: {} };
vm.runInNewContext(fs.readFileSync(new URL("chrome_extension/chatgpt-text.js", agentRoot), "utf8"), helpers);
const start = worker.indexOf("async function maybeWakeChatGptRendering");
const implementation = worker.slice(start, worker.indexOf("// ChatGPT can briefly pause", start));

// Playwright enables focus emulation automatically. Use its bundled executable,
// but a fresh native CDP pipe, so a hidden renderer is a real test precondition.
async function nativeBrowser(t) {
  const tempRoot = path.resolve(os.tmpdir());
  const profile = fs.mkdtempSync(path.join(tempRoot, "wca-render-fixture-"));
  const child = spawn(chromium.executablePath(), [
    "--headless=new", "--remote-debugging-pipe", "--no-startup-window",
    "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
    "--disable-component-update", "--disable-sync", `--user-data-dir=${profile}`,
  ], { stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"], windowsHide: true });
  let sequence = 0;
  let buffer = "";
  const pending = new Map();
  const events = [];
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++sequence;
    const timeout = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 5000);
    pending.set(id, { resolve, reject, timeout });
    child.stdio[3].write(`${JSON.stringify({ id, method, params, sessionId })}\0`);
  });
  child.stdio[4].setEncoding("utf8");
  child.stdio[4].on("data", chunk => {
    buffer += chunk;
    let boundary;
    while ((boundary = buffer.indexOf("\0")) >= 0) {
      const message = JSON.parse(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 1);
      const item = pending.get(message.id);
      if (item) {
        clearTimeout(item.timeout); pending.delete(message.id);
        if (message.error) item.reject(new Error(message.error.message));
        else item.resolve(message.result);
      } else events.forEach(listener => listener(message));
    }
  });
  const exited = new Promise(resolve => child.once("exit", resolve));
  t.after(async () => {
    await send("Browser.close").catch(() => child.kill());
    await exited;
    const target = path.resolve(profile);
    assert.equal(path.dirname(target), tempRoot);
    assert.ok(path.basename(target).startsWith("wca-render-fixture-"));
    fs.rmSync(target, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  });
  const { browserContextId } = await send("Target.createBrowserContext");
  const createPage = async url => {
    const { targetId } = await send("Target.createTarget", { url: "about:blank", browserContextId, background: true });
    const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
    events.push(message => {
      if (message.sessionId === sessionId && message.method === "Fetch.requestPaused") {
        void send("Fetch.fulfillRequest", { requestId: message.params.requestId,
          responseCode: 200, responseHeaders: [{ name: "Content-Type", value: "text/html" }],
          body: Buffer.from('<main id="answer">Prefix</main>').toString("base64"),
        }, sessionId).catch(() => {});
      }
    });
    await send("Fetch.enable", { patterns: [{ urlPattern: "*" }] }, sessionId);
    await send("Page.navigate", { url }, sessionId);
    const evaluate = async expression => {
      const result = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }, sessionId);
      assert.equal(result.exceptionDetails, undefined);
      return result.result.value;
    };
    // Wait on a page event rather than a fixed sleep.
    await evaluate("new Promise(resolve => document.readyState === 'complete' ? resolve() : addEventListener('load', () => resolve(), { once: true }))");
    return { targetId, sessionId, evaluate, bringToFront: () => send("Page.bringToFront", {}, sessionId) };
  };
  return { send, createPage };
}

test("production rendering lease keeps native Chromium visible after activation has no effect", { timeout: 20000 }, async t => {
  const browser = await nativeBrowser(t);
  const url = "https://chatgpt.com/c/offline-render-lease";
  const page = await browser.createPage(url);
  const other = await browser.createPage("https://chatgpt.com/c/offline-other");
  await other.bringToFront();
  const state = () => page.evaluate("({ visibility: document.visibilityState, focused: document.hasFocus() })");
  const before = await state();
  assert.equal(before.visibility, "hidden");
  await page.evaluate("window.rendered = new Promise(resolve => requestAnimationFrame(() => { document.querySelector('#answer').textContent = 'Complete review.'; resolve(); })); true");
  await other.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))");
  assert.equal(await page.evaluate("document.querySelector('#answer').textContent"), "Prefix");
  let session;
  let attaches = 0;
  let detaches = 0;
  let activations = 0;
  const storage = {};
  const sandbox = {
    Date: { now: () => 9000 }, ChatGptText: helpers.self.ChatGptText,
    assertChatGptDestination: async (_tab, expected) => { assert.equal(await page.evaluate("location.href"), expected); return expected; },
    execute: async (_tab, fn, args = []) => page.evaluate(`(${fn.toString()})(...${JSON.stringify(args)})`),
    requestChatGpt: async route => { assert.equal(route, "/event"); },
    chrome: {
      storage: { session: { get: async () => storage, set: async value => Object.assign(storage, value) } },
      tabs: {
        get: async () => ({ id: 1 }),
        // Models the observed unsuccessful native activation, not successful wake.
        update: async () => { activations++; },
      },
      debugger: {
        attach: async target => {
          assert.equal(target.tabId, 1); attaches++;
          session = (await browser.send("Target.attachToTarget", { targetId: page.targetId, flatten: true })).sessionId;
        },
        sendCommand: async (target, method, params) => {
          assert.equal(target.tabId, 1);
          assert.equal(method, "Emulation.setFocusEmulationEnabled");
          return browser.send(method, params, session);
        },
        detach: async () => { detaches++; await browser.send("Target.detachFromTarget", { sessionId: session }); },
      },
    },
  };
  vm.runInNewContext(implementation, sandbox);
  const watch = { text: "Prefix", unchangedSince: 0, attempted: false };
  t.after(() => sandbox.releaseChatGptRendering(watch));
  await sandbox.maybeWakeChatGptRendering(1, "offline-lease", {
    responseComplete: false, hasNewResponse: true, generating: false,
    completionEvidence: { composerIdle: true }, renderEvidence: before,
  }, "Prefix", watch, url);
  assert.deepEqual(await state(), { visibility: "visible", focused: true });
  await other.bringToFront();
  assert.deepEqual(await state(), { visibility: "visible", focused: true });
  // Exercise actual browser rendering while the other page has native focus.
  await page.evaluate("window.rendered");
  assert.equal(await page.evaluate("document.querySelector('#answer').textContent"), "Complete review.");
  await sandbox.releaseChatGptRendering(watch);
  assert.equal((await state()).visibility, "hidden");
  assert.equal(activations, 1);
  assert.equal(attaches, 1);
  assert.equal(detaches, 1);
});
