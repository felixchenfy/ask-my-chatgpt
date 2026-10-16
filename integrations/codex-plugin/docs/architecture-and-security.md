# Architecture and security

The installed plugin is a thin skill plus MCP route. Its `.mcp.json` calls the plugin-relative `scripts\launch-mcp.cmd`, which starts `server.mjs` from `CHATGPT_BRIDGE_SOURCE` when that is set (for example a stable `<WebContentAgent>\integrations\codex-plugin` checkout) and otherwise from the copy that contains the launcher. The adapter registers `ask_chatgpt` and the read-only `read_chatgpt_conversation` quickly enough for Codex's initial tool catalog, then invokes the configured WebContentAgent checkout (`WCA_PROJECT_ROOT`, else the repository containing `integrations/codex-plugin`, else a sibling `WebContentAgent`) only when a tool is called. WebContentAgent remains authoritative for validation, private task state, Chrome Companion control, single submission, automatic one-shot read-only recovery, waiting, and the returned canonical URL.

```text
Codex skill
  -> WebContentAgent ask_chatgpt MCP tool
  -> private task record + token-protected 127.0.0.1 bridge
  -> WebContentAgent Chrome Companion
  -> normal signed-in chatgpt.com page
  -> stable assistant response + canonical /c/... URL
```

For an optional PDF, WebContentAgent resolves the real path, permits `.pdf` only, enforces `%USERPROFILE%\.codex\chatgpt-pdf-review\config.json` roots and size limit, and passes the file to Chrome through `DOM.setFileInputFiles`. It never uses a native file picker or copies browser credentials.

The companion reuses one managed tab per stable session key. It clears synchronized ChatGPT drafts, inserts the exact prompt, verifies normalized text and the optional visible filename, revalidates the enabled Send form, submits once, records that a user message appeared, and anchors extraction to that exact user turn before waiting for a stable assistant response. Persistent fingerprints block duplicate submissions and ambiguous timeout retries for 30 minutes. Visible throttling opens a 30-minute no-send circuit breaker. It rejects repetitive citation-card extraction instead of reporting it as completed. If a stable canonical URL exists and extraction remains unverified, the Python bridge automatically reads that conversation once without sending another message.

Codex may retain an old cache-version directory after emptying it during plugin reinstall, while an already-open task or already-running MCP server still holds paths into that directory. `scripts\repair-installed-cache.ps1` restores only missing skill, MCP, manifest, launcher, server, and Python-adapter compatibility files. With `CHATGPT_BRIDGE_SOURCE` set, the launcher redirects future starts to that stable source checkout; the copied server and adapter let an already-running old process complete its current call. Intact cache files are never overwritten, and repair performs no browser or network action.

Authentication remains in the user's normal Chrome profile. Cookies, credentials, authorization headers, browser storage, MFA, CAPTCHA, verification, and recovery are never copied or automated. The bridge accepts only `https://chatgpt.com/` and canonical `https://chatgpt.com/c/...` destinations.

Companion 0.4.33 uses a randomly generated per-install local pairing token, separate from browser credentials. `scripts/prepare-companion.ps1` creates the ignored runtime token and extension asset without rotating existing pairing. The bridge rejects webpage Origins and wrong tokens. An unauthenticated `/command` migration response contains only public reload/version metadata, never queued work. This protects against untrusted web origins, not malicious processes already able to read this user's files.

Cancellation is cooperative over the Node/Python pipe. A single-use `/before-submit` permit is the commit boundary: cancellation before it prevents sending; after it, submission is ambiguous until acknowledged and must never be retried automatically. A claim timeout is proven zero-submission only after rechecking the claim under the same lock. Recovery carries the original submitted message ID, and missing identities fail closed. Independent single-flight polling lanes prevent unrelated Companion work from starving ChatGPT. Exact metrics count Unicode codepoints, not UTF-16 code units.

Private task records stay under WebContentAgent's ignored `runtime\state\chatgpt-bridge` directory. The conversation registry at `%USERPROFILE%\.codex\chatgpt-pdf-review\conversations.json` stores readable aliases and canonical URLs only. Both remain outside the plugin's Git history.

The former Edge/Playwright browser controller was removed when this plugin moved into the WebContentAgent repository; the MCP route above does not use it. Its standalone `src/paths.mjs` (PDF path policy) and `src/review-store.mjs` (send-state machine) helpers remain with their unit tests, but the live policy and send-once state are enforced by WebContentAgent's Python bridge. Playwright remains only a development dependency for the offline Companion DOM tests.
