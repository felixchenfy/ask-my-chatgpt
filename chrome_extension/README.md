# Chrome Companion

An unpacked Manifest V3 extension. It is the foundation of the ChatGPT bridge:
it types into the ChatGPT page in your normal signed-in Chrome, confirms the
message was sent once, and reads the reply back. It talks only to local servers
on `127.0.0.1`, using a random per-machine pairing token.

Load this folder once from `chrome://extensions` (Developer mode → Load
unpacked). Reload it after its files change or after a pairing token is
replaced.

## Which file belongs to what

| Group | Files |
| --- | --- |
| **ChatGPT bridge (core)** | `chatgpt-text.js`, `chatgpt-tabs.js`, `chatgpt-submission.js`, generated `chatgpt-pairing.js` |
| **Shared** | `manifest.json`, `service-worker.js`, `poll-scheduler.js`, `version-sync.js` |
| **Extra: 1point3acres assistant** | `checkin-state.js`, `checkin-target.js`, `google-search.js`, `quiz-solve.js`, `quiz-outcome.js`, `trusted-input.js`, `input-diagnostics.js`, generated `quiz-pairing.js` |

The extra is described in
[`src/web_content_agent/onepoint3acres/README.md`](../src/web_content_agent/onepoint3acres/README.md).
Its lane stays inactive until that desktop app has run once and created
`quiz-pairing.js`.

## Permissions

| Permission | Used by |
| --- | --- |
| `chatgpt.com`, `http://127.0.0.1:18732` | ChatGPT bridge |
| `www.1point3acres.com`, `www.google.com`, `http://127.0.0.1:18731` | Extra only (check-in quiz, its evidence search and its local app) |
| `tabs`, `scripting`, `debugger`, `storage`, `alarms` | Typing into pages, confirming clicks, reading replies and periodic polling, for both groups |

The generated `*-pairing.js` files hold local tokens. They are git-ignored;
never commit or share them.
