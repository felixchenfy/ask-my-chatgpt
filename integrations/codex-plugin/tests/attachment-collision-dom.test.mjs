// Observed live rename regression, using offline Chromium only.
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { chromium } from "playwright";

const agentRoot = new URL("../../../", import.meta.url);
const fixture = fs.readFileSync(new URL("tests/fixtures/chatgpt-renamed-pdf-user-turn.html", agentRoot), "utf8");
const url = "https://chatgpt.com/c/offline-attachment-collision";
const filename = "battle-hex-grid-registration.pdf";
const question = `Read the newly attached ${filename}.`;

test("collision-renamed PDF cards retain scoped identity and staging guards", async t => {
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const context = await browser.newContext({ serviceWorkers: "block" });
  await context.route("**/*", route => route.fulfill({ contentType: "text/html", body: fixture }));
  const page = await context.newPage();
  const mount = async ({ observed = "battle-hex-grid-registration(1).pdf", expected = filename, mode = "" } = {}) => {
    await page.goto(url);
    for (const name of ["chatgpt-text", "chatgpt-tabs", "chatgpt-submission"]) {
      await page.addScriptTag({ content: fs.readFileSync(new URL(`chrome_extension/${name}.js`, agentRoot), "utf8") });
    }
    await page.evaluate(({ question, observed, expected, mode }) => {
      const next = document.querySelector('[data-message-id="renamed-pdf-user"]');
      next.remove();
      const card = next.querySelector('[role="group"]');
      card.setAttribute("aria-label", observed);
      card.querySelector("button").setAttribute("aria-label", observed);
      card.querySelector(".font-semibold").textContent = observed;
      if (mode === "mismatched-text") card.querySelector(".font-semibold").textContent = "wrong.pdf";
      if (mode === "mismatched-button") card.querySelector("button").setAttribute("aria-label", "wrong.pdf");
      if (mode === "no-file-type") card.querySelector(".text-token-text-secondary").remove();
      if (mode === "hidden") card.style.display = "none";
      if (mode === "busy") card.setAttribute("aria-busy", "true");
      if (mode === "failed") { const status = document.createElement("span"); status.setAttribute("role", "alert"); status.textContent = "Upload failed"; card.append(status); }
      if (mode === "additional") card.after(card.cloneNode(true));
      if (mode === "wrong-prompt") next.querySelector('[data-testid="collapsible-user-message-content"]').textContent = "Another person's question.";
      if (mode === "missing-id") next.removeAttribute("data-message-id");
      document.querySelector("textarea").value = question;
      globalThis.ChatGptSubmission.begin("collision-fixture", question, expected, globalThis.location.href);
      if (mode === "composer") document.querySelector("form").append(card);
      else if (mode === "history") document.querySelector('[data-message-id="old-user"]').before(next);
      else document.querySelector('[data-message-id="old-user"]').after(next);
      document.querySelector("textarea").value = "";
    }, { question, observed, expected, mode });
  };
  const inspect = () => page.evaluate(question => globalThis.ChatGptSubmission.inspect(
    "collision-fixture", question, globalThis.location.href), question);

  for (const observed of [filename, "battle-hex-grid-registration(1).pdf", "battle-hex-grid-registration(27).pdf", "battle-hex-grid-registration (2).pdf"]) {
    await t.test(`verified new turn accepts ${observed}`, async () => {
      await mount({ observed });
      const result = await inspect();
      assert.equal(result.matches, true);
      assert.equal(result.submitted, true);
      assert.equal(result.messageId, "renamed-pdf-user");
      assert.equal(result.attachment.observedFilename, observed);
      assert.equal(result.attachment.expectedFilename, filename);
      assert.equal(result.attachment.filenameMatch, observed === filename ? "exact" : "collision_suffix");
    });
  }
  for (const observed of ["wrong(1).pdf", "battle-hex-grid-registration(0).pdf", "battle-hex-grid-registration(01).pdf",
    "battle-hex-grid-registration(-1).pdf", "battle-hex-grid-registration(1).pdf.exe", "battle-hex-grid-registration-1.pdf",
    "battle-hex-grid-registration(1)(2).pdf", "battle-hex-grid-registration(1).PDF"]) {
    await t.test(`does not normalize unrelated filename ${observed}`, async () => {
      await mount({ observed });
      const result = await inspect();
      assert.equal(result.submitted, false);
      assert.equal(result.messageId, "");
      assert.equal(result.attachment.state, "unexpected");
      assert.equal(result.attachment.filenameMatch, "mismatch");
    });
  }
  for (const mode of ["mismatched-text", "mismatched-button", "no-file-type", "hidden", "busy", "failed", "additional", "wrong-prompt", "missing-id", "history"]) {
    await t.test(`collision suffix never bypasses ${mode}`, async () => {
      await mount({ mode });
      const result = await inspect();
      assert.equal(result.submitted, false);
      assert.equal(result.messageId, "");
    });
  }
  await t.test("composer still requires the exact selected filename", async () => {
    await mount({ mode: "composer" });
    const result = await page.evaluate(filename => globalThis.ChatGptSubmission.attachments(
      document.querySelector("form"), filename, [filename]), filename);
    assert.equal(result.selected, true);
    assert.equal(result.ready, false);
    assert.equal(result.state, "unexpected");
  });
  await t.test("literal filename characters are not regex syntax", async () => {
    await mount({ expected: "review.v1+[draft].pdf", observed: "review.v1+[draft](1).pdf" });
    assert.equal((await inspect()).submitted, true);
    await mount({ expected: "review.v1+[draft].pdf", observed: "reviewXv11d(1).pdf" });
    assert.equal((await inspect()).submitted, false);
  });
  await t.test("an existing numeric suffix is never silently stripped", async () => {
    await mount({ expected: "report(1).pdf", observed: "report(2).pdf" });
    assert.equal((await inspect()).submitted, false);
    await mount({ expected: "report(1).pdf", observed: "report(1)(2).pdf" });
    assert.equal((await inspect()).submitted, true);
  });
});
