(function installChatGptText(root) {
  // Self-contained: also serialized into the native-input page world. Keep all
  // composer users on the same contract rather than repairing one selector only.
  function findComposer() {
    const legacy = document.querySelector('#prompt-textarea')
      || document.querySelector('[contenteditable="true"][data-testid*="composer"]')
      || document.querySelector('textarea[placeholder*="Message"]');
    if (legacy) return legacy;
    const candidates = [...document.querySelectorAll('form [contenteditable="true"][role="textbox"][aria-label="Ask ChatGPT"]')]
      .filter(node => node.getClientRects().length > 0
        && getComputedStyle(node).visibility !== "hidden"
        && !node.closest('[data-message-author-role], [data-chatgpt-search-unit-key], [role="dialog"]'));
    return candidates.length === 1 ? candidates[0] : null;
  }

  function findSendButton(form) {
    if (!form) return null;
    const candidates = [...form.querySelectorAll('button')].filter(button =>
      button.form === form && button.getClientRects().length > 0
      && getComputedStyle(button).visibility !== "hidden"
      && (button.getAttribute("data-testid") === "send-button"
        || /^send(?: prompt| message)?$/i.test((button.getAttribute("aria-label") || "").trim())));
    return candidates.length === 1 ? candidates[0] : null;
  }

  function composerText(node) {
    if (!node) return "";
    // An editor DIV may expose an empty value property while containing text.
    // Only native form controls define their editable contents through value.
    if (node.tagName === "TEXTAREA" || node.tagName === "INPUT") return String(node.value ?? "");
    // Rich editors wrap paragraphs in P and auto-detected URLs in SPAN.
    // innerText can add layout breaks before those inline wrappers; textContent
    // drops paragraph breaks. Serialize authored node boundaries instead.
    const text = element => {
      if (element.nodeType === 3) return element.textContent || "";
      if (element.nodeType !== 1) return "";
      if (element.tagName === "BR") return "\n";
      const value = [...element.childNodes].map(text).join("");
      return element.tagName === "P" || element.tagName === "DIV" ? `\n${value}\n` : value;
    };
    return text(node);
  }

  function normalizeComposerText(value) {
    return String(value || "")
      .normalize("NFC")
      .replace(/\u00a0/g, " ")
      .replace(/[\u200b-\u200d\ufeff]/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function normalizeResponseText(value) {
    return String(value || "")
      .normalize("NFC")
      .replace(/\u00a0/g, " ")
      .replace(/[\u200b-\u200d\ufeff]/g, "")
      .split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/)
      .map(line => line.replace(/[\t ]+/g, " ").replace(/^[\s\x1c-\x1f\x85]+|[\s\x1c-\x1f\x85]+$/g, ""))
      .filter((line, index, lines) => line || (index > 0 && lines[index - 1]))
      .join("\n")
      .trim();
  }

  function searchUnitRole(node) {
    return /^(?:fallback-turn-\d+):\d+:(user|assistant)$/.exec(
      node?.getAttribute?.("data-chatgpt-search-unit-key") || "")?.[1] || "";
  }

  function messageId(node) {
    if (!node) return "";
    if (!node.hasAttribute?.("data-chatgpt-search-unit-key")) {
      return node.getAttribute?.("data-message-id") || "";
    }
    if (!searchUnitRole(node)) return "";
    const ids = [...new Set((node.getAttribute("data-chatgpt-search-message-ids") || "").trim().split(/\s+/))];
    // Repeated copies of one UUID are observed; several different IDs are not
    // one message. The shared outer turn key belongs to the user, not the reply.
    if (ids.length !== 1 || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(ids[0])) return "";
    const legacyId = node.getAttribute("data-message-id");
    const legacyRole = node.getAttribute("data-message-author-role");
    return (!legacyId || legacyId === ids[0]) && (!legacyRole || legacyRole === searchUnitRole(node)) ? ids[0] : "";
  }

  function messageNodes(role) {
    return [...document.querySelectorAll('[data-message-author-role], [data-chatgpt-search-unit-key]')]
      .filter(node => (node.hasAttribute("data-chatgpt-search-unit-key")
        ? searchUnitRole(node) : node.getAttribute("data-message-author-role")) === role);
  }

  function responseTurn(node) {
    if (searchUnitRole(node) === "assistant") {
      const turn = node.closest('[data-turn-key]');
      if (!turn || !messageId(node)) return null;
      const units = [...turn.querySelectorAll('[data-chatgpt-search-unit-key]')];
      const users = units.filter(unit => searchUnitRole(unit) === "user");
      const assistants = units.filter(unit => searchUnitRole(unit) === "assistant");
      if (users.length !== 1 || assistants.length !== 1 || assistants[0] !== node
        || !messageId(users[0]) || messageId(users[0]) === messageId(node)
        || turn.getAttribute("data-turn-key") !== messageId(users[0])
        || !(users[0].compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)) return null;
      return turn;
    }
    return node.closest('.agent-turn, article, section[data-turn="assistant"], [data-testid^="conversation-turn-"]');
  }

  function responseCopyButtons(node) {
    const turn = responseTurn(node);
    if (!turn) return [];
    return [...turn.querySelectorAll('button')].filter(button => {
      if (!button.getClientRects().length || getComputedStyle(button).visibility === "hidden") return false;
      const label = (button.getAttribute('aria-label') || "").trim();
      const testId = button.getAttribute('data-testid') || "";
      if (/^copy (?:message|table)$/i.test(label)) return false;
      if (!(/copy-(?:turn|response)|(?:turn|response)-copy/i.test(testId)
        || /^(?:copy|copy response|copy answer|复制|复制回复|复制回答)$/i.test(label))) return false;
      if (searchUnitRole(node) !== "assistant") return true;
      const unit = button.closest('[data-chatgpt-search-unit-key]');
      // Code-block toolbars also label their button "Copy". The observed
      // markdown-copy exclusion marks UI, not a completed response action.
      if (unit === node && button.closest('[data-markdown-copy="exclude"]')) return false;
      return button.closest('[data-turn-key]') === turn
        && (unit === node || (!unit
          && Boolean(node.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING)));
    });
  }

  // User turns contain attachment tiles and collapse controls as well as prose.
  // Select the body structurally; never strip filenames or accept a substring.
  function userPromptText(node) {
    if (!node?.querySelectorAll) return "";
    const bodies = [...node.querySelectorAll('[data-testid="collapsible-user-message-content"]')];
    const marked = [...node.querySelectorAll('[data-markdown-text-tone="user-message"]')];
    // Observed search units also render the raw prompt in one pre-wrap body,
    // without a markdown tone. Keep the same unique-body/exact-text checks;
    // multiple marked bodies must not fall through to a plausible fallback.
    const candidates = searchUnitRole(node) === "user"
      ? marked.length ? marked : [...node.querySelectorAll('.whitespace-pre-wrap')]
      : bodies.length ? bodies : [...node.querySelectorAll('.whitespace-pre-wrap')];
    if (candidates.length !== 1) return ""; // Unknown/ambiguous markup fails closed.
    const body = candidates[0];
    if (body.closest('[role="group"], [role="button"], button')) return "";
    // textContent joins adjacent paragraphs without a separator. Serialize the
    // observed block/BR boundaries, including collapsed prose; innerText alone
    // can omit hidden descendants. Inline links/spans must not split a word.
    const text = element => {
      if (element.nodeType === 3) return element.textContent || "";
      if (element.nodeType !== 1) return "";
      if (element.tagName === "BR") return "\n";
      // Observed raw user search bodies render a fenced block as PRE/CODE,
      // retaining its language but omitting the Markdown fence from textContent.
      // Restore only this text-only, direct-child shape. Full original-prompt
      // equality still rejects changed content, language or fence width.
      if (element.tagName === "PRE" && searchUnitRole(node) === "user"
        && body.matches('.whitespace-pre-wrap') && element.parentElement === body
        && element.getAttribute("data-markdown-copy") === "code-block"
        && element.childNodes.length === 1) {
        const code = element.firstElementChild;
        const language = code?.getAttribute("data-language") || "";
        // Unlabelled fences omit data-language entirely. An explicitly empty
        // attribute is not this observed shape; do not conflate the two.
        const languageShape = code?.hasAttribute("data-language")
          ? code.attributes.length === 2 && /^[a-zA-Z0-9_+.-]+$/.test(language)
          : code?.attributes.length === 1;
        if (code?.tagName === "CODE" && code.children.length === 0
          && code.getAttribute("data-markdown-copy-content") === "true"
          && languageShape) {
          return `\n\`\`\`${language}\n${code.textContent}\n\`\`\`\n`;
        }
      }
      const value = [...element.childNodes].map(text).join("");
      if (element.tagName === "CODE") {
        // October 5 raw search-unit prompts also use bare text-only CODE
        // children directly in their pre-wrap body. Other generic CODE shapes
        // and delimiter widths remain unknown; full equality still decides.
        const bareSearchInline = searchUnitRole(node) === "user"
          && body.matches('.whitespace-pre-wrap') && element.parentElement === body
          && element.attributes.length === 0 && element.children.length === 0
          && getComputedStyle(element).display === "inline";
        if (element.classList?.contains("user-message-inline-code") || bareSearchInline) {
          return `\`${value}\``;
        }
      }
      return element.tagName === "P" || element.tagName === "DIV"
        ? `\n${value}\n` : value;
    };
    return text(body);
  }

  function responseLooksSuspicious(value) {
    const text = normalizeResponseText(value);
    if (!text) return true;
    if (/^(?:第\d{0,6}(?:行)?[:：]?|line\s*\d*[:：]?)$/iu.test(text)) return true;
    const lines = text.split("\n").map(line => line.trim()).filter(Boolean);
    const lastLine = lines.at(-1) || "";
    const tokens = lastLine.match(/[\p{L}\p{N}]+/gu) || [];
    const compactLength = lastLine.replace(/\s+/g, "").length;
    const hasTerminalBoundary = /[.!?。！？…\])}\"'`]$/.test(lastLine);
    const hasMachineReadableTerminator = /^[A-Z0-9][A-Z0-9._:-]{7,}$/u.test(lastLine);
    if (
      (tokens.length >= 5 || compactLength >= 24)
      && !hasTerminalBoundary
      && !hasMachineReadableTerminator
    ) return true;
    if (lines.length < 4) return false;
    const counts = new Map();
    for (const line of lines) counts.set(line, (counts.get(line) || 0) + 1);
    const maximum = Math.max(...counts.values());
    return maximum >= 4 && maximum / lines.length >= 0.4;
  }

  function responseTextCandidates(node) {
    const clone = node.cloneNode(true);
    const fileCitationSelector = '[data-file-citation-group-identity]';
    const hasFileCitations = Boolean(node.querySelector(fileCitationSelector));
    const controls = [
      'button', '[role="button"]', '[role="toolbar"]', '[aria-hidden="true"]',
      '[data-markdown-copy="exclude"]',
      '[data-testid*="copy"]', '[data-testid*="citation"]', '[data-testid*="source"]',
    ];
    // Keep the established fallback for other markup. For observed file chips,
    // neither their block layout nor their labels are authored response text.
    clone.querySelectorAll([
      ...controls,
      ...(hasFileCitations ? [fileCitationSelector, 'script', 'style'] : ['[class*="citation"]', '[class*="source"]']),
    ].join(',')).forEach(element => element.remove());
    if (!hasFileCitations && searchUnitRole(node) !== "assistant") return [node.innerText || "", clone.textContent || ""];

    // Serialize the detached, UI-free tree instead of choosing a longer dirty
    // innerText. Preserve authored line/block boundaries; never parse or repair
    // JSON, strip filename strings, or mutate the user's live response DOM.
    const blocks = new Set(['ADDRESS', 'ARTICLE', 'ASIDE', 'BLOCKQUOTE', 'DIV', 'DL',
      'DT', 'DD', 'FIGCAPTION', 'FIGURE', 'FOOTER', 'H1', 'H2', 'H3', 'H4', 'H5',
      'H6', 'HEADER', 'HR', 'LI', 'MAIN', 'OL', 'P', 'PRE', 'SECTION', 'TABLE',
      'THEAD', 'TBODY', 'TFOOT', 'TR', 'UL']);
    const text = element => {
      if (element.nodeType === 3) return element.textContent || "";
      if (element.nodeType !== 1) return "";
      if (element.tagName === 'BR') {
        // ChatGPT commonly emits <br> followed by a source newline.
        return /^\r?\n/.test(element.nextSibling?.textContent || "") ? "" : "\n";
      }
      const value = [...element.childNodes].map(text).join("");
      if (element.tagName === 'TD' || element.tagName === 'TH') return `${value}\t`;
      return blocks.has(element.tagName) ? `\n${value}\n` : value;
    };
    return [text(clone)];
  }

  function chooseBestResponse(candidates) {
    const normalized = candidates.map(normalizeResponseText).filter(Boolean);
    if (!normalized.length) return { text: "", suspicious: true };
    // Writing style is diagnostic only; it cannot select or veto authored text.
    const text = normalized.reduce((best, value) => value.length > best.length ? value : best, "");
    return { text, suspicious: responseLooksSuspicious(text) };
  }

  function responseNodesForSubmittedTurn({
    readOnly = false,
    assistants = [],
    visibleAssistants = [],
    users = [],
    visibleUsers = [],
    assistantCountBefore = 0,
    userCountBefore = 0,
    submittedQuestion = "",
    submittedMessageId = "",
    nodeText = userPromptText,
    nodeId = messageId,
    follows = () => false,
  }) {
    if (readOnly && !submittedMessageId && !submittedQuestion) {
      return { nodes: visibleAssistants.slice(-1), concurrentUserDetected: false };
    }

    const expectedQuestion = normalizeComposerText(submittedQuestion);
    const matchingUsers = expectedQuestion
      ? visibleUsers.filter(user => normalizeComposerText(nodeText(user)) === expectedQuestion)
      : [];
    if (!submittedMessageId && users.length > userCountBefore + 1) {
      return { nodes: [], concurrentUserDetected: true };
    }
    // Never substitute a different user turn or a historical repetition of the prompt.
    const identifiedUsers = visibleUsers.filter(user => nodeId(user) === submittedMessageId);
    const submittedUser = submittedMessageId
      ? (identifiedUsers.length === 1 ? identifiedUsers[0] : null)
      : (users.length === userCountBefore + 1 && matchingUsers.includes(visibleUsers.at(-1))
        ? visibleUsers.at(-1) : null);

    if (submittedUser) {
      const newerUsers = users.filter(user =>
        user !== submittedUser && follows(submittedUser, user)
      );
      const followingAssistants = visibleAssistants.filter(assistant =>
        follows(submittedUser, assistant) && (searchUnitRole(assistant) !== "assistant"
          || (responseTurn(assistant)?.contains(submittedUser) && Boolean(messageId(assistant))))
      );
      return {
        nodes: followingAssistants,
        concurrentUserDetected: newerUsers.length > 0,
      };
    }

    return { nodes: [], concurrentUserDetected: false };
  }

  function responseCompletionSignalReady({
    readOnly = false,
    responseNodeCount = 0,
    currentTurnCopyActionCount = 0,
    composerIdle = false,
  }) {
    if (readOnly) return responseNodeCount > 0 && currentTurnCopyActionCount > 0;
    return currentTurnCopyActionCount > 0 && composerIdle;
  }

  function responseGeneratingSignalActive({
    visibleStopControlCount = 0,
    currentTurnVisibleBusyCount = 0,
  }) {
    return visibleStopControlCount > 0 || currentTurnVisibleBusyCount > 0;
  }

  function renderWakeNeeded(state, unchangedMs, attempted) {
    if (attempted || unchangedMs < 8000 || state?.responseComplete) return false;
    return state?.renderEvidence?.visibility === "hidden"
      || Boolean(state?.hasNewResponse && !state?.generating && state?.completionEvidence?.composerIdle);
  }

  function responseExpectationState(
    value,
    expectedCharacters = 0,
    expectedLines = 0,
    minimumCharacters = 0,
  ) {
    const text = normalizeResponseText(value);
    const characterCount = Array.from(text.replace(/\n/g, "")).length;
    const lineCount = text ? text.split("\n").length : 0;
    const charactersMatch = !expectedCharacters || characterCount === expectedCharacters;
    const linesMatch = !expectedLines || lineCount === expectedLines;
    const minimumCharactersMatch = !minimumCharacters || characterCount >= minimumCharacters;
    return {
      characterCount,
      lineCount,
      matches: charactersMatch && linesMatch && minimumCharactersMatch,
    };
  }

  root.ChatGptText = {
    findComposer,
    findSendButton,
    composerText,
    chooseBestResponse,
    responseTextCandidates,
    normalizeComposerText,
    normalizeResponseText,
    userPromptText,
    searchUnitRole,
    messageId,
    messageNodes,
    responseTurn,
    responseCopyButtons,
    responseCompletionSignalReady,
    responseGeneratingSignalActive,
    renderWakeNeeded,
    responseExpectationState,
    responseLooksSuspicious,
    responseNodesForSubmittedTurn,
  };
})(self);
