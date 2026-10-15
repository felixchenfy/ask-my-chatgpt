importScripts(
  "chatgpt-text.js",
  "chatgpt-pairing.js",
  "poll-scheduler.js",
  "chatgpt-tabs.js",
  "checkin-target.js",
  "checkin-state.js",
  "google-search.js",
  "quiz-solve.js",
  "quiz-outcome.js",
  "trusted-input.js",
  "input-diagnostics.js",
  "version-sync.js"
);

try { importScripts("quiz-pairing.js"); } catch { /* Optional local quiz lane is not paired. */ }

const BRIDGE = "http://127.0.0.1:18731";
const TOKEN = self.QuizPairing?.token || "";
const CHATGPT_BRIDGE = "http://127.0.0.1:18732";
const CHATGPT_TOKEN = self.ChatGptPairing.token;
const EXTENSION_VERSION = chrome.runtime.getManifest().version;
const WORKER_INSTANCE_ID = crypto.randomUUID();
const ONEPOINT_QUIZ_RUN_KEY = "onePointQuizRun";

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("wca-poll", { periodInMinutes: 1 });
});
chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create("wca-poll", { periodInMinutes: 1 });
});
chrome.alarms.onAlarm.addListener(() => { pollAll(); });
chrome.action.onClicked.addListener(() => { pollAll(); });

async function request(path, options = {}) {
  const response = await fetch(`${BRIDGE}${path}`, {
    ...options,
    cache: "no-store",
    headers: { "Content-Type": "application/json", "X-WCA-Token": TOKEN }
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

async function requestChatGpt(path, options = {}) {
  const response = await fetch(`${CHATGPT_BRIDGE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", "X-WCA-Token": CHATGPT_TOKEN }
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

async function event(runId, message) {
  await request("/event", { method: "POST", body: JSON.stringify({ run_id: runId, message }) });
}

async function waitForTab(tabId) {
  await new Promise((resolve, reject) => {
    const started = Date.now();
    let settled = false, timer;
    const finish = error => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      chrome.tabs.onRemoved.removeListener(removed);
      if (error) reject(error); else resolve();
    };
    const fail = (message, status, discarded = false) => {
      const error = new Error(message);
      error.tabLoadEvidence = { status, discarded, elapsedMs: Date.now() - started, budgetMs: 30000 };
      finish(error);
    };
    function listener(id, info) {
      if (id === tabId && info.status === "complete") finish();
    }
    function removed(id) {
      if (id === tabId) fail("Page tab closed while loading", "closed");
    }
    const inspect = async deadline => {
      try {
        const current = await chrome.tabs.get(tabId);
        if (settled) return;
        if (current.status === "complete") finish();
        else if (deadline) fail("页面加载超时", current.status || "unknown", Boolean(current.discarded));
      } catch (_) {
        fail("Page tab unavailable while loading", "unavailable");
      }
    };
    // Subscribe before the snapshot so completion during tabs.get is not lost.
    // Recheck at the deadline instead of inferring loading from event silence.
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.onRemoved.addListener(removed);
    timer = setTimeout(() => inspect(true), 30000);
    void inspect(false);
  });
}

async function navigate(tabId, url) {
  // Preserve the tab's current foreground/background state. The 1Point3Acres
  // workflow intentionally runs in a visible tab so Chrome will deliver
  // trusted input; ChatGPT managed tabs start in the background and can be
  // foregrounded once if their response rendering stalls.
  await chrome.tabs.update(tabId, { url });
  await waitForTab(tabId);
  await new Promise(resolve => setTimeout(resolve, 1200));
}

async function acquireChatGptTab(command) {
  const key = command.session_key
    ? `session:${command.session_key}`
    : `temporary:${command.run_id}`;
  const stored = await chrome.storage.session.get("chatGptManagedTabs");
  const managed = stored.chatGptManagedTabs || {};
  const destination = command.conversation_url || "https://chatgpt.com/";
  const managedTabId = Number.isInteger(managed[key]) ? managed[key] : null;
  const storedTab = managedTabId
    ? await chrome.tabs.get(managedTabId).catch(() => null)
    : null;
  const openChatGptTabs = await chrome.tabs.query({ url: "https://chatgpt.com/*" });
  const reusable = ChatGptTabs.chooseReusableTab(
    openChatGptTabs,
    destination,
    storedTab?.id
  );
  let tab = reusable || storedTab;
  let source = reusable
    ? (reusable.id === storedTab?.id ? "managed" : "existing")
    : (storedTab ? "managed-navigation" : "created");
  let createdByCompanion = false;
  if (!tab) {
    tab = await chrome.tabs.create({ url: destination, active: false });
    createdByCompanion = true;
    source = "created";
    await waitForTab(tab.id);
  } else if (!ChatGptTabs.sameDestination(tab.url, destination)) {
    await navigate(tab.id, destination);
    tab = await chrome.tabs.get(tab.id);
  }
  managed[key] = tab.id;
  await chrome.storage.session.set({ chatGptManagedTabs: managed });
  return {
    tab,
    key,
    persistent: Boolean(command.session_key),
    closeOnSuccess: createdByCompanion && !command.session_key,
    source
  };
}

async function releaseChatGptTab(managedTab, succeeded) {
  if (!managedTab?.tab?.id || managedTab.persistent) return;
  const stored = await chrome.storage.session.get("chatGptManagedTabs");
  const managed = stored.chatGptManagedTabs || {};
  delete managed[managedTab.key];
  await chrome.storage.session.set({ chatGptManagedTabs: managed });
  if (succeeded && managedTab.closeOnSuccess) {
    await chrome.tabs.remove(managedTab.tab.id).catch(() => {});
  }
}

function inspectChatGptThrottle() {
  const candidates = [...document.querySelectorAll([
    '[role="alert"]', '[role="alertdialog"]', '[role="dialog"]',
    '[data-testid*="toast"]', '[class*="toast"]', '[class*="banner"]'
  ].join(','))].filter(element => element.offsetParent !== null);
  const text = candidates.map(element => element.innerText || element.textContent || "").join("\n");
  const match = text.match(/rate limit|usage limit|too many requests|try again later|reached (?:the|your|a) .*limit|请求过多|达到.*限制/i);
  return match ? { throttled: true, message: match[0] } : { throttled: false };
}

async function execute(tabId, func, args = [], { preserveErrorResult = false } = {}) {
  const [injection] = await chrome.scripting.executeScript({ target: { tabId }, func, args });
  if (injection?.error) {
    const detail = typeof injection.error === "string"
      ? injection.error
      : injection.error.message || JSON.stringify(injection.error);
    throw new Error(`Injected script failed: ${detail}`);
  }
  const result = injection?.result;
  if (result?.error && !preserveErrorResult) {
    const error = new Error(result.error);
    error.inspectionPhase = func.name || "page_inspection";
    if (result.reasonCode) error.destinationEvidence = {
      reasonCode: result.reasonCode, expected: result.expected || "", observed: result.observed || ""
    };
    if (result.errorCode) error.code = result.errorCode;
    throw error;
  }
  return result;
}

async function prepareChatGptText(tabId) {
  const [injection] = await chrome.scripting.executeScript({
    target: { tabId },
    files: ["chatgpt-text.js", "chatgpt-tabs.js", "chatgpt-submission.js"]
  });
  if (injection?.error) {
    const detail = typeof injection.error === "string"
      ? injection.error
      : injection.error.message || JSON.stringify(injection.error);
    throw new Error(`Could not install the ChatGPT text helper: ${detail}`);
  }
}

async function assertChatGptDestination(tabId, expected, options = {}) {
  const state = await execute(tabId, (wanted, settings) =>
    ChatGptTabs.destinationState(wanted, location.href, settings), [expected, options],
    { preserveErrorResult: true });
  if (!state?.ok) {
    const error = new Error(state?.error || "conversation_mismatch: cannot verify destination");
    error.destinationEvidence = {
      reasonCode: state?.reasonCode || "invalid_destination_result",
      expected: state?.expected || "",
      observed: state?.observed || ""
    };
    throw error;
  }
  return state.url;
}

async function waitForChatGptDestination(tabId, expected, options = {}, budgetMs = 15000) {
  const started = Date.now();
  let observations = 0;
  while (true) {
    try {
      return await assertChatGptDestination(tabId, expected, options);
    } catch (error) {
      observations++;
      if (error.destinationEvidence?.reasonCode !== "canonical_redirected_home"
        || Date.now() - started >= budgetMs) {
        if (error.destinationEvidence) error.destinationEvidence = {
          ...error.destinationEvidence,
          waitElapsedMs: Date.now() - started,
          waitBudgetMs: budgetMs,
          observationCount: observations
        };
        throw error;
      }
    }
    // ChatGPT's SPA can briefly expose home while hydrating an already-open
    // canonical conversation. Observe only; never navigate, type or send here.
    await new Promise(resolve => setTimeout(resolve, 500));
  }
}

async function trustedClick(tabId, point) {
  const target = { tabId };
  await chrome.debugger.attach(target, "1.3");
  try {
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type: "mouseMoved", x: point.x, y: point.y
    });
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type: "mousePressed", x: point.x, y: point.y, button: "left", clickCount: 1
    });
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type: "mouseReleased", x: point.x, y: point.y, button: "left", clickCount: 1
    });
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
  }
}

async function trustedInsertText(tabId, text, expectedSelector = null) {
  const target = { tabId };
  await chrome.debugger.attach(target, "1.3");
  try {
    if (!expectedSelector) throw new Error("可信文本输入缺少目标选择器");
    await focusAndReplaceTextWithCdp(
      (method, params) => chrome.debugger.sendCommand(target, method, params),
      expectedSelector,
      text
    );
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
  }
}

async function trustedActivateButton(tabId, text, { contains = false } = {}) {
  // Restore/select the target tab. Windows may decline foreground focus, so
  // window.focused is diagnostic only: CDP's short focus lease and exact trusted
  // target-event verification decide whether the input actually worked.
  const currentTab = await chrome.tabs.get(tabId);
  if (Number.isInteger(currentTab.windowId)) {
    const currentWindow = await chrome.windows.get(currentTab.windowId);
    if (currentWindow.state === "minimized") {
      await chrome.windows.update(currentTab.windowId, { state: "normal" });
    }
    await chrome.windows.update(currentTab.windowId, { focused: true });
  }
  await chrome.tabs.update(tabId, { active: true });
  await new Promise(resolve => setTimeout(resolve, 250));
  const activeTab = await chrome.tabs.get(tabId);
  const activeWindow = await chrome.windows.get(activeTab.windowId);
  if (!activeTab.active || activeWindow.state === "minimized") {
    throw new Error(
      `无法激活 Chrome 工作页；tab.active=${activeTab.active}；` +
      `window.focused=${activeWindow.focused}；window.state=${activeWindow.state}`
    );
  }

  const target = { tabId };
  const expectedText = JSON.stringify(text);
  const comparison = contains
    ? `normalize(button.innerText).includes(normalize(${expectedText}))`
    : `normalize(button.innerText) === normalize(${expectedText})`;
  const expression = `(() => {
    const normalize = value => value.replace(/\\s+/g, " ").trim();
    const matches = [...document.querySelectorAll("button")]
      .filter(button => button.offsetParent !== null && ${comparison});
    return matches.length === 1 ? matches[0] : null;
  })()`;
  await chrome.debugger.attach(target, "1.3");
  try {
    try {
      await focusAndActivateWithCdp(
        (method, params) => chrome.debugger.sendCommand(target, method, params),
        expression,
        `按钮“${text}”`
      );
    } catch (error) {
      throw new Error(
        `${error.message}；Chrome tab.active=${activeTab.active}；` +
        `window.focused=${activeWindow.focused}；window.state=${activeWindow.state}`
      );
    }
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
  }
}

async function trustedReplaceText(tabId, point, text, expectedDestination) {
  const target = { tabId };
  await chrome.debugger.attach(target, "1.3");
  try {
    await assertChatGptDestination(tabId, expectedDestination);
    for (const event of [
      { type: "mouseMoved", x: point.x, y: point.y },
      { type: "mousePressed", x: point.x, y: point.y, button: "left", clickCount: 1 },
      { type: "mouseReleased", x: point.x, y: point.y, button: "left", clickCount: 1 }
    ]) {
      await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", event);
    }
    const selection = await chrome.debugger.sendCommand(target, "Runtime.evaluate", {
      expression: `(() => {
        const destination = (${ChatGptTabs.destinationState.toString()})(${JSON.stringify(expectedDestination)}, location.href);
        if (!destination.ok) return destination;
        const composer = (${ChatGptText.findComposer.toString()})();
        if (!composer) return { ok: false, error: "composer_not_found" };
        const existing = (${ChatGptText.composerText.toString()})(composer);
        if (existing.trim()) return { ok: false, error: "draft_conflict: preserve the existing user draft" };
        composer.focus();
        if (composer instanceof HTMLTextAreaElement || composer instanceof HTMLInputElement) {
          composer.setSelectionRange(0, 0);
          return { ok: true };
        }
        const range = document.createRange();
        range.selectNodeContents(composer);
        range.collapse(false);
        const selected = window.getSelection();
        selected.removeAllRanges();
        selected.addRange(range);
        return { ok: true };
      })()`,
      returnByValue: true
    });
    if (!selection.result?.value?.ok) {
      throw new Error(selection.result?.value?.error || "ChatGPT composer could not be selected while inserting text");
    }
    await assertChatGptDestination(tabId, expectedDestination);
    await chrome.debugger.sendCommand(target, "Input.insertText", { text });
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
  }
}

async function submitChatGptForm(tabId, expectedDestination) {
  const target = { tabId };
  await chrome.debugger.attach(target, "1.3");
  try {
    const submission = await chrome.debugger.sendCommand(target, "Runtime.evaluate", {
      expression: `(() => {
        const destination = (${ChatGptTabs.destinationState.toString()})(${JSON.stringify(expectedDestination)}, location.href);
        if (!destination.ok) return destination;
        const composer = (${ChatGptText.findComposer.toString()})();
        const send = (${ChatGptText.findSendButton.toString()})(composer?.closest("form"));
        if (!send) return { ok: false, error: "send_button_not_found" };
        if (send.disabled || send.getAttribute("aria-disabled") === "true") {
          return { ok: false, error: "send_button_disabled" };
        }
        if (!send.form) return { ok: false, error: "send_form_not_found" };
        if (send.type !== "submit") return { ok: false, error: "send_button_not_submit" };
        HTMLFormElement.prototype.requestSubmit.call(send.form, send);
        return { ok: true };
      })()`,
      returnByValue: true
    });
    if (!submission.result?.value?.ok) {
      throw new Error(`ChatGPT form submission failed: ${submission.result?.value?.error || "unknown_error"}`);
    }
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
  }
}

async function trustedAttachFile(tabId, filePath, expectedDestination) {
  const target = { tabId };
  await chrome.debugger.attach(target, "1.3");
  try {
    await assertChatGptDestination(tabId, expectedDestination);
    const document = await chrome.debugger.sendCommand(target, "DOM.getDocument", {
      depth: -1,
      pierce: true
    });
    const found = await chrome.debugger.sendCommand(target, "DOM.querySelector", {
      nodeId: document.root.nodeId,
      selector: 'input#upload-files, input[type="file"]'
    });
    if (!found.nodeId) throw new Error("ChatGPT PDF input was not found");
    await assertChatGptDestination(tabId, expectedDestination);
    await chrome.debugger.sendCommand(target, "DOM.setFileInputFiles", {
      nodeId: found.nodeId,
      files: [filePath]
    });
  } finally {
    await chrome.debugger.detach(target).catch(() => {});
  }
}

function inspectChatGptComposer(expectedQuestion, expectedFilename, { readOnly = false } = {}) {
  const composer = ChatGptText.findComposer();
  // Structural evidence only: no page title, body text, account or draft content.
  const composerEvidence = {
    present: Boolean(composer), visible: Boolean(composer && composer.offsetParent !== null)
  };
  const renderEvidence = {
    visibility: document.visibilityState, focused: document.hasFocus(), readyState: document.readyState
  };
  if (!composer || composer.offsetParent === null) {
    if (readOnly) return { ready: false, composerEvidence, renderEvidence };
    const text = document.body?.innerText || "";
    const challenge = /captcha|security check|verify you are human/i.test(document.title)
      || document.querySelector('iframe[src*="challenge"], iframe[src*="captcha"], [data-testid*="challenge"], [id*="captcha"], [class*="captcha"], form[action*="challenge"]');
    if (challenge || /captcha|security check|verify you are human/i.test(text)) {
      return { ready: false, error: "ChatGPT requires manual verification",
        errorCode: "manual_verification_required", composerEvidence, renderEvidence };
    }
    if (/log in|sign up/i.test(text)) return { ready: false, error: "Chrome is not signed in to ChatGPT",
      errorCode: "not_signed_in", composerEvidence, renderEvidence };
    return { ready: false, composerEvidence, renderEvidence };
  }
  if (!readOnly) composer.scrollIntoView({ block: "center", inline: "center" });
  const rect = composer.getBoundingClientRect();
  const value = ChatGptText.composerText(composer);
  // Empty composers may expose only Voice, with no Send button yet. Draft
  // protection must still inspect their form, including upload-in-progress UI.
  const form = composer.closest?.("form");
  const send = ChatGptText.findSendButton(form);
  // Some file inputs are portaled outside the form; input.files is only a
  // local-selection diagnostic. Readiness requires a card IN this send form.
  const fileInputs = [...document.querySelectorAll('input[type="file"]')];
  const selectedFiles = fileInputs
    .flatMap(input => [...(input.files || [])].map(file => file.name));
  const attachmentSelected = !expectedFilename || selectedFiles.some(name => name === expectedFilename);
  const attachment = ChatGptSubmission.attachments(form, expectedFilename, selectedFiles);
  const attachmentVisible = !expectedFilename || attachment.visible;
  const draftUploadInProgress = Boolean(form && (form.matches?.('[aria-busy="true"]')
    || form.querySelector('[aria-busy="true"], [role="progressbar"], progress, .animate-spin')));
  return {
    ready: true,
    composerEvidence,
    renderEvidence,
    composerPoint: { x: rect.left + rect.width / 2, y: rect.top + Math.min(rect.height / 2, 24) },
    composerValue: value.replace(/\u00a0/g, " ").trim(),
    composerValueNormalized: value
      .normalize("NFC")
      .replace(/\u00a0/g, " ")
      .replace(/[\u200b-\u200d\ufeff]/g, "")
      .replace(/\s+/g, " ")
      .trim(),
    expectedQuestionNormalized: expectedQuestion
      .normalize("NFC")
      .replace(/\u00a0/g, " ")
      .replace(/[\u200b-\u200d\ufeff]/g, "")
      .replace(/\s+/g, " ")
      .trim(),
    sendReady: Boolean(send && send.type === "submit" && !send.disabled && send.getAttribute("aria-disabled") !== "true"),
    attachment,
    attachmentReady: attachment.ready && !draftUploadInProgress,
    attachmentSelected,
    attachmentVisible,
    selectedFiles,
    draftUploadInProgress,
    composerDiagnostic: {
      formPresent: Boolean(form), formContainsComposer: Boolean(form?.contains(composer)),
      selectedFileCount: selectedFiles.length,
      selectedFilename: selectedFiles.length === 1 ? selectedFiles[0].slice(0, 256) : "",
      selectedFilesInForm: fileInputs.filter(input => form?.contains(input))
        .reduce((count, input) => count + (input.files?.length || 0), 0),
      composerCharacterCount: value.trim().length, draftUploadInProgress,
      sendButtonPresent: Boolean(send), sendButtonType: send?.type || "",
      sendButtonDisabled: Boolean(send && (send.disabled || send.getAttribute("aria-disabled") === "true"))
    },
    sendPoint: send ? (() => {
      const box = send.getBoundingClientRect();
      return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
    })() : null,
    assistantCount: ChatGptText.messageNodes("assistant").length,
    userCount: ChatGptText.messageNodes("user").length
  };
}

function inspectChatGptThinkingMode(requestedMode) {
  const normalize = value => String(value || "").replace(/\s+/g, " ").trim();
  const labelOf = node => normalize(node.innerText || node.getAttribute("aria-label") || "");
  const isVisible = node => {
    const rect = node.getBoundingClientRect(), style = getComputedStyle(node);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden"
      && !node.disabled && node.getAttribute("aria-disabled") !== "true";
  };
  const pointOf = node => {
    const rect = node.getBoundingClientRect();
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  };
  const isMax = label => /(^|[\s:–—-])max(?:imum)?(?=$|[\s:–—-])/i.test(label);
  // A popup relationship and explicit thinking metadata identify the control.
  // Never search all selected widgets or score unrelated menus as candidates.
  const triggers = [...document.querySelectorAll('button[aria-haspopup], [role="button"][aria-haspopup]')]
    .filter(node => isVisible(node) && !node.closest('[data-message-author-role]')
      && /thinking|reasoning|effort|思考|推理/i.test([
        node.getAttribute("aria-label"), node.getAttribute("data-testid"), node.getAttribute("title"), labelOf(node)
      ].join(" ")));
  const composerForm = ChatGptText.findComposer()?.closest("form");
  const observedComposerLabels = composerForm
    ? [...composerForm.querySelectorAll('button.__composer-pill[aria-haspopup="menu"]')]
      .filter(isVisible).map(labelOf).filter(Boolean).map(label => label.slice(0, 120))
    : [];
  const base = {
    requestedMode, verified: false, selectedLabel: "current page setting", maxOptions: [], triggerCandidates: [],
    triggerCount: triggers.length, observedComposerLabels
  };
  if (triggers.length !== 1) return { ...base, unsupportedReason: "thinking_control_not_unique" };
  const trigger = triggers[0];
  const popupId = trigger.getAttribute("aria-controls");
  const popup = popupId ? document.getElementById(popupId) : null;
  // The observed intelligence slider can label its highest setting "Pro".
  // Its internal data-max marker is not proof of the requested visible Max mode.
  // Record this representation without selecting a model or moving the slider.
  const intelligencePicker = popup && isVisible(popup)
    && popup.getAttribute("aria-labelledby") === trigger.id
    ? popup.querySelector('[data-testid="composer-intelligence-picker-content"]') : null;
  if (intelligencePicker) {
    const slider = intelligencePicker.querySelector('[data-model-reasoning-effort-slider] [role="slider"]');
    const power = slider?.closest('[role="menuitem"]');
    const descriptionIds = (power?.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
    const descriptions = descriptionIds.map(id => document.getElementById(id))
      .filter(node => node && intelligencePicker.contains(node)).map(node => normalize(node.textContent).slice(0, 160));
    return {
      ...base, selectedLabel: labelOf(trigger), unsupportedReason: "unsupported_thinking_effort_slider",
      sliderEvidence: {
        present: Boolean(slider), minimum: slider?.getAttribute("aria-valuemin") ?? null,
        maximum: slider?.getAttribute("aria-valuemax") ?? null,
        value: slider?.getAttribute("aria-valuenow") ?? null, descriptions
      }
    };
  }
  const options = popup && isVisible(popup)
    ? [...popup.querySelectorAll('[role="option"], [role="menuitemradio"], [role="radio"]')].filter(isVisible)
    : [];
  const selected = options.filter(node => node.getAttribute("aria-selected") === "true"
    || node.getAttribute("aria-checked") === "true" || node.getAttribute("data-state") === "checked");
  const verified = (selected.length === 1 && isMax(labelOf(selected[0]))) || isMax(labelOf(trigger));
  const maxOptions = options.filter(node => isMax(labelOf(node)));
  return {
    ...base, verified, selectedLabel: verified ? "Max" : labelOf(trigger),
    maxOptions: maxOptions.length === 1 ? [{ label: labelOf(maxOptions[0]), point: pointOf(maxOptions[0]) }] : [],
    triggerCandidates: [{ label: labelOf(trigger), point: pointOf(trigger) }]
  };
}

async function ensureChatGptThinkingMode(tabId, requestedMode, expectedDestination = null) {
  const inspect = async () => {
    if (expectedDestination) await assertChatGptDestination(tabId, expectedDestination);
    return execute(tabId, inspectChatGptThinkingMode, [requestedMode]);
  };
  let state = await inspect();
  let selectionAttempted = false;
  const attemptedLabels = [];
  const result = () => ({ ...state, requestedMode, selectionAttempted, attemptedLabels, fallbackReason: "" });
  if (requestedMode === "current" || state.verified) return result();
  const click = async candidate => {
    if (expectedDestination) await assertChatGptDestination(tabId, expectedDestination);
    selectionAttempted = true;
    attemptedLabels.push(candidate.label);
    await trustedClick(tabId, candidate.point);
    await new Promise(resolve => setTimeout(resolve, 500));
    state = await inspect();
  };
  if (!state.maxOptions?.length && state.triggerCandidates?.length === 1) {
    await click(state.triggerCandidates[0]);
  }
  if (!state.verified && state.maxOptions?.length === 1) await click(state.maxOptions[0]);
  if (state.verified) return result();
  const fallbackReason = "The current ChatGPT page did not expose a reliably linked Max thinking option.";
  if (requestedMode === "max") {
    const error = new Error(`${fallbackReason} The bridge did not send because strict Max was requested.`);
    error.thinkingState = { ...result(), fallbackReason };
    throw error;
  }
  return { ...result(), fallbackReason };
}

function inspectChatGptSubmission(runId, expectedQuestion, expectedDestination) {
  return ChatGptSubmission.inspect(runId, expectedQuestion, expectedDestination);
}

function expandChatGptSubmission(runId, expectedQuestion, expectedDestination) {
  return ChatGptSubmission.expand(runId, expectedQuestion, expectedDestination);
}

function beginChatGptSubmission(runId, question, filename, destination) {
  return ChatGptSubmission.begin(runId, question, filename, destination);
}

function endChatGptSubmission(runId) {
  ChatGptSubmission.end(runId);
}

function inspectChatGptResponse(
  assistantCountBefore,
  readOnly = false,
  userCountBefore = 0,
  submittedQuestion = "",
  submittedMessageId = "",
  expectedDestination = ""
) {
  const destination = ChatGptTabs.destinationState(expectedDestination, location.href, {
    allowProvisional: Boolean(submittedMessageId)
  });
  if (!destination.ok) return destination;
  const challenge = /captcha|security check|verify you are human/i.test(document.title)
    || document.querySelector('iframe[src*="challenge"], iframe[src*="captcha"], [data-testid*="challenge"], [id*="captcha"], [class*="captcha"], form[action*="challenge"]');
  if (challenge) {
    return { error: "ChatGPT requires manual verification" };
  }
  const isVisible = element => {
    if (!element) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none"
      && style.visibility !== "hidden"
      && rect.width > 0
      && rect.height > 0;
  };
  const assistants = ChatGptText.messageNodes("assistant");
  const visibleAssistants = assistants.filter(isVisible);
  const users = ChatGptText.messageNodes("user");
  const visibleUsers = users.filter(isVisible);
  const submittedUserPresent = Boolean(submittedMessageId) && visibleUsers.filter(user =>
    ChatGptText.messageId(user) === submittedMessageId
  ).length === 1;
  // WEB -> canonical resolution is accepted only with the original turn still
  // present. Hydration can unmount it briefly: wait, never substitute another turn.
  if (!ChatGptTabs.sameDestination(expectedDestination, destination.url) && !submittedUserPresent) {
    return { hasNewResponse: false, responseCandidates: [], awaitingSubmittedMessage: true,
      conversationUrl: destination.url, conversationUrlCanonical: false };
  }
  const responseSelection = ChatGptText.responseNodesForSubmittedTurn({
    readOnly,
    assistants,
    visibleAssistants,
    users,
    visibleUsers,
    assistantCountBefore,
    userCountBefore,
    submittedQuestion,
    submittedMessageId,
    nodeText: ChatGptText.userPromptText,
    follows: (first, second) => Boolean(
      first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING
    )
  });
  if (responseSelection.concurrentUserDetected) {
    return {
      error: "A newer user message appeared in the target conversation during response extraction"
    };
  }
  const responseNodes = responseSelection.nodes;
  const responseCandidates = responseNodes.flatMap(ChatGptText.responseTextCandidates);
  const fileCitationGroupCount = responseNodes.reduce((count, node) =>
    count + node.querySelectorAll('[data-file-citation-group-identity]').length, 0);
  const responseTurns = new Set(responseNodes.map(ChatGptText.responseTurn).filter(Boolean));
  const currentTurnCompletionButtons = new Set(responseNodes.flatMap(ChatGptText.responseCopyButtons));
  const visibleButtons = [...document.querySelectorAll('button')].filter(isVisible);
  const composerIdle = visibleButtons.some(button => {
    const metadata = [
      button.getAttribute('aria-label') || "",
      button.getAttribute('data-testid') || "",
      button.getAttribute('title') || ""
    ].join(" ");
    return /start dictation|start voice|开始听写|开始语音/i.test(metadata);
  });
  // A completed assistant turn exposes a Copy response action inside that turn's
  // .agent-turn/section ancestor. Historical turns must never satisfy this signal.
  const responseComplete = ChatGptText.responseCompletionSignalReady({
    readOnly,
    responseNodeCount: responseNodes.length,
    currentTurnCopyActionCount: currentTurnCompletionButtons.size,
    composerIdle
  });
  const visibleStopControls = visibleButtons.filter(button => {
    const metadata = [
      button.getAttribute('data-testid') || "",
      button.getAttribute('aria-label') || ""
    ].join(" ");
    return /(?:^|[-_ ])stop(?:[-_ ]|$)|stop generating|停止生成|停止回复/i.test(metadata);
  });
  const currentTurnVisibleBusyElements = new Set([...responseTurns].flatMap(turn => {
    const candidates = [...turn.querySelectorAll('[aria-busy="true"]')];
    if (turn.matches('[aria-busy="true"]')) candidates.push(turn);
    return candidates.filter(isVisible);
  }));
  const pageBusyElements = [...document.querySelectorAll('[aria-busy="true"]')];
  const generating = ChatGptText.responseGeneratingSignalActive({
    visibleStopControlCount: visibleStopControls.length,
    currentTurnVisibleBusyCount: currentTurnVisibleBusyElements.size
  });
  return {
    assistantCount: assistants.length,
    submittedUserPresent,
    hasNewResponse: responseNodes.length > 0,
    responseCandidates,
    responseTextEvidence: {
      fileCitationGroupCount,
      structuredFileCitationExtraction: fileCitationGroupCount > 0
    },
    responseComplete,
    renderEvidence: { visibility: document.visibilityState, focused: document.hasFocus() },
    completionEvidence: {
      currentTurnCopyActionCount: currentTurnCompletionButtons.size,
      composerIdle,
      responseNodeCount: responseNodes.length,
      responseTurnCount: responseTurns.size
    },
    generating,
    generationEvidence: {
      visibleStopControlCount: visibleStopControls.length,
      currentTurnVisibleBusyCount: currentTurnVisibleBusyElements.size,
      unrelatedVisibleBusyCount: pageBusyElements.filter(element =>
        isVisible(element) && ![...responseTurns].some(turn => turn.contains(element))
      ).length,
      hiddenBusyCount: pageBusyElements.filter(element => !isVisible(element)).length
    },
    conversationUrl: destination.url,
    conversationUrlCanonical: destination.canonical
  };
}

function summarizeChatGptObservation(state, responseText, expectation) {
  return {
    hasNewResponse: Boolean(state?.hasNewResponse),
    responseCharacterCount: expectation?.characterCount || 0,
    responseLineCount: expectation?.lineCount || 0,
    expectationMatches: Boolean(expectation?.matches),
    responseComplete: Boolean(state?.responseComplete),
    generating: Boolean(state?.generating),
    completionEvidence: state?.completionEvidence || {},
    generationEvidence: state?.generationEvidence || {},
    responseTextPresent: Boolean(responseText),
    responseCandidateCount: Array.isArray(state?.responseCandidates)
      ? state.responseCandidates.length
      : 0,
    responseTextEvidence: state?.responseTextEvidence || {},
    conversationUrlCanonical: Boolean(state?.conversationUrlCanonical),
    awaitingSubmittedMessage: Boolean(state?.awaitingSubmittedMessage),
    renderEvidence: state?.renderEvidence || {}
  };
}

async function maybeWakeChatGptRendering(tabId, runId, state, responseText, watch, destination) {
  const now = Date.now();
  if (watch.text !== responseText) { watch.text = responseText; watch.unchangedSince = now; }
  if (!ChatGptText.renderWakeNeeded(state, now - watch.unchangedSince, watch.attempted)) return;
  watch.attempted = true;
  // Bound the UI wake across service-worker restarts as well as this wait loop.
  const stored = await chrome.storage.session.get("chatGptRenderWake");
  if (stored.chatGptRenderWake === runId) return;
  await assertChatGptDestination(tabId, destination, { allowProvisional: true });
  await chrome.storage.session.set({ chatGptRenderWake: runId });
  const tab = await chrome.tabs.get(tabId);
  if (Number.isInteger(tab.windowId)) {
    const window = await chrome.windows.get(tab.windowId);
    if (window.state === "minimized") await chrome.windows.update(tab.windowId, { state: "normal" });
    await chrome.windows.update(tab.windowId, { focused: true });
  }
  await chrome.tabs.update(tabId, { active: true });
  await assertChatGptDestination(tabId, destination, { allowProvisional: true });
  // Activating a native window is not a renderer-visibility guarantee. Round 002
  // stayed hidden with hasFocus=true for the whole wait after activation. Hold
  // a target-scoped CDP focus lease until the wait's finally block; it neither
  // changes the DOM nor sends input. Do not detach another debugger on failure.
  const target = { tabId };
  await chrome.debugger.attach(target, "1.3");
  watch.debuggerTarget = target;
  try {
    await chrome.debugger.sendCommand(target, "Emulation.setFocusEmulationEnabled", { enabled: true });
    await assertChatGptDestination(tabId, destination, { allowProvisional: true });
    const rendered = await execute(tabId, () => ({
      visibility: document.visibilityState, focused: document.hasFocus()
    }));
    watch.focusEvidence = rendered;
    if (rendered?.visibility !== "visible") {
      throw new Error("ChatGPT rendering wake did not make the target document visible");
    }
  } catch (error) {
    await releaseChatGptRendering(watch);
    throw error;
  }
  await requestChatGpt("/event", { method: "POST", body: JSON.stringify({ run_id: runId,
    message: `Foregrounded the exact ChatGPT tab once and enabled a temporary focus-emulation lease after stalled rendering (${state.renderEvidence?.visibility || "unknown"} -> ${watch.focusEvidence.visibility}, ${Array.from(responseText).length} characters). No upload or submit was performed.` }) });
}

async function releaseChatGptRendering(watch) {
  const target = watch.debuggerTarget;
  if (!target) return;
  // Detaching also resets session-scoped emulation if explicit disable fails
  // (e.g. the user has closed the tab). Never acquire a new debugger to clean up.
  delete watch.debuggerTarget;
  try {
    await chrome.debugger.sendCommand(target, "Emulation.setFocusEmulationEnabled", { enabled: false });
  } catch { /* The session may already have detached. */ }
  await chrome.debugger.detach(target).catch(() => {});
}

// ChatGPT can briefly pause while streaming even when its current UI exposes no
// reliable stop control. Require a long quiet window so a transient prefix is
// never reported as the completed answer.
const CHATGPT_RESPONSE_STABLE_MS = 20000;
const CHATGPT_EXPECTATION_SETTLE_MS = 60000;

// Maintenance inspection of an existing target only. No navigation, form input,
// upload, send permit, response read, tab ownership change, or composer scrolling.
async function executeChatGptComposerDiagnostic(command) {
  let observation = { phase: "composer_diagnostic_failed" };
  try {
    const destination = ChatGptTabs.normalizedDestination(command.conversation_url);
    if (!ChatGptTabs.destinationState(destination, destination).canonical) {
      throw new Error("A canonical existing conversation is required");
    }
    const stored = await chrome.storage.session.get("chatGptManagedTabs");
    const managedId = stored.chatGptManagedTabs?.[`session:${command.session_key}`];
    const tabs = await chrome.tabs.query({ url: "https://chatgpt.com/*" });
    const tab = ChatGptTabs.chooseReusableTab(tabs, destination, managedId);
    if (!tab) throw new Error("No existing tab matches the diagnostic conversation");
    observation = { ...observation, tabId: tab.id,
      tabSource: tab.id === managedId ? "managed" : "existing" };
    await prepareChatGptText(tab.id);
    await assertChatGptDestination(tab.id, destination);
    const composer = await execute(tab.id, inspectChatGptComposer,
      ["", command.attachment_filename || "", { readOnly: true }]);
    observation = { ...observation, phase: "composer_diagnostic",
      composerEvidence: composer.composerEvidence, renderEvidence: composer.renderEvidence,
      attachment: composer.attachment || {}, selectedFileCount: composer.selectedFiles?.length || 0,
      composerDiagnostic: { ...composer.composerDiagnostic,
        matchingTabCount: tabs.filter(candidate => ChatGptTabs.sameDestination(candidate.url, destination)).length } };
  } catch (error) {
    observation.composerErrorCode = String(error.message).slice(0, 256);
  }
  await requestChatGpt("/observation", { method: "POST", body: JSON.stringify({
    run_id: command.run_id, last_observation: observation }) });
}

async function executeChatGpt(command) {
  const runId = command.run_id;
  const responseTimeoutMs = Math.min(Math.max(Number(command.response_timeout_seconds) || 900, 1), 3600) * 1000;
  // Preparation + confirmation share the caller's pre-submit budget. A
  // confirmed send receives the full response budget, matching Python.wait().
  const preparationDeadline = Date.now() + responseTimeoutMs;
  const renderWatch = { text: "", unchangedSince: Date.now(), attempted: false };
  let tab;
  let managedTab;
  let messageSubmitted = false;
  let submissionAttempted = false;
  let submissionPermitGranted = false;
  let lastObservation = {};
  let verifiedConversationUrl = "";
  let succeeded = false;
  let thinking = {
    requestedMode: command.thinking_mode || "current",
    verified: false,
    selectionAttempted: false,
    fallbackReason: ""
  };
  try {
    await requestChatGpt("/event", {
      method: "POST",
      body: JSON.stringify({ run_id: runId, message: "Chrome companion accepted the ChatGPT task." })
    });
    managedTab = await acquireChatGptTab(command);
    tab = managedTab.tab;
    await prepareChatGptText(tab.id);
    await requestChatGpt("/event", {
      method: "POST",
      body: JSON.stringify({
        run_id: runId,
        message: managedTab.source === "existing"
          ? "Reused an already-open ChatGPT tab for the exact conversation URL."
          : `Using ChatGPT tab source: ${managedTab.source}.`
      })
    });
    await new Promise(resolve => setTimeout(resolve, 1500));

    const destination = await waitForChatGptDestination(
      tab.id, command.conversation_url || "https://chatgpt.com/", { allowProvisional: true }
    );
    verifiedConversationUrl = destination;

    const initialThrottle = await execute(tab.id, inspectChatGptThrottle);
    if (initialThrottle.throttled) throw new Error(`CHATGPT_THROTTLED: ${initialThrottle.message}`);

    thinking = await ensureChatGptThinkingMode(tab.id, command.thinking_mode || "current", destination);
    await requestChatGpt("/event", {
      method: "POST",
      body: JSON.stringify({
        run_id: runId,
        message: thinking.verified
          ? `Selected and verified ChatGPT thinking mode: ${thinking.selectedLabel}`
          : (thinking.requestedMode === "prefer_max"
            ? `Max was preferred but not verified; continuing with the current page setting. ${thinking.fallbackReason}`
            : "Using the current ChatGPT page setting; no specific thinking mode was requested.")
      })
    });

    let composer;
    const composerStarted = Date.now();
    const composerDeadline = Math.min(preparationDeadline, composerStarted + 30000);
    do {
      await assertChatGptDestination(tab.id, destination);
      composer = await execute(tab.id, inspectChatGptComposer,
        [command.question, command.attachment_filename], { preserveErrorResult: true });
      const composerValid = composer !== null && typeof composer === "object"
        && !Array.isArray(composer) && typeof composer.ready === "boolean";
      lastObservation = {
        phase: !composerValid ? "composer_inspection_failed" : composer.ready ? "composer_ready" : "composer_loading",
        composerErrorCode: !composerValid ? "invalid_composer_result" : composer.errorCode || "",
        composerEvidence: composer?.composerEvidence || {},
        renderEvidence: composer?.renderEvidence || {},
        tabId: tab.id,
        tabSource: managedTab.source,
        attachment: composer?.attachment || {},
        selectedFileCount: composer?.selectedFiles?.length || 0,
        composerDiagnostic: composer?.composerDiagnostic || {},
        composerElapsedMs: Date.now() - composerStarted,
        composerBudgetMs: Math.max(0, composerDeadline - composerStarted)
      };
      const active = await requestChatGpt("/observation", { method: "POST", body: JSON.stringify({
        run_id: runId, last_observation: lastObservation }) });
      if (!active.ok) throw new Error("ChatGPT task was cancelled or expired before submission");
      if (!composerValid) throw new Error("ChatGPT composer inspection returned no valid result");
      if (composer?.error) throw new Error(composer.error);
      if (composer?.ready) break;
      await new Promise(resolve => setTimeout(resolve, 250));
    } while (Date.now() < composerDeadline);
    if (!composer?.ready) throw new Error("ChatGPT composer was not found within its readiness budget");
    const queuedAttachment = command.preserved_queued_attachment;
    const preservedAttachment = Boolean(queuedAttachment && command.attachment_path
      && queuedAttachment.tab_id === tab.id && !composer.composerValueNormalized
      && !composer.draftUploadInProgress && composer.attachment?.state === "ready"
      && composer.attachment.cardCount === 1 && composer.attachment.expectedCardCount === 1
      && composer.attachment.filenameMatch === "exact" && composer.attachment.visible
      && composer.attachment.observedFilename === command.attachment_filename);
    if (queuedAttachment && !preservedAttachment) {
      throw new Error("draft_conflict: the inspected queued attachment changed; preserve the draft");
    }
    if (!preservedAttachment && (composer.attachment?.cardCount || composer.draftUploadInProgress)) {
      throw new Error("ChatGPT composer already contains attachments; preserve the draft and resolve them manually");
    }
    const preservedDraft = Boolean(command.preserved_queued_draft
      && command.preserved_queued_draft.tab_id === tab.id && !command.attachment_path
      && composer.composerValueNormalized
      && composer.composerValueNormalized === composer.expectedQuestionNormalized);
    if (composer.composerValueNormalized && !preservedDraft) {
      throw new Error("draft_conflict: preserve the existing user draft before starting another request");
    }
    if (command.attachment_path && !preservedAttachment) {
      await trustedAttachFile(tab.id, command.attachment_path, destination);
      const uploadDeadline = Math.min(preparationDeadline, Date.now() + 60 * 1000);
      do {
        await new Promise(resolve => setTimeout(resolve, 500));
        composer = await execute(
          tab.id,
          inspectChatGptComposer,
          [command.question, command.attachment_filename]
        );
        if (composer.error) throw new Error(composer.error);
        lastObservation = { phase: "attachment_staging", attachment: composer.attachment || {},
          tabId: tab.id, tabSource: managedTab.source,
          composerDiagnostic: composer.composerDiagnostic || {} };
        const active = await requestChatGpt("/observation", { method: "POST", body: JSON.stringify({
          run_id: runId, last_observation: lastObservation }) });
        if (!active.ok) throw new Error("ChatGPT task was cancelled or expired before submission");
        // A just-started native upload can expose one unnamed card while its
        // progress UI is still active. Wait within this same upload budget;
        // it is never ready until the normal exact-name/card checks pass.
        const pendingUnnamedCard = composer.draftUploadInProgress === true
          && composer.attachment?.state === "unexpected" && composer.attachment.ready === false
          && composer.attachment.cardCount === 1 && composer.attachment.expectedCardCount === 0
          && composer.attachment.expectedFilename === command.attachment_filename
          && composer.attachment.observedFilename === "" && composer.attachment.filenameMatch === "mismatch";
        if (["failed", "unexpected", "removed"].includes(composer.attachment?.state) && !pendingUnnamedCard) {
          throw new Error(`ChatGPT attachment staging is ${composer.attachment.state}`);
        }
        if (composer.attachmentReady) break;
      } while (Date.now() < uploadDeadline);
      if (!composer.attachmentReady) {
        throw new Error(`ChatGPT did not finish staging ${command.attachment_filename}`);
      }
    }
    if (preservedAttachment) {
      await requestChatGpt("/event", { method: "POST", body: JSON.stringify({
        run_id: runId, message: "Preserved the independently inspected attachment from the proven-unsent parent; no file was uploaded again." }) });
    }
    if (!preservedDraft) {
      await trustedReplaceText(tab.id, composer.composerPoint, command.question, destination);
    } else {
      await requestChatGpt("/event", { method: "POST", body: JSON.stringify({
        run_id: runId, message: "Preserved the exact queued draft from the proven-unsent parent; no text was inserted." }) });
    }
    await new Promise(resolve => setTimeout(resolve, 500));
    composer = await execute(
      tab.id,
      inspectChatGptComposer,
      [command.question, command.attachment_filename]
    );
    // This is a distinct observation after native input. Retaining the earlier
    // empty-composer snapshot would hide whether insertion or comparison failed.
    lastObservation = { phase: "composer_input_verification",
      composerEvidence: composer?.composerEvidence || {},
      renderEvidence: composer?.renderEvidence || {},
      composerDiagnostic: composer?.composerDiagnostic || {},
      expectedPromptLength: Array.from(composer?.expectedQuestionNormalized || "").length,
      observedPromptLength: Array.from(composer?.composerValueNormalized || "").length,
      matches: composer?.composerValueNormalized === composer?.expectedQuestionNormalized,
      attachment: composer?.attachment || {}, tabId: tab.id, tabSource: managedTab.source };
    const inputActive = await requestChatGpt("/observation", { method: "POST", body: JSON.stringify({
      run_id: runId, last_observation: lastObservation }) });
    if (!inputActive.ok) throw new Error("ChatGPT task was cancelled before input verification");
    if (composer.error) throw new Error(composer.error);
    if (composer.composerValueNormalized !== composer.expectedQuestionNormalized) {
      throw new Error("ChatGPT composer does not contain the exact queued question");
    }
    if (!composer.attachmentReady) {
      throw new Error("The queued PDF is no longer staged in ChatGPT");
    }
    if (!composer.sendReady || !composer.sendPoint) throw new Error("ChatGPT Send button is not ready");
    const assistantCountBefore = composer.assistantCount;
    const userCountBefore = composer.userCount;
    if (Date.now() >= preparationDeadline) throw new Error("ChatGPT preparation budget expired before submission");
    await assertChatGptDestination(tab.id, destination);
    // Capture the ID set and ordering boundary immediately before the permit.
    await execute(tab.id, beginChatGptSubmission, [runId, command.question, command.attachment_filename, destination]);
    const permit = await requestChatGpt("/before-submit", {
      method: "POST", body: JSON.stringify({ run_id: runId })
    });
    if (!permit.ok) throw new Error("ChatGPT submission was cancelled or its permit expired");
    submissionPermitGranted = true;
    submissionAttempted = true;
    await submitChatGptForm(tab.id, destination);

    const confirmationStarted = Date.now();
    const submitDeadline = Math.min(preparationDeadline, confirmationStarted + 120000);
    renderWatch.unchangedSince = confirmationStarted;
    let observationSavedAt = 0;
    let submission;
    do {
      await new Promise(resolve => setTimeout(resolve, 250));
      submission = await execute(tab.id, inspectChatGptSubmission, [runId, command.question, destination]);
      lastObservation = { ...submission, phase: "submission_confirmation",
        confirmationElapsedMs: Date.now() - confirmationStarted,
        confirmationBudgetMs: submitDeadline - confirmationStarted,
        submissionAttempted: true, submissionConfirmed: Boolean(submission?.submitted),
        submissionOutcomeUnknown: !submission?.submitted, zeroSubmissionProven: false };
      // Save only bounded structural diagnostics, never prompt or page bodies.
      // This also detects cancellation/timeout without requesting another permit.
      if (Date.now() - observationSavedAt >= 1000 || submission?.submitted || submission?.promptExpansionAvailable || Date.now() >= submitDeadline || submission?.fatalError) {
        const active = await requestChatGpt("/observation", { method: "POST", body: JSON.stringify({
          run_id: runId, last_observation: lastObservation }) });
        observationSavedAt = Date.now();
        if (!active.ok) throw new Error("ChatGPT task was cancelled or expired after the submission attempt; do not resend");
      }
      if (submission?.fatalError) throw new Error(submission.fatalError);
      if (submission.submitted) break;
      if (submission.promptExpansionAvailable && Date.now() < submitDeadline) {
        await execute(tab.id, expandChatGptSubmission, [runId, command.question, destination]);
      }
      await maybeWakeChatGptRendering(tab.id, runId, submission, "", renderWatch, submission.conversationUrl || destination);
    } while (Date.now() < submitDeadline);
    if (!submission?.submitted) {
      throw new Error("ChatGPT submission could not be confirmed; it may have been sent. Do not resend.");
    }
    messageSubmitted = true;
    verifiedConversationUrl = submission.conversationUrl;
    await execute(tab.id, endChatGptSubmission, [runId]).catch(() => {});
    await requestChatGpt("/submitted", {
      method: "POST",
      body: JSON.stringify({ run_id: runId, conversation_url: submission.conversationUrl || "",
        message_id: submission.messageId })
    });
    if (!submission.messageId) throw new Error("Submitted message has no stable ID; extraction cannot be verified");

    const deadline = Date.now() + responseTimeoutMs;
    let previous = "";
    let stableSince = 0;
    renderWatch.unchangedSince = Date.now();
    while (Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 500));
      const state = await execute(
        tab.id,
        inspectChatGptResponse,
        [assistantCountBefore, false, userCountBefore, command.question, submission.messageId, submission.conversationUrl]
      );
      const throttle = await execute(tab.id, inspectChatGptThrottle);
      if (!state) continue;
      if (throttle?.throttled) throw new Error(`CHATGPT_THROTTLED: ${throttle.message}`);
      if (state.error) throw new Error(state.error);
      if (state.submittedUserPresent && state.conversationUrlCanonical
        && !ChatGptTabs.sameDestination(submission.conversationUrl, state.conversationUrl)) {
        // Persist the resolved URL with the SAME message ID, without another send.
        submission.conversationUrl = state.conversationUrl;
        verifiedConversationUrl = state.conversationUrl;
        await requestChatGpt("/submitted", {
          method: "POST",
          body: JSON.stringify({ run_id: runId, conversation_url: state.conversationUrl,
            message_id: submission.messageId })
        });
      }
      const selected = ChatGptText.chooseBestResponse(state.responseCandidates || []);
      const responseText = selected.text;
      const expectation = ChatGptText.responseExpectationState(
        responseText,
        command.expected_response_characters || 0,
        command.expected_response_lines || 0,
        command.minimum_response_characters || 0
      );
      lastObservation = summarizeChatGptObservation(state, responseText, expectation);
      if (Date.now() - observationSavedAt >= 1000) {
        const active = await requestChatGpt("/observation", { method: "POST", body: JSON.stringify({
          run_id: runId, last_observation: lastObservation }) });
        observationSavedAt = Date.now();
        if (!active.ok) throw new Error("ChatGPT task was cancelled or expired while reading its response");
      }
      await maybeWakeChatGptRendering(tab.id, runId, state, responseText, renderWatch, submission.conversationUrl);
      if (state.hasNewResponse && responseText && !state.generating) {
        if (responseText === previous) {
          if (!stableSince) stableSince = Date.now();
          const stableFor = Date.now() - stableSince;
          if (stableFor >= CHATGPT_RESPONSE_STABLE_MS && state.responseComplete) {
            if (!state.conversationUrlCanonical) continue;
            const url = new URL(state.conversationUrl);
            if (url.origin !== "https://chatgpt.com" || !url.pathname.startsWith("/c/")) {
              throw new Error("ChatGPT did not expose a canonical conversation URL");
            }
            if (!expectation.matches) {
              if (stableFor < CHATGPT_EXPECTATION_SETTLE_MS) continue;
              await requestChatGpt("/complete", {
                method: "POST",
                body: JSON.stringify({
                  run_id: runId,
                  ok: false,
                  status: "response_unverified",
                  response_text: responseText,
                  conversation_url: state.conversationUrl,
                  error: `The completed response did not match the expected size (${expectation.characterCount} characters, ${expectation.lineCount} lines).`,
                  response_character_count: expectation.characterCount,
                  response_line_count: expectation.lineCount,
                  response_extraction_verified: false,
                  message_submitted: true,
                  last_observation: lastObservation
                })
              });
              return;
            }
            await requestChatGpt("/complete", {
              method: "POST",
              body: JSON.stringify({
                run_id: runId,
                ok: true,
                status: "completed",
                response_text: responseText,
                conversation_url: state.conversationUrl,
                thinking_mode: thinking.verified ? "max" : "current",
                thinking_mode_requested: thinking.requestedMode,
                thinking_mode_verified: Boolean(thinking.verified),
                thinking_mode_selection_attempted: Boolean(thinking.selectionAttempted),
                thinking_mode_fallback_reason: thinking.fallbackReason || "",
                response_character_count: expectation.characterCount,
                response_line_count: expectation.lineCount,
                response_extraction_verified: true,
                last_observation: lastObservation,
                message_submitted: true
              })
            });
            succeeded = true;
            return;
          }
        } else {
          previous = responseText;
          stableSince = Date.now();
        }
      } else {
        previous = responseText;
        stableSince = 0;
      }
    }
    await requestChatGpt("/complete", {
      method: "POST",
      body: JSON.stringify({
        run_id: runId,
        ok: false,
        status: "response_unverified",
        response_text: previous,
        conversation_url: verifiedConversationUrl,
        error: "Timed out without reliable current-turn completion evidence.",
        response_extraction_verified: false,
        message_submitted: true,
        last_observation: lastObservation
      })
    });
    return;
  } catch (error) {
    const conversationUrl = verifiedConversationUrl || command.conversation_url || "";
    if (error.thinkingState) {
      thinking = error.thinkingState;
      lastObservation = { phase: "thinking_mode_validation_failed", thinkingEvidence: {
        requestedMode: thinking.requestedMode, verified: Boolean(thinking.verified),
        selectionAttempted: Boolean(thinking.selectionAttempted),
        unsupportedReason: thinking.unsupportedReason || "max_not_verified",
        triggerCount: thinking.triggerCount ?? null, selectedLabel: thinking.selectedLabel || "",
        observedComposerLabels: (thinking.observedComposerLabels || []).join(" | ").slice(0, 256),
        sliderPresent: Boolean(thinking.sliderEvidence?.present),
        sliderMinimum: thinking.sliderEvidence?.minimum ?? null,
        sliderMaximum: thinking.sliderEvidence?.maximum ?? null,
        sliderValue: thinking.sliderEvidence?.value ?? null,
        sliderDescription: (thinking.sliderEvidence?.descriptions || []).join(" | ").slice(0, 256)
      } };
    }
    if (error.tabLoadEvidence) lastObservation = {
      phase: "tab_load_failed", tabLoadEvidence: error.tabLoadEvidence
    };
    if (error.destinationEvidence) lastObservation = {
      phase: "destination_validation_failed", destinationEvidence: error.destinationEvidence,
      inspectionPhase: error.inspectionPhase || "destination_guard",
      tabSource: managedTab?.source || ""
    };
    await requestChatGpt("/complete", {
      method: "POST",
      body: JSON.stringify({
        run_id: runId,
        ok: false,
        status: String(error.message).startsWith("CHATGPT_THROTTLED:") ? "throttled"
          : (submissionAttempted && !messageSubmitted ? "submission_unconfirmed" : "failed"),
        error: error.message,
        conversation_url: conversationUrl,
        thinking_mode: thinking.verified ? "max" : "current",
        thinking_mode_requested: thinking.requestedMode,
        thinking_mode_verified: Boolean(thinking.verified),
        thinking_mode_selection_attempted: Boolean(thinking.selectionAttempted),
        thinking_mode_fallback_reason: thinking.fallbackReason || "",
        message_submitted: messageSubmitted,
        submission_attempted: submissionAttempted,
        zero_submission_proven: !submissionPermitGranted && !submissionAttempted,
        last_observation: lastObservation
      })
    }).catch(() => {});
  } finally {
    if (tab?.id) await execute(tab.id, endChatGptSubmission, [runId]).catch(() => {});
    await releaseChatGptRendering(renderWatch);
    await releaseChatGptTab(managedTab, succeeded);
    if (!succeeded && tab?.id && await chrome.tabs.get(tab.id).catch(() => null)) {
      await chrome.tabs.update(tab.id, { active: true }).catch(() => {});
    }
  }
}

async function executeChatGptRead(command) {
  const runId = command.run_id;
  const renderWatch = { text: "", unchangedSince: Date.now(), attempted: false };
  let tab;
  let managedTab;
  let succeeded = false;
  let destination = command.conversation_url;
  try {
    await requestChatGpt("/event", {
      method: "POST",
      body: JSON.stringify({
        run_id: runId,
        message: "Chrome companion opened the existing ChatGPT conversation in read-only recovery mode."
      })
    });
    managedTab = await acquireChatGptTab(command);
    tab = managedTab.tab;
    await prepareChatGptText(tab.id);
    destination = await waitForChatGptDestination(
      tab.id, command.conversation_url, { allowProvisional: true }
    );
    await requestChatGpt("/event", {
      method: "POST",
      body: JSON.stringify({
        run_id: runId,
        message: managedTab.source === "existing"
          ? "Reused an already-open ChatGPT tab for the exact conversation URL."
          : "Opened the exact ChatGPT conversation URL for read-only recovery."
      })
    });
    const responseTimeoutMs = Math.min(
      Math.max(Number(command.response_timeout_seconds) || 120, 1),
      600
    ) * 1000;
    const deadline = Date.now() + responseTimeoutMs;
    let previous = "";
    let stableSince = 0;
    let lastObservation = {};
    renderWatch.unchangedSince = Date.now();
    while (Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 500));
      const state = await execute(tab.id, inspectChatGptResponse,
        [0, true, 0, "", command.expected_message_id || "", destination]);
      if (!state) continue;
      if (state.error) throw new Error(state.error);
      if (state.submittedUserPresent && state.conversationUrlCanonical) destination = state.conversationUrl;
      const selected = ChatGptText.chooseBestResponse(state.responseCandidates || []);
      const expectation = ChatGptText.responseExpectationState(
        selected.text,
        command.expected_response_characters || 0,
        command.expected_response_lines || 0,
        command.minimum_response_characters || 0
      );
      lastObservation = summarizeChatGptObservation(state, selected.text, expectation);
      await maybeWakeChatGptRendering(tab.id, runId, state, selected.text, renderWatch, destination);
      if (state.hasNewResponse && selected.text && !state.generating) {
        if (selected.text === previous) {
          if (!stableSince) stableSince = Date.now();
          if (Date.now() - stableSince >= 3000 && state.responseComplete) {
            if (!state.conversationUrlCanonical) continue;
            if (!expectation.matches) {
              throw new Error("The existing ChatGPT response is visible but extraction remains unverified");
            }
            const url = new URL(state.conversationUrl);
            if (url.origin !== "https://chatgpt.com" || !url.pathname.startsWith("/c/")) {
              throw new Error("ChatGPT did not expose a canonical conversation URL");
            }
            await requestChatGpt("/complete", {
              method: "POST",
              body: JSON.stringify({
                run_id: runId,
                ok: true,
                status: "read_completed",
                response_text: selected.text,
                conversation_url: state.conversationUrl,
                response_character_count: expectation.characterCount,
                response_line_count: expectation.lineCount,
                response_extraction_verified: true,
                last_observation: lastObservation
              })
            });
            succeeded = true;
            return;
          }
        } else {
          previous = selected.text;
          stableSince = Date.now();
        }
      } else {
        stableSince = 0;
      }
    }
    throw new Error(
      `Timed out reading the existing ChatGPT response. Last observation: ${JSON.stringify(lastObservation)}`
    );
  } catch (error) {
    const conversationUrl = destination || command.conversation_url || "";
    await requestChatGpt("/complete", {
      method: "POST",
      body: JSON.stringify({
        run_id: runId,
        ok: false,
        status: "failed",
        error: error.message,
        conversation_url: conversationUrl,
        ...((error.tabLoadEvidence || error.destinationEvidence) ? { last_observation:
          error.tabLoadEvidence
            ? { phase: "tab_load_failed", tabLoadEvidence: error.tabLoadEvidence }
            : { phase: "destination_validation_failed",
              destinationEvidence: error.destinationEvidence,
              inspectionPhase: error.inspectionPhase || "destination_guard",
              tabSource: managedTab?.source || "" }
        } : {})
      })
    }).catch(() => {});
  } finally {
    await releaseChatGptRendering(renderWatch);
    await releaseChatGptTab(managedTab, succeeded);
  }
}

function pageGuard() {
  const text = document.body?.innerText || "";
  if (/验证码|安全验证|人机验证|captcha|security check|challenge/i.test(text)) return "页面要求人工验证";
  if (/请先登录|立即登录|尚未登录/.test(text) || /\/login|\/signin/.test(location.pathname)) return "Chrome 登录已失效";
  return null;
}

function extractQuiz() {
  const text = document.body?.innerText || "";
  const blocked = /验证码|安全验证|人机验证|captcha|security check|challenge/i.test(text)
    ? "页面要求人工验证"
    : (/请先登录|立即登录|尚未登录/.test(text) || /\/login|\/signin/.test(location.pathname) ? "Chrome 登录已失效" : null);
  if (blocked) return { error: blocked };
  if (/今日已答题|已经完成今日答题|明日再来/.test(text)) return { completed: true };
  const heading = [...document.querySelectorAll("h1,h2,h3")].find(node => /【题目】|每日答题题目/.test(node.innerText));
  if (!heading) return { error: "没有找到明确题干" };
  // The closest shared question/submit container, not viewport geometry. Sticky
  // navigation, ads, background rendering and scrolling must not become options.
  let container = heading.parentElement;
  let submits = [];
  while (container && container !== document.body) {
    submits = [...container.querySelectorAll("button")]
      .filter(button => button.innerText.trim() === "提交答案");
    if (submits.length) break;
    container = container.parentElement;
  }
  if (!container || container === document.body || submits.length !== 1) {
    return { error: "没有找到唯一的题目与提交按钮区域" };
  }
  const submit = submits[0];
  const choices = [...container.querySelectorAll("button")]
    .filter(button => button !== submit &&
      Boolean(heading.compareDocumentPosition(button) & 4) &&
      Boolean(button.compareDocumentPosition(submit) & 4))
    .map(button => button.innerText.trim()).filter(Boolean);
  if (choices.length < 2 || choices.length > 8) return { error: `选项数量不可靠：${choices.length}` };
  return { question: heading.innerText.replace(/^【题目】\s*/, "").trim(), choices };
}

function inspectAnswerControls(choiceText) {
  const text = document.body?.innerText || "";
  const blocked = /验证码|安全验证|人机验证|captcha|security check|challenge/i.test(text)
    ? "页面要求人工验证"
    : (/请先登录|立即登录|尚未登录/.test(text) || /\/login|\/signin/.test(location.pathname) ? "Chrome 登录已失效" : null);
  if (blocked) return { error: blocked };
  const normalize = value => value.replace(/\s+/g, " ").trim();
  const buttons = [...document.querySelectorAll("button")].filter(button => button.offsetParent !== null);
  const matches = buttons.filter(button => normalize(button.innerText) === normalize(choiceText));
  if (matches.length !== 1) return { error: `推荐选项匹配数量不是 1：${matches.length}` };
  const submit = buttons.find(button => button.innerText.trim() === "提交答案");
  if (!submit) return { error: "没有找到明确的“提交答案”按钮" };
  return {
    submitDisabled: submit.disabled
  };
}

async function solveQuizStep(tabId, runId, quiz) {
  const result = interpretQuizSolveResult(await request("/solve", {
    method: "POST",
    body: JSON.stringify({
      run_id: runId,
      question: quiz.question,
      choices: quiz.choices,
      checkin_verified: quiz.checkinVerified === true,
      web_evidence: quiz.webEvidence || []
    })
  }));
  if (result.pending) return false;
  const answer = result.answer;
  const liveQuiz = await execute(tabId, extractQuiz);
  assertReviewedQuizMatches(quiz, liveQuiz, answer);
  if (answer.review_id) {
    const preparation = await request("/review/prepare", {
      method: "POST",
      body: JSON.stringify({ run_id: runId, review_id: answer.review_id,
        quiz: { question: liveQuiz.question, choices: liveQuiz.choices } })
    });
    if (!preparation.authorized) throw new Error("Codex 审核已失效，不选择或提交");
  }
  await event(runId, `[7/8] ${answer.decided_by === "codex" ? "Codex 已批准" : "本地答案通过门槛"}，正在选择选项（尚未提交）：${answer.choice_text}`);
  let controls = await execute(tabId, inspectAnswerControls, [answer.choice_text]);
  if (controls.error) throw new Error(controls.error);
  try {
    await trustedActivateButton(tabId, answer.choice_text);
  } catch (error) {
    error.quizDiagnostic = { phase: "selecting_answer", finalSubmitAttempted: false,
      submissionPermitConsumed: false, error: error.message };
    throw error;
  }
  await new Promise(resolve => setTimeout(resolve, 300));
  controls = await execute(tabId, inspectAnswerControls, [answer.choice_text]);
  if (controls.error) throw new Error(controls.error);
  if (controls.submitDisabled) throw new Error("选中答案后提交按钮仍不可用");
  const finalQuiz = await execute(tabId, extractQuiz);
  assertReviewedQuizMatches(quiz, finalQuiz, answer);
  // Selection is reversible. Consume the one-use permit only after it succeeds,
  // immediately before final submission; a failed selection remains recoverable.
  if (answer.review_id) {
    const permit = await request("/review/permit", {
      method: "POST",
      body: JSON.stringify({ run_id: runId, review_id: answer.review_id,
        quiz: { question: finalQuiz.question, choices: finalQuiz.choices } })
    });
    if (!permit.authorized) throw new Error("未取得 Codex 审核提交许可，不提交");
  }
  await event(runId, "[7/8] 已验证选项点击且提交按钮可用；现在提交一次，随后只读取网站结果。");
  // Persist BEFORE the side effect. A new worker must only observe this attempt,
  // never select/submit again even if the click or its response was lost.
  const submittedAt = Date.now();
  await chrome.storage.session.set({
    [ONEPOINT_QUIZ_RUN_KEY]: { runId, tabId, quiz, phase: "awaiting_outcome", submittedAt }
  });
  await trustedActivateButton(tabId, "提交答案");
  await new Promise(resolve => setTimeout(resolve, 1800));
  return observeQuizSubmission(tabId, runId, submittedAt);
}

async function observeQuizSubmission(tabId, runId, submittedAt) {
  const outcome = await execute(tabId, inspectQuizOutcome);
  if (!outcome.completed) {
    const elapsed = Date.now() - submittedAt;
    const detail = outcome.pending_label || outcome.alerts?.join("；") || "尚未出现明确判分";
    if (Number.isFinite(elapsed) && elapsed >= 0 && elapsed < 600000) {
      await event(runId, `[8/8] 等待网站结果：${detail}；已等待约 ${Math.round(elapsed / 1000)} 秒。` +
        "下一次每分钟轮询只读取结果，不会再次提交；十分钟后若仍无结果则停止等待，保留未知状态。");
      return false;
    }
    const error = new Error(`网站结果等待超时：${detail}；提交状态未知，不重复提交。若是人机验证请人工处理。`);
    error.quizDiagnostic = { phase: "awaiting_outcome", outcome };
    throw error;
  }
  const label = outcome.verdict === "correct" ? "网站明确判定答对"
    : outcome.verdict === "incorrect" ? "网站明确判定答错（不会重试提交）"
    : "网站显示今日已答题，但无法确认对错";
  await event(runId, `[8/8] ${label}：${outcome.evidence}`);
  return outcome;
}

async function collectGoogleEvidence(runId, quiz) {
  let searchTab = null;
  try {
    await event(runId, "[6/8] 正在用 Google 搜索站内相关资料；排除第三方题库与 AI 概览，站内帖子仍需核验。");
    const queries = [buildGoogleSearchQuery(quiz.question, quiz.choices)];
    for (let index = 0; index < queries.length; index += 1) {
      const url = `https://www.google.com/search?q=${encodeURIComponent(queries[index])}&hl=zh-CN`;
      if (!searchTab) {
        searchTab = await chrome.tabs.create({ url, active: false });
        await waitForTab(searchTab.id);
      } else {
        await navigate(searchTab.id, url);
      }
      const inspection = await execute(searchTab.id, inspectGoogleSearchResults);
      if (inspection.error) throw new Error(inspection.error);
      if (inspection.results?.length) {
        await event(
          runId,
          `[6/8] Google 搜索完成，取得 ${inspection.results.length} 条候选证据` +
          "（限定一亩三分地域名；不代表每条都是官方规则）。"
        );
        return inspection.results;
      }
    }
    await event(runId, "[6/8] Google 没有返回可用结果；继续生成本地建议，仍须通过当前审核策略。");
    return [];
  } catch (error) {
    await event(
      runId,
      `[6/8] Google 搜索不可用：${error.message}；不会绕过验证，继续生成本地建议，仍须审核。`
    ).catch(() => {});
    return [];
  } finally {
    if (searchTab?.id) await chrome.tabs.remove(searchTab.id).catch(() => {});
  }
}

async function resumeQuiz(command) {
  const runId = command.run_id;
  let tab = null;
  let checkinVerified = false;
  try {
    const stored = await chrome.storage.session.get(ONEPOINT_QUIZ_RUN_KEY);
    const state = stored[ONEPOINT_QUIZ_RUN_KEY];
    if (!state || state.runId !== runId) throw new Error("没有找到可恢复的答题状态");
    checkinVerified = state.quiz.checkinVerified === true;
    tab = await chrome.tabs.get(state.tabId).catch(() => null);
    if (!tab) throw new Error("答题标签页已经关闭，无法安全恢复");
    const completed = state.phase === "awaiting_outcome"
      ? await observeQuizSubmission(tab.id, runId, state.submittedAt)
      : await solveQuizStep(tab.id, runId, state.quiz);
    if (!completed) return;
    await chrome.storage.session.remove(ONEPOINT_QUIZ_RUN_KEY);
    await request("/complete", {
      method: "POST",
      body: JSON.stringify({ run_id: runId, ok: completed.verdict !== "incorrect",
        quiz_outcome: completed, checkin_verified: checkinVerified,
        ...(completed.verdict === "incorrect" ? { error: "网站明确判定答错；不会重复提交" } : {}) })
    });
  } catch (error) {
    await chrome.storage.session.remove(ONEPOINT_QUIZ_RUN_KEY).catch(() => {});
    await event(runId, `自动运行停止：${error.message}`).catch(() => {});
    await request("/complete", {
      method: "POST",
      body: JSON.stringify({ run_id: runId, ok: false, error: error.message,
        quiz_diagnostic: error.quizDiagnostic, checkin_verified: checkinVerified })
    }).catch(() => {});
  } finally {
    if (tab?.id && await chrome.tabs.get(tab.id).catch(() => null)) {
      await chrome.tabs.update(tab.id, { active: true }).catch(() => {});
    }
  }
}

async function executeRun(command) {
  const runId = command.run_id;
  let tab;
  let checkinVerified = false;
  let checkinIssue = null;
  let quizOutcome = { verdict: "already_completed_unknown", evidence: "今日答题已完成，未执行本次提交" };
  try {
    await event(runId, "[2/8] 已连接当前 Chrome 登录会话，开始网页流程。");
    tab = await chrome.tabs.create({ url: "https://www.1point3acres.com/home", active: true });
    await waitForTab(tab.id);
    await navigate(tab.id, "https://www.1point3acres.com/next/daily-checkin");
    await event(runId, "[3/8] 签到页面已打开，正在读取当前状态。");
    let checkin = await waitForCheckinState(() => execute(tab.id, inspectCheckin));
    if (checkin.error) throw new Error(checkin.error);
    if (checkin.completed) {
      checkinVerified = true;
      await event(runId, "[4/8] 已验证今日签到已经完成，无需重复提交。");
    } else {
      try {
        if (!checkin.moodSelected) {
          await trustedActivateButton(tab.id, "开心", { contains: true });
          await new Promise(resolve => setTimeout(resolve, 300));
          checkin = await execute(tab.id, inspectCheckin);
          if (!checkin.moodSelected) {
            await event(runId,
              "[3/8] 已可信点击“开心”；页面未暴露稳定的选中标记，继续以精确备注值和提交按钮状态验证表单。");
          }
        }
        if (!checkin.noteValue) {
          await execute(tab.id, locateCheckinTarget, ["note"]);
          await trustedInsertText(
            tab.id,
            "今日签到",
            'textarea[placeholder*="记录今天的点点滴滴"]'
          );
          await new Promise(resolve => setTimeout(resolve, 300));
          checkin = await execute(tab.id, inspectCheckin);
        }
        if (checkin.noteValue !== "今日签到") {
          throw new Error(`签到备注输入后校验失败；当前值：${JSON.stringify(checkin.noteValue)}`);
        }
        if (checkin.submitDisabled) {
          throw new Error("签到表单未接受心情或备注，提交按钮仍不可用");
        }
        const previousLastSign = checkin.lastSign;
        await event(runId, "[3/8] 签到心情和备注已验证，正在提交签到。");
        await trustedActivateButton(tab.id, "提交签到");
        await new Promise(resolve => setTimeout(resolve, 1800));
        const immediateOutcome = await waitForCheckinState(
          () => execute(tab.id, inspectCheckin)
        );
        await navigate(tab.id, "https://www.1point3acres.com/next/daily-checkin");
        const verified = await waitForCheckinState(() => execute(tab.id, inspectCheckin));
        if (verified.error) throw new Error(verified.error);
        if (!verified.completed && verified.lastSign === previousLastSign) {
          const alerts = immediateOutcome.alerts?.join("；") || "页面没有错误提示";
          checkinIssue =
            `签到请求暂未生效；上次签到时间仍为 ${previousLastSign || "未知"}；${alerts}`;
          await event(runId,
            `[4/8] ${checkinIssue}。本次继续处理答题；后续运行会重新检查签到。`);
        } else {
          checkinVerified = true;
          await event(runId,
            `[4/8] 签到已提交并验证；上次签到时间更新为 ${verified.lastSign || "今日"}。`);
        }
      } catch (error) {
        checkinIssue = `签到步骤未完成：${error.message}`;
        await event(runId,
          `[4/8] ${checkinIssue}。签到与答题彼此独立，本次继续处理答题；后续运行会重新检查签到。`);
      }
    }

    await navigate(tab.id, "https://www.1point3acres.com/next/daily-question");
    await event(runId, "[5/8] 每日答题页面已打开，正在读取题目和选项。");
    // Always inspect a fresh page before considering another answer. An old
    // consumed permit is not a grade; an explicit grade IS completed, even wrong.
    const existingOutcome = await execute(tab.id, inspectQuizOutcome);
    if (!existingOutcome.completed && existingOutcome.pending_label) {
      throw new Error("网站仍在处理上次答题；先等待并核验结果，不重复提交");
    }
    const quiz = existingOutcome.completed ? { completed: true } : await execute(tab.id, extractQuiz);
    if (quiz.error) throw new Error(quiz.error);
    if (quiz.completed) {
      if (existingOutcome.completed) quizOutcome = existingOutcome;
      await event(runId, "[6/8] 已验证今日答题已经完成，无需调用本地模型。");
      await event(runId, "[7/8] 无需选择或提交答案。");
      await event(runId, checkinVerified
        ? "[8/8] 今日签到与答题状态均已验证，完整流程结束。"
        : "[8/8] 今日答题已验证完成；签到尚未完成，后续运行会重新检查。");
    } else {
      quiz.checkinVerified = checkinVerified;
      quiz.webEvidence = await collectGoogleEvidence(runId, quiz);
      await chrome.storage.session.set({
        [ONEPOINT_QUIZ_RUN_KEY]: { runId, tabId: tab.id, quiz }
      });
      const completed = await solveQuizStep(tab.id, runId, quiz);
      if (!completed) return;
      quizOutcome = completed;
      await chrome.storage.session.remove(ONEPOINT_QUIZ_RUN_KEY);
    }
    await request("/complete", { method: "POST", body: JSON.stringify({ run_id: runId,
      ok: quizOutcome.verdict !== "incorrect" && checkinVerified,
      quiz_outcome: quizOutcome, checkin_verified: checkinVerified,
      ...(quizOutcome.verdict === "incorrect"
        ? { error: "网站明确判定答错；不会重复提交" }
        : (checkinIssue ? { error: checkinIssue } : {})) }) });
  } catch (error) {
    await event(runId, `自动运行停止：${error.message}`).catch(() => {});
    await request("/complete", { method: "POST", body: JSON.stringify({ run_id: runId, ok: false,
      error: error.message, quiz_diagnostic: error.quizDiagnostic,
      checkin_verified: checkinVerified }) }).catch(() => {});
  } finally {
    if (tab?.id) {
      if (await chrome.tabs.get(tab.id).catch(() => null)) {
        await chrome.tabs.update(tab.id, { active: true }).catch(() => {});
      }
    }
  }
}

async function poll() {
  if (!TOKEN) return { bridgeAvailable: false, blockTasks: false };
  try {
    const query = new URLSearchParams({
      extension_version: EXTENSION_VERSION,
      protocol_version: String(WCA_EXTENSION_PROTOCOL_VERSION),
      worker_instance_id: WORKER_INSTANCE_ID
    });
    const command = await request(`/command?${query}`);
    const stored = await chrome.storage.local.get(WCA_EXTENSION_RELOAD_GUARD_KEY);
    const decision = decideExtensionVersionAction(
      command,
      EXTENSION_VERSION,
      stored[WCA_EXTENSION_RELOAD_GUARD_KEY]
    );
    if (decision.clearGuard) {
      await chrome.storage.local.remove(WCA_EXTENSION_RELOAD_GUARD_KEY);
    }
    if (decision.reload) {
      await chrome.storage.local.set({
        [WCA_EXTENSION_RELOAD_GUARD_KEY]: {
          ...decision.guard,
          workerInstanceId: WORKER_INSTANCE_ID,
          requestedAt: new Date().toISOString()
        }
      });
      chrome.runtime.reload();
      return { bridgeAvailable: true, blockTasks: true };
    }
    if (decision.blockTasks) return { bridgeAvailable: true, blockTasks: true };
    if (command.action === "diagnose_input") {
      let result;
      try { result = await runInputDiagnostic(command); }
      catch (error) { result = { diagnostic_id: command.diagnostic_id, ok: false, error: error.message }; }
      await request("/diagnostics/input-result", { method: "POST", body: JSON.stringify(result) });
    }
    if (command.action === "run_full_auto") await executeRun(command);
    if (command.action === "resume_quiz") await resumeQuiz(command);
    return { bridgeAvailable: true, blockTasks: false };
  } catch (_) {
    // The desktop app is normally not running; connection failures are expected.
    return { bridgeAvailable: false, blockTasks: false };
  }
}

async function pollChatGpt() {
  try {
    const query = new URLSearchParams({
      extension_version: EXTENSION_VERSION,
      protocol_version: String(WCA_EXTENSION_PROTOCOL_VERSION),
      worker_instance_id: WORKER_INSTANCE_ID
    });
    const command = await requestChatGpt(`/command?${query}`);
    const stored = await chrome.storage.local.get(WCA_EXTENSION_RELOAD_GUARD_KEY);
    const decision = decideExtensionVersionAction(
      command,
      EXTENSION_VERSION,
      stored[WCA_EXTENSION_RELOAD_GUARD_KEY]
    );
    if (decision.clearGuard) {
      await chrome.storage.local.remove(WCA_EXTENSION_RELOAD_GUARD_KEY);
    }
    if (decision.reload) {
      await chrome.storage.local.set({
        [WCA_EXTENSION_RELOAD_GUARD_KEY]: {
          ...decision.guard,
          workerInstanceId: WORKER_INSTANCE_ID,
          requestedAt: new Date().toISOString()
        }
      });
      chrome.runtime.reload();
      return;
    }
    if (decision.blockTasks) return;
    if (command.action === "inspect_chatgpt_composer") await executeChatGptComposerDiagnostic(command);
    if (command.action === "ask_chatgpt") await executeChatGpt(command);
    if (command.action === "read_chatgpt_conversation") await executeChatGptRead(command);
  } catch (_) {
    // The ChatGPT bridge is normally not running; connection failures are expected.
  }
}

const pollOnePointLane = PollScheduler.singleFlight(poll);
const pollChatGptLane = PollScheduler.singleFlight(pollChatGpt);
async function pollAll() {
  await Promise.all([pollOnePointLane(), pollChatGptLane()]);
}

pollAll();
