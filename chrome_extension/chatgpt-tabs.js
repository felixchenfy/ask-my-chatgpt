(function installChatGptTabs(root) {
  function normalizedDestination(value) {
    // Use the same identity grammar for tab reuse and every injected guard.
    return destinationState(value, value).url;
  }

  function sameDestination(left, right) {
    const normalizedLeft = normalizedDestination(left);
    return Boolean(normalizedLeft && normalizedLeft === normalizedDestination(right));
  }

  function chooseReusableTab(tabs, destination, managedTabId) {
    const matches = (tabs || []).filter(tab => sameDestination(tab.url, destination));
    const managed = matches.find(tab => tab.id === managedTabId);
    if (managed) return managed;
    return matches.sort((left, right) => {
      if (Boolean(left.active) !== Boolean(right.active)) return left.active ? -1 : 1;
      return Number(right.lastAccessed || 0) - Number(left.lastAccessed || 0);
    })[0] || null;
  }

  // Self-contained so the identical guard can run in the submission's JS task.
  function destinationState(expected, actual, { allowProvisional = false, allowNew = false } = {}) {
    const normalize = value => {
      try {
        const url = new URL(String(value || ""));
        if (url.origin !== "https://chatgpt.com" || url.username || url.password
          || url.searchParams.get("temporary-chat") === "true") return "";
        let pathname = url.pathname.replace(/\/$/, "") || "/";
        // Project conversations retain the same conversation ID after redirect.
        // Recognize only the complete project-conversation route, not generic /g/.
        const project = /^\/g\/g-p-[A-Za-z0-9-]+\/c\/([A-Za-z0-9-]+)$/.exec(pathname);
        if (project) pathname = `/c/${project[1]}`;
        // A new-chat UI can expose a local UUID before assigning the durable ID.
        // Normalize only this observed encoded colon, not arbitrary URL escapes.
        pathname = pathname.replace(/^\/c\/local-chatgpt%3[aA]/, "/c/local-chatgpt:");
        if (pathname !== "/" && !/^\/c\/(?:WEB:)?[A-Za-z0-9-]+$/.test(pathname)
          && !/^\/c\/local-chatgpt:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(pathname)) return "";
        return url.origin + pathname;
      } catch (_) { return ""; }
    };
    const wanted = normalize(expected);
    const observed = normalize(actual);
    const canonical = /^https:\/\/chatgpt\.com\/c\/[A-Za-z0-9-]+$/.test(observed);
    const isProvisional = value => /^https:\/\/chatgpt\.com\/c\/(?:WEB:[A-Za-z0-9-]+|local-chatgpt:[0-9a-f-]{36})$/i.test(value);
    const provisional = isProvisional(observed);
    // A newly submitted chat first exposes a WEB route. Only post-submit callers
    // grant allowNew; an existing conversation still cannot change identity.
    const allowedTransition = (canonical && allowProvisional && isProvisional(wanted))
      || ((canonical || provisional) && allowNew && wanted === "https://chatgpt.com/");
    const ok = Boolean(wanted && observed && (wanted === observed || allowedTransition));
    const canonicalRedirectedHome = /^https:\/\/chatgpt\.com\/c\/[A-Za-z0-9-]+$/.test(wanted)
      && observed === "https://chatgpt.com/";
    const reasonCode = ok ? "" : canonicalRedirectedHome
      ? "canonical_redirected_home" : "destination_mismatch";
    return { ok, url: observed, canonical, provisional, expected: wanted, observed, reasonCode,
      error: ok ? "" : canonicalRedirectedHome
        ? "conversation_mismatch: the requested conversation redirected to ChatGPT home and may be unavailable to the current Chrome account; no further action is allowed"
        : "conversation_mismatch: ChatGPT is not at the requested conversation; no further action is allowed" };
  }

  root.ChatGptTabs = { chooseReusableTab, normalizedDestination, sameDestination, destinationState };
})(self);
