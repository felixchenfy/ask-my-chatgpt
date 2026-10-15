(function installChatGptSubmission(root) {
  // Isolated-world, per-attempt DOM evidence. No page internals/network access.
  // Reinstallation must disconnect any old observer, including after navigation.
  root.ChatGptSubmission?.end();
  let active = null;
  const seenAttachmentScopes = new WeakSet();
  const composerNode = () => root.ChatGptText.findComposer();
  const idOf = node => root.ChatGptText.messageId(node);
  const follows = (first, second) => Boolean(first?.isConnected && second?.isConnected
    && (first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING));
  const contains = (parent, child) => parent === child || Boolean(parent?.contains?.(child));

  function collisionRename(card, expectedFilename) {
    // Observed after submission: ChatGPT can change file.pdf to file(1).pdf.
    // This is not general basename normalization: retain the entire original
    // stem/extension and require matching semantic labels within this one tile.
    const observed = card.getAttribute("aria-label")?.trim() || "";
    const dot = expectedFilename.lastIndexOf(".");
    if (dot < 1) return false;
    const stem = expectedFilename.slice(0, dot);
    const extension = expectedFilename.slice(dot);
    if (extension.toLowerCase() !== ".pdf" || !observed.startsWith(stem)
      || !observed.endsWith(extension)) return false;
    const suffix = observed.slice(stem.length, -extension.length);
    if (!/^ ?\([1-9]\d{0,8}\)$/.test(suffix)) return false;
    return card.querySelector('.truncate.font-semibold')?.textContent?.trim() === observed
      && card.querySelector('[data-default-action="true"] button[aria-label]')?.getAttribute("aria-label") === observed
      && [...card.querySelectorAll('.truncate')].some(node => node.textContent?.trim() === "PDF");
  }

  function attachments(scope, expectedFilename = "", selectedFiles = [], { allowCollisionRename = false } = {}) {
    // Python's absent optional filename crosses JSON as null, not undefined.
    // Keep real filenames unchanged so missing/wrong PDF cards still fail closed.
    expectedFilename = expectedFilename ?? "";
    // Scope is the send form (staging) or ONE user node (confirmation).
    // Composer and submitted resource cards have distinct observed contracts.
    const selector = scope?.tagName === "FORM"
      ? '[role="group"][aria-label], .group\\/composer-attachment'
      : '[role="group"][aria-label], .group\\/resource-card';
    const cards = scope ? [...scope.querySelectorAll(selector)]
      .filter(node => !node.closest('[contenteditable="true"], textarea, [data-testid="collapsible-user-message-content"], [data-markdown-text-tone="user-message"], .whitespace-pre-wrap')) : [];
    const names = cards.map(node => {
      if (node.matches('[role="group"][aria-label]')) return node.getAttribute("aria-label")?.trim() || "";
      if (node.matches('.group\\/resource-card')) {
        // Observed submitted PDF: one overlay opener, matching filename and PDF
        // type, outside the prompt body. A filename elsewhere is insufficient.
        const opens = [...node.querySelectorAll('button.peer\\/resource-card[aria-label]')];
        const name = opens.length === 1 ? opens[0].getAttribute("aria-label")?.trim() || "" : "";
        const labels = [...node.querySelectorAll('.truncate.font-semibold')]
          .filter(label => label.textContent?.trim() === name);
        const types = [...node.querySelectorAll('.truncate')]
          .filter(label => label.textContent?.trim() === "PDF");
        return name && opens[0].type === "button" && labels.length === 1 && types.length === 1
          && node.querySelectorAll('button[aria-label]').length === 1 ? name : "";
      }
      // Observed October 4 composer card: filename display and opening button,
      // plus a separate Remove control in one wrapper. No whole-form text match.
      const opens = [...node.querySelectorAll('button.composer-attachment-surface[aria-label]')];
      const name = opens.length === 1 ? opens[0].getAttribute("aria-label")?.trim() || "" : "";
      const removes = [...node.querySelectorAll('button[aria-label]')]
        .filter(button => button.getAttribute("aria-label") === `Remove ${name}`);
      const labels = [...node.querySelectorAll('.truncate')]
        .filter(label => label.textContent?.trim() === name);
      return name && opens[0].type === "button" && removes.length === 1
        && node.querySelectorAll('button[aria-label]').length === 2 && labels.length === 1 ? name : "";
    });
    const selected = Boolean(expectedFilename && selectedFiles.includes(expectedFilename));
    const expected = cards.filter((card, index) => names[index] === expectedFilename
      || (allowCollisionRename && collisionRename(card, expectedFilename)));
    const visible = expected.filter(node => node.getClientRects().length > 0
      && getComputedStyle(node).visibility !== "hidden");
    // Native file inputs can retain the previous upload after its message was
    // submitted. FileList is selection evidence, not the current draft or payload.
    // Current scoped cards, their readiness and exact names decide acceptance.
    const extra = cards.length !== expected.length || expected.length > 1;
    let state = !expectedFilename && !cards.length ? "none"
      : extra ? "unexpected" : !visible.length
        ? (scope && seenAttachmentScopes.has(scope) ? "removed" : selected ? "selected" : "missing")
        : "ready";
    if (visible.length) {
      seenAttachmentScopes.add(scope);
      const card = visible[0];
      const status = [...card.querySelectorAll('[role="alert"], [role="status"]')]
        .map(node => node.textContent || "").join(" ");
      if (state === "ready" && /fail|error|unable|失败|错误/i.test(status)) state = "failed";
      if (state === "ready" && (card.matches('[aria-busy="true"]')
        || card.querySelector('[aria-busy="true"], [role="progressbar"], progress, .animate-spin')
        || /uploading|processing|上传中|处理中/i.test(status))) state = "uploading";
    }
    const filenameMatch = expected.length === 1
      ? (names[cards.indexOf(expected[0])] === expectedFilename ? "exact" : "collision_suffix")
      : cards.length ? "mismatch" : "missing";
    return { state, ready: state === "ready" || state === "none", selected,
      expectedFilename: expectedFilename.slice(0, 256),
      observedFilename: cards.length === 1 ? names[0].slice(0, 256) : "", filenameMatch,
      visible: visible.length === 1, cardCount: cards.length, expectedCardCount: expected.length };
  }

  function collect(state, records = []) {
    const users = root.ChatGptText.messageNodes("user");
    // MutationRecord.previousSibling retains the ordering boundary even if
    // React removes that old turn in the SAME mutation batch. Never infer order
    // merely from an unseen ID or a changed count after the anchor disappears.
    const insertedAfterBoundary = records.flatMap(record => {
      if (record.type !== "childList" || !state.tail) return [];
      if (!contains(record.previousSibling, state.tail)) return [];
      return [...record.addedNodes];
    });
    for (const node of users) {
      const id = idOf(node);
      if (state.baselineNodes.has(node) || (id && state.baselineIds.has(id))) continue;
      const witnessed = follows(state.tail, node)
        || insertedAfterBoundary.some(added => contains(added, node))
        || (state.emptyHome && !state.tail);
      if (witnessed) {
        // Bound observer memory. A burst of extra turns is ambiguous, not a
        // reason to keep scanning arbitrary conversation history indefinitely.
        if (state.witnessed.size >= 16 && !state.witnessed.has(node)) state.overflow = true;
        else state.witnessed.add(node);
      }
    }
  }

  function begin(runId, question, filename, destination) {
    end();
    const users = root.ChatGptText.messageNodes("user");
    const messages = [...users, ...root.ChatGptText.messageNodes("assistant")]
      .sort((a, b) => follows(a, b) ? -1 : follows(b, a) ? 1 : 0);
    const state = { runId, question, filename, destination,
      baselineNodes: new Set(messages), baselineIds: new Set(messages.map(idOf).filter(Boolean)),
      baselineUserCount: users.length, tail: messages.at(-1), witnessed: new Set(), overflow: false,
      emptyHome: !messages.length && new URL(destination).pathname === "/" };
    state.observer = new MutationObserver(records => collect(state, records));
    state.observer.observe(document.body, { childList: true, subtree: true, attributes: true,
      attributeFilter: ["data-message-id", "data-chatgpt-search-message-ids", "data-chatgpt-search-unit-key"] });
    active = state;
    // A worker interruption must not leave a permanent observer in the page.
    state.cleanupTimer = setTimeout(() => { if (active === state) end(runId); }, 125000);
    return { baselineUserCount: users.length, baselineIdCount: state.baselineIds.size };
  }

  function inspect(runId, expectedQuestion, expectedDestination) {
    const destination = ChatGptTabs.destinationState(expectedDestination, location.href, { allowNew: true });
    if (!destination.ok) return { submitted: false, candidateVerified: false,
      destinationMatches: false, fatalError: destination.error, conversationUrl: location.href };
    const state = active?.runId === runId ? active : null;
    if (!state) return { submitted: false, candidateVerified: false, destinationMatches: true,
      identityEvidence: "missing_baseline", conversationUrl: location.href };
    collect(state, state.observer.takeRecords());
    const users = root.ChatGptText.messageNodes("user");
    // Retain witnessed IDs across a re-mount of the SAME turn. Different stable
    // IDs are distinct candidates, even if one is no longer mounted.
    const keys = new Set([...state.witnessed].map(node => idOf(node) || node));
    const witnessedIds = new Set([...state.witnessed].map(idOf).filter(Boolean));
    const candidates = users.filter(node => state.witnessed.has(node) || witnessedIds.has(idOf(node)));
    const candidate = candidates.at(-1);
    const observed = ChatGptText.normalizeComposerText(ChatGptText.userPromptText(candidate));
    const expected = ChatGptText.normalizeComposerText(expectedQuestion);
    const matches = Boolean(expected) && observed === expected;
    const candidateId = idOf(candidate);
    const observedCandidateId = candidateId || (witnessedIds.size === 1 ? [...witnessedIds][0] : "");
    const composer = composerNode();
    const composerEmpty = Boolean(composer) && !ChatGptText.normalizeComposerText(
      root.ChatGptText.composerText(composer));
    // begin() runs only after exact pre-send staging. Permit the observed UI
    // collision rename here, never in the composer/file-selection checks.
    const attachment = attachments(candidate, state.filename, [], { allowCollisionRename: true });
    const unique = keys.size === 1 && candidates.length === 1 && !state.overflow;
    const submitted = unique && Boolean(candidateId) && !state.baselineIds.has(candidateId)
      && matches && composerEmpty && attachment.ready;
    const bodies = candidate ? [...candidate.querySelectorAll('[data-testid="collapsible-user-message-content"]')] : [];
    const promptExpansionAvailable = unique && Boolean(candidateId) && !state.baselineIds.has(candidateId)
      && !matches && Boolean(observed) && expected.startsWith(observed)
      && composerEmpty && attachment.ready && !state.expansionAttempted
      && Boolean(expansionButton(candidate));
    return { submitted, messageId: submitted ? candidateId : "", candidateMessageId: observedCandidateId,
      promptExpansionAvailable,
      candidateMounted: Boolean(candidate),
      candidateVerified: submitted, candidateCount: keys.size, matches,
      promptContainerPresent: Boolean(bodies.length || candidate?.querySelector('.whitespace-pre-wrap, [data-markdown-text-tone="user-message"]')),
      expectedPromptLength: Array.from(expected).length, observedPromptLength: Array.from(observed).length,
      stableIdPresent: Boolean(candidateId), composerEmpty, attachment,
      userCount: users.length, baselineUserCount: state.baselineUserCount,
      baselineIdCount: state.baselineIds.size, orderingAnchorPresent: Boolean(state.tail?.isConnected),
      identityEvidence: unique ? "witnessed_after_baseline" : "ambiguous_or_missing_successor",
      destinationMatches: true, conversationUrl: destination.url,
      renderEvidence: { visibility: document.visibilityState, focused: document.hasFocus() },
      generating: Boolean(document.querySelector('button[data-testid="stop-button"]')) };
  }

  function expansionButton(candidate) {
    // Only the observed disclosure within this user bubble, never prompt text,
    // historical turns, attachment controls or a submit/edit button.
    if (root.ChatGptText.searchUnitRole(candidate) !== "user") return null;
    const bubbles = [...candidate.querySelectorAll('[data-user-message-bubble="true"]')];
    if (bubbles.length !== 1) return null;
    const buttons = [...bubbles[0].querySelectorAll('button[type="button"][aria-expanded="false"][data-thread-find-skip="true"][data-markdown-copy="exclude"]')]
      .filter(button => button.textContent.trim() === "Show more"
        && !button.closest('[data-markdown-text-tone], .whitespace-pre-wrap')
        && !button.disabled && button.getAttribute("aria-disabled") !== "true"
        && button.getClientRects().length > 0 && getComputedStyle(button).visibility !== "hidden");
    return buttons.length === 1 ? buttons[0] : null;
  }

  function expand(runId, expectedQuestion, expectedDestination) {
    const state = active?.runId === runId ? active : null;
    if (!state || state.question !== expectedQuestion || state.destination !== expectedDestination
      || state.expansionAttempted) return { expanded: false };
    const checked = inspect(runId, expectedQuestion, expectedDestination);
    if (!checked.promptExpansionAvailable) return { expanded: false };
    const candidates = root.ChatGptText.messageNodes("user")
      .filter(node => idOf(node) === checked.candidateMessageId);
    const button = candidates.length === 1 ? expansionButton(candidates[0]) : null;
    if (!button) return { expanded: false };
    state.expansionAttempted = true; // One disclosure attempt per original send.
    button.click();
    // Disclosure is not submission confirmation. The next inspect must still
    // establish complete equality and all original identity guards.
    return { expanded: true };
  }

  function end(runId) {
    if (active && (!runId || active.runId === runId)) {
      active.observer.disconnect(); clearTimeout(active.cleanupTimer); active = null;
    }
  }
  root.ChatGptSubmission = { begin, inspect, expand, end, attachments };
})(self);
