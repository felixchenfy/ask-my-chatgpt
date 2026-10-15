// Deterministic worker integration: no browser, network, upload or real send.
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const worker = fs.readFileSync(new URL("../../chrome_extension/service-worker.js", import.meta.url), "utf8");
const helpers = { self: {}, URL };
for (const name of ["chatgpt-text", "chatgpt-tabs"]) {
  vm.runInNewContext(fs.readFileSync(new URL(`../../chrome_extension/${name}.js`, import.meta.url), "utf8"), helpers);
}
const target = "https://chatgpt.com/c/offline-confirmation";
const question = "Review the attached PDF.";

async function run({ delay = 250, cancel = "", timeout = 900, failure = "", composerDelay = 0, composerError = "", missingComposerResult = false, pageLoadFailure = false, draft = "", preservedDraft, preservedAttachment, attachmentCard, attachmentPath, uploadState = "", responseText = "A complete offline review.", minimum, generating = false, responseComplete = true, collapsed = false } = {}) {
  let now = 1, sentAt = null, sends = 0, responsePolls = 0, cleanups = 0, wakes = 0, writes = 0, uploads = 0;
  const completions = [], acknowledgements = [], observations = [];
  let stagingPolls = 0;
  let expansions = 0;
  const scope = {
    URL, Date: { now: () => now },
    setTimeout: (callback, ms) => { now += ms; callback(); },
    ChatGptText: helpers.self.ChatGptText, ChatGptTabs: helpers.self.ChatGptTabs,
    CHATGPT_RESPONSE_STABLE_MS: 20000, CHATGPT_EXPECTATION_SETTLE_MS: 60000,
    inspectChatGptThrottle: function throttle() {}, inspectChatGptComposer: function composer() {},
    beginChatGptSubmission: function begin() {}, endChatGptSubmission: function end() {},
    inspectChatGptSubmission: function submission() {}, inspectChatGptResponse: function response() {},
    expandChatGptSubmission: function expand() {},
    acquireChatGptTab: async () => {
      if (pageLoadFailure) throw Object.assign(new Error("Page loading timeout"), {
        tabLoadEvidence: { status: "loading", discarded: false, elapsedMs: 30000, budgetMs: 30000 }
      });
      return { tab: { id: 1 }, source: "existing" };
    },
    prepareChatGptText: async () => {}, assertChatGptDestination: async () => {
      if (failure === "initial-destination") throw Object.assign(new Error("conversation_mismatch"), {
        destinationEvidence: { reasonCode: "canonical_redirected_home", expected: target, observed: "https://chatgpt.com/" }
      });
      return target;
    },
    waitForChatGptDestination: async () => {
      if (failure === "initial-destination") throw Object.assign(new Error("conversation_mismatch"), {
        destinationEvidence: { reasonCode: "canonical_redirected_home", expected: target, observed: "https://chatgpt.com/" }
      });
      return target;
    },
    ensureChatGptThinkingMode: async () => ({ requestedMode: "current" }),
    trustedReplaceText: async () => { writes++; }, trustedAttachFile: async () => { uploads++; },
    submitChatGptForm: async () => { sends++; sentAt = now; },
    releaseChatGptTab: async () => {}, releaseChatGptRendering: async () => { cleanups++; },
    maybeWakeChatGptRendering: async (_tab, _run, state, _text, watch) => {
      if (state.renderEvidence?.visibility === "hidden" && now - watch.unchangedSince >= 8000 && !watch.attempted) {
        watch.attempted = true; wakes++;
      }
    },
    summarizeChatGptObservation: () => ({}),
    chrome: { tabs: { get: async () => ({ id: 1 }), update: async () => {} } },
    requestChatGpt: async (route, options) => {
      const body = JSON.parse(options.body);
      if (route === "/complete") completions.push(body);
      if (route === "/submitted") acknowledgements.push(body);
      if (route === "/observation") observations.push(body);
      return { ok: !(route === "/before-submit" && cancel === "before")
        && !(route === "/observation" && cancel === "expand" && body.last_observation?.promptExpansionAvailable)
        && !(route === "/observation" && cancel === "composer")
        && !(route === "/observation" && ["after", "response"].includes(cancel) && sentAt !== null && now - sentAt >= 9000) };
    },
    execute: async (_tab, func) => {
      if (func.name === "begin" || func.name === "end") return {};
      if (func.name === "expand") { expansions++; return { expanded: true }; }
      if (func.name === "throttle") return { throttled: false };
      if (func.name === "composer" && missingComposerResult) return undefined;
      if (func.name === "composer" && composerError) return {
        ready: false, error: composerError,
        errorCode: composerError.includes("verification") ? "manual_verification_required" : "not_signed_in",
        composerEvidence: { present: false, visible: false },
        renderEvidence: { visibility: "hidden", focused: false, readyState: "complete" },
      };
      if (func.name === "composer") return now < composerDelay ? {
        ready: false, composerEvidence: { present: false, visible: false },
        renderEvidence: { visibility: "hidden", focused: false, readyState: "loading" },
      } : {
        ready: true, composerPoint: {}, sendPoint: {}, sendReady: true,
        composerValueNormalized: writes ? (failure === "input-mismatch" ? "partial" : question) : draft, expectedQuestionNormalized: question,
        attachmentReady: true, attachmentVisible: true, attachmentSelected: true, assistantCount: 1, userCount: 1,
        ...(failure === "attachment-card" ? { attachment: { cardCount: 1, state: "unexpected" } } : {}),
        ...(failure === "selected-file" ? { selectedFiles: ["private-file.pdf"] } : {}),
        ...(failure === "pending-upload" ? { draftUploadInProgress: true } : {}),
        ...(attachmentCard ? {attachment: attachmentCard} : {}),
        ...(uploads && uploadState ? (() => {
          stagingPolls++;
          const busy = uploadState !== "idle";
          const pending = { state: "unexpected", ready: false, cardCount: 1, expectedCardCount: 0,
            expectedFilename: "review.pdf", observedFilename: "", filenameMatch: "mismatch", visible: false };
          if (uploadState === "transient" && stagingPolls > 1) return {
            attachment: { state: "ready", ready: true, cardCount: 1, expectedCardCount: 1,
              observedFilename: "review.pdf", filenameMatch: "exact", visible: true },
            attachmentReady: true, draftUploadInProgress: false,
          };
          if (uploadState === "wrong-name") pending.observedFilename = "other.pdf";
          if (uploadState === "duplicate") pending.cardCount = 2;
          if (["failed", "removed"].includes(uploadState)) pending.state = uploadState;
          return { attachment: pending, attachmentReady: false, draftUploadInProgress: busy,
            composerDiagnostic: { composerCharacterCount: 0, draftUploadInProgress: busy } };
        })() : {}),
      };
      if (func.name === "submission") {
        if (failure === "closed" && now - sentAt > 9000) throw new Error("Tab closed");
        const submitted = now - sentAt >= delay && (!collapsed || expansions > 0);
        return { submitted, messageId: submitted ? "new-message" : "", candidateMessageId: "candidate",
          promptExpansionAvailable: collapsed && expansions === 0,
          candidateVerified: submitted, matches: true, promptContainerPresent: true,
          expectedPromptLength: question.length, observedPromptLength: question.length,
          userCount: 2, baselineUserCount: 1, destinationMatches: failure !== "destination",
          ...(failure === "destination" ? { fatalError: "conversation_mismatch" } : {}),
          renderEvidence: { visibility: "hidden" }, conversationUrl: target };
      }
      assert.equal(func.name, "response"); responsePolls++;
      return { hasNewResponse: true, submittedUserPresent: true,
        responseCandidates: [responseText], responseComplete,
        generating, conversationUrl: target, conversationUrlCanonical: true };
    },
  };
  // Keep page-state fixtures, but exercise the actual worker execution boundary.
  // A mock execute() previously hid its eager result.error handling.
  const inspectFixture = scope.execute;
  scope.chrome.scripting = {
    executeScript: async ({ func }) => [{ frameId: 0, result: await inspectFixture(1, func) }],
  };
  vm.runInNewContext(worker.slice(worker.indexOf("async function execute(tabId,"), worker.indexOf("async function prepareChatGptText(")), scope);
  vm.runInNewContext(worker.slice(worker.indexOf("async function executeChatGpt(command)"), worker.indexOf("async function executeChatGptRead(command)")), scope);
  await scope.executeChatGpt({ run_id: "offline", question, preserved_queued_draft: preservedDraft,
    preserved_queued_attachment: preservedAttachment, attachment_path: attachmentPath,
    attachment_filename: attachmentPath ? "review.pdf" : null,
    conversation_url: target, response_timeout_seconds: timeout, minimum_response_characters: minimum });
  assert.equal(completions.length, 1);
  assert.equal(cleanups, 1);
  return { sends, writes, uploads, stagingPolls, responsePolls, wakes, expansions, acknowledgements, observations, result: completions[0], totalElapsed: now - 1, elapsed: now - sentAt };
}

test("collapsed submitted prompt expands once then rechecks without resending", async () => {
  const r = await run({ collapsed: true });
  assert.equal(r.result.ok, true);
  assert.equal(r.expansions, 1);
  assert.equal(r.sends, 1);
  assert.equal(r.acknowledgements.length, 1);
});

test("cancellation is checked before the submitted prompt disclosure", async () => {
  const r = await run({ collapsed: true, cancel: "expand" });
  assert.equal(r.result.ok, false);
  assert.equal(r.expansions, 0);
  assert.equal(r.sends, 1);
  assert.equal(r.acknowledgements.length, 0);
});

test("a fresh upload's unnamed busy card waits for the exact ready card without another upload", async () => {
  const r = await run({ attachmentPath: "/offline/review.pdf", uploadState: "transient" });
  assert.equal(r.result.ok, true);
  assert.equal(r.uploads, 1);
  assert.equal(r.observations.filter(x => x.last_observation.phase === "attachment_staging").length, 2);
  assert.equal(r.writes, 1); assert.equal(r.sends, 1);
  assert.equal(r.observations.find(x => x.last_observation.phase === "attachment_staging").last_observation.attachment.state, "unexpected");
});

test("an unnamed busy card that never finishes consumes only the existing staging budget", async () => {
  const r = await run({ attachmentPath: "/offline/review.pdf", uploadState: "stalled" });
  assert.match(r.result.error, /did not finish staging review.pdf/);
  assert.equal(r.uploads, 1); assert.equal(r.stagingPolls, 120);
  assert.equal(r.writes, 0); assert.equal(r.sends, 0);
  assert.equal(r.result.zero_submission_proven, true);
});

for (const uploadState of ["idle", "wrong-name", "duplicate", "failed", "removed"]) {
  test(`upload terminal or conflicting card still fails immediately: ${uploadState}`, async () => {
    const r = await run({ attachmentPath: "/offline/review.pdf", uploadState });
    assert.match(r.result.error, /attachment staging is/);
    assert.equal(r.uploads, 1); assert.equal(r.stagingPolls, 1);
    assert.equal(r.writes, 0); assert.equal(r.sends, 0);
    assert.equal(r.result.zero_submission_proven, true);
  });
}

for (const mode of ["exact", "no-proof", "wrong-tab", "draft", "busy", "wrong-file", "duplicate", "missing", "cancelled"]) {
  test(`inspected queued PDF continuation preserves draft and never reuploads: ${mode}`, async () => {
    const card = {state:"ready",cardCount:1,expectedCardCount:1,filenameMatch:"exact",visible:true,observedFilename:"review.pdf"};
    if (mode === "wrong-file") card.observedFilename = "other.pdf";
    if (mode === "duplicate") card.cardCount = 2;
    if (mode === "missing") { card.state = "missing"; card.cardCount = 0; }
    const r = await run({attachmentPath:"/offline/review.pdf",attachmentCard:card,
      preservedAttachment: mode === "no-proof" ? undefined : {run_id:"parent",tab_id:mode === "wrong-tab" ? 2 : 1},
      draft:mode === "draft" ? "Unrelated draft" : "",failure:mode === "busy" ? "pending-upload" : "",
      cancel:mode === "cancelled" ? "before" : ""});
    assert.equal(r.uploads,0);
    assert.equal(r.writes,["exact","cancelled"].includes(mode) ? 1 : 0);
    assert.equal(r.sends,mode === "exact" ? 1 : 0);
    assert.equal(r.result.ok,mode === "exact");
    if (mode === "exact") assert.equal(r.acknowledgements[0].message_id,"new-message");
    else assert.equal(r.result.zero_submission_proven,true);
  });
}

for (const [draft, tab, ok] of [[question, 1, true], [question, 2, false], ["Edited draft", 1, false]]) {
  test(`preserved queued draft checks exact content and tab: ${tab}, ${ok}`, async () => {
    const r = await run({ draft, preservedDraft: { run_id: "parent", tab_id: tab } });
    assert.equal(r.writes, 0);
    assert.equal(r.sends, ok ? 1 : 0);
    assert.equal(r.result.ok, ok);
    if (!ok) assert.match(r.result.error, /draft_conflict/);
  });
}

test("input mismatch saves post-input counts instead of the earlier empty snapshot", async () => {
  const r = await run({ failure: "input-mismatch" });
  assert.equal(r.sends, 0); assert.equal(r.writes, 1);
  assert.match(r.result.error, /exact queued question/);
  const evidence = r.result.last_observation;
  assert.equal(evidence.phase, "composer_input_verification");
  assert.equal(evidence.observedPromptLength, 7);
  assert.equal(evidence.expectedPromptLength, question.length);
  assert.equal(evidence.matches, false);
  assert.ok(!JSON.stringify(evidence).includes("partial"));
});
for (const delay of [250, 15000]) test(`confirms after ${delay} ms with one send, then reads the same ID`, async () => {
  const r = await run({ delay });
  assert.equal(r.sends, 1); assert.equal(r.result.ok, true);
  assert.equal(r.acknowledgements[0].message_id, "new-message");
  assert.ok(r.responsePolls > 0);
  if (delay > 8000) assert.equal(r.wakes, 1);
});

for (const failure of ["attachment-card", "pending-upload"]) {
  test(`pre-upload ${failure} conflict preserves the triggering evidence without input`, async () => {
    const r = await run({ failure });
    assert.equal(r.sends, 0);
    assert.equal(r.writes, 0);
    assert.match(r.result.error, /already contains attachments/);
    assert.equal(r.result.last_observation.tabId, 1);
    assert.equal(r.result.last_observation.selectedFileCount, 0);
    assert.equal(r.result.last_observation.attachment.cardCount || 0, failure === "attachment-card" ? 1 : 0);
    assert.ok(!JSON.stringify(r.observations).includes("private-file.pdf"));
  });
}

test("a retained native file selection alone is not a draft attachment", async () => {
  const r = await run({ failure: "selected-file", delay: 250 });
  assert.equal(r.sends, 1);
  assert.equal(r.result.ok, true);
  assert.ok(r.observations.some(item => item.last_observation.selectedFileCount === 1));
});
test("unconfirmed request is bounded and preserves explicitly unverified candidate diagnostics", async () => {
  const r = await run({ delay: Infinity });
  assert.equal(r.sends, 1); assert.equal(r.responsePolls, 0);
  assert.equal(r.result.status, "submission_unconfirmed");
  assert.equal(r.elapsed, 120000);
  assert.equal(r.result.last_observation.candidateMessageId, "candidate");
  assert.equal(r.result.last_observation.candidateVerified, false);
  assert.equal(r.result.last_observation.confirmationElapsedMs, 120000);
  assert.equal(r.result.zero_submission_proven, false);
  assert.equal(r.result.submission_attempted, true);
  assert.equal(r.acknowledgements.length, 0);
});
test("confirmation cannot exceed the remaining pre-submission request budget", async () => {
  const r = await run({ delay: Infinity, timeout: 20 });
  assert.equal(r.result.status, "submission_unconfirmed");
  assert.ok(r.elapsed <= 20000);
  assert.ok(r.elapsed > 10000);
});
test("late-loading composer is observed before typing or sending", async () => {
  assert.equal((await run({ composerDelay: 6000 })).result.ok, true);
});

for (const error of ["Chrome is not signed in to ChatGPT", "ChatGPT requires manual verification"]) {
  test(`composer preserves terminal error: ${error}`, async () => {
    const r = await run({ composerError: error });
    assert.equal(r.result.error, error);
    assert.equal(r.result.last_observation.composerErrorCode,
      error.includes("verification") ? "manual_verification_required" : "not_signed_in");
    assert.equal(r.result.last_observation.renderEvidence.visibility, "hidden");
    assert.ok(r.totalElapsed < 3000);
    assert.equal(r.sends, 0); assert.equal(r.writes, 0);
    assert.equal(r.result.zero_submission_proven, true);
  });
}
test("genuine missing composer keeps bounded structural evidence, not a fabricated login diagnosis", async () => {
  const r = await run({ composerDelay: Infinity });
  assert.match(r.result.error, /composer was not found/);
  assert.equal(r.result.last_observation.composerEvidence.present, false);
  assert.equal(r.result.last_observation.renderEvidence.readyState, "loading");
  assert.equal(r.result.last_observation.composerErrorCode, "");
  assert.ok(r.result.last_observation.composerElapsedMs <= 30000);
  assert.equal(r.result.last_observation.composerBudgetMs, 30000);
  assert.equal(r.sends, 0); assert.equal(r.writes, 0);
  assert.equal(r.result.zero_submission_proven, true);
});
test("missing page-script result fails immediately, never masquerading as a loading composer", async () => {
  const r = await run({ missingComposerResult: true });
  assert.match(r.result.error, /composer inspection returned no valid result/i);
  assert.equal(r.result.last_observation.phase, "composer_inspection_failed");
  assert.equal(r.result.last_observation.composerErrorCode, "invalid_composer_result");
  assert.ok(r.totalElapsed < 3000);
  assert.equal(r.writes, 0); assert.equal(r.sends, 0);
  assert.equal(r.result.zero_submission_proven, true);
});
test("composer wait respects preparation budget and cancellation without input", async () => {
  const short = await run({ composerDelay: Infinity, timeout: 5 });
  assert.ok(short.totalElapsed <= 5000);
  assert.equal(short.result.last_observation.composerBudgetMs, 3500);
  const cancelled = await run({ composerDelay: Infinity, cancel: "composer" });
  assert.match(cancelled.result.error, /cancelled or expired/);
  assert.equal(cancelled.writes, 0); assert.equal(cancelled.sends, 0);
  assert.ok(cancelled.totalElapsed < 3000);
});
for (const cancel of ["before", "after"]) test(`cancellation ${cancel} permit retains safe submission semantics`, async () => {
  const r = await run({ delay: Infinity, cancel });
  assert.equal(r.result.ok, false);
  assert.equal(r.sends, cancel === "before" ? 0 : 1);
  assert.equal(r.result.zero_submission_proven, cancel === "before");
  assert.equal(r.acknowledgements.length, 0);
  if (cancel === "after") { assert.ok(r.elapsed < 11000); assert.equal(r.wakes, 1); }
});
for (const failure of ["destination", "closed"]) test(`${failure} stops observation without a resend or leaked lease`, async () => {
  const r = await run({ delay: Infinity, failure });
  assert.equal(r.sends, 1); assert.equal(r.result.ok, false);
  assert.equal(r.acknowledgements.length, 0);
  assert.ok(r.result.last_observation.candidateMessageId);
});
test("page loading failure records evidence before any input or submit permit", async () => {
  const r = await run({ pageLoadFailure: true });
  assert.equal(r.writes, 0); assert.equal(r.sends, 0);
  assert.equal(r.result.zero_submission_proven, true);
  assert.equal(r.result.last_observation.phase, "tab_load_failed");
  assert.equal(r.result.last_observation.tabLoadEvidence.status, "loading");
});
test("destination failure records its route evidence before any input or submit permit", async () => {
  const r = await run({ failure: "initial-destination" });
  assert.equal(r.writes, 0); assert.equal(r.sends, 0);
  assert.equal(r.result.zero_submission_proven, true);
  assert.equal(r.result.last_observation.phase, "destination_validation_failed");
  assert.equal(r.result.last_observation.destinationEvidence.reasonCode, "canonical_redirected_home");
});
test("cancellation after confirmation stops the response wait and releases rendering", async () => {
  const r = await run({ cancel: "response" });
  assert.equal(r.sends, 1); assert.equal(r.acknowledgements.length, 1);
  assert.equal(r.result.message_submitted, true); assert.equal(r.result.ok, false);
  assert.ok(r.elapsed < 11000); assert.equal(r.result.zero_submission_proven, false);
});

for (const draft of ["Unrelated unsent draft", question]) test(`preserves existing draft: ${draft}`, async () => {
  const r = await run({ draft });
  assert.match(r.result.error, /draft_conflict/);
  assert.equal(r.writes, 0); assert.equal(r.sends, 0);
  assert.equal(r.result.zero_submission_proven, true);
});
for (const minimum of [undefined, 1]) test(`complete unpunctuated list, minimum ${minimum}`, async () => {
  const r = await run({ minimum, responseText: "Actions\n- Keep state\n- Test retries\n- Add integration tests covering the real browser workflow" });
  assert.equal(r.result.ok, true); assert.equal(r.sends, 1);
});
for (const state of [{ generating: true }, { responseComplete: false }]) test(`length floor cannot replace completion evidence ${JSON.stringify(state)}`, async () => {
  const r = await run({ ...state, minimum: 1, timeout: 30 });
  assert.equal(r.result.ok, false); assert.equal(r.sends, 1);
});
