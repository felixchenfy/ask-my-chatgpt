# ChatGPT PDF Review for Codex

A Codex plugin (skill + small Node MCP server) that asks ChatGPT for a second opinion through **your normal signed-in Chrome page**, using this repository's ChatGPT bridge and Chrome Companion. It supports text questions, optional PDF attachments, Max-mode requests, verified completed-response retrieval, automatic one-shot read-only recovery, and follow-ups in the same canonical ChatGPT conversation.

> **It sends real messages.** Every `ask_chatgpt` call types and submits one message in your signed-in ChatGPT account (and uploads the PDF if one is attached). The offline test suite never contacts ChatGPT.

The MCP server exposes `ask_chatgpt` and the read-only `read_chatgpt_conversation`, then runs `scripts/ask-chatgpt-adapter.py` with WebContentAgent's Python environment when a tool is called. WebContentAgent hands a private task file to its Chrome Companion over a token-protected loopback connection. The Companion uses ordinary visible `chatgpt.com` UI behavior; it does not use the OpenAI API, private ChatGPT endpoints, a second browser profile, copied cookies, stored passwords, or CAPTCHA/MFA automation.

Related article: [How I Let Codex Talk to ChatGPT Through My Signed-In Chrome](https://feiyuchen-blog.pages.dev/blog/codex-talks-to-chatgpt-through-chrome/).

## Prerequisites

- Windows with Google Chrome, Node.js 22+, and the Codex desktop app/CLI.
- This repository installed as described in the [root README](../../README.md) (`uv sync`), so `.venv\Scripts\python.exe` exists, and the unpacked Chrome Companion from `chrome_extension\` loaded once in Chrome (`chrome://extensions` → Developer mode → Load unpacked).
- ChatGPT signed in normally in that Chrome profile.

## How it finds WebContentAgent

| Setting | Default | Override |
| --- | --- | --- |
| WebContentAgent root | The repository that contains this plugin (`integrations/codex-plugin` → `../..`), else a sibling `../WebContentAgent` checkout | `WCA_PROJECT_ROOT` |
| Python | `<root>\.venv\Scripts\python.exe` | `WCA_PYTHON_EXECUTABLE` |
| Plugin source run by `scripts\launch-mcp.cmd` | The copy containing the launcher (`%~dp0..`) | `CHATGPT_BRIDGE_SOURCE` |
| PDF policy | `%USERPROFILE%\.codex\chatgpt-pdf-review\config.json` | `CHATGPT_PDF_REVIEW_CONFIG` |

Codex installs plugins into a versioned cache under `%USERPROFILE%\.codex\plugins\cache\`. A cached copy is outside this repository, so the relative default cannot find WebContentAgent there: set `WCA_PROJECT_ROOT` to your checkout, and optionally `CHATGPT_BRIDGE_SOURCE` to `<checkout>\integrations\codex-plugin` so the installed plugin always runs the live source (with its `node_modules`). `.mcp.json` forwards these variables to the server.

## Setup

From PowerShell in this directory:

```powershell
Set-Location <WebContentAgent>\integrations\codex-plugin
npm ci
.\scripts\setup.ps1 -AllowedPdfRoots 'C:\Reports','D:\Approved PDFs'
npm test
```

`setup.ps1` resolves WebContentAgent as above, verifies its virtual environment and Chrome Companion source, prepares the random local Companion pairing secret, writes `%USERPROFILE%\.codex\chatgpt-pdf-review\config.json` (the PDF upload allowlist and size limit; it overwrites an existing file), and initializes the private conversation registry. Without `-AllowedPdfRoots` the allowlist is only your Documents folder. Text-only questions do not need any PDF root.

Reload the Chrome Companion only after its source changes; ordinary sends are automatic. Private task records live under WebContentAgent's ignored `runtime\state\chatgpt-bridge` directory. The conversation registry stays under `%USERPROFILE%\.codex\chatgpt-pdf-review\`, outside Git.

### Optional local preferences

The skill can honour an optional, uncommitted local preferences file at `skills/chatgpt-pdf-review/references/user-preferences.md`, for example a standing consent for task-relevant GPT messages, a default review conversation, or a designated Codex maintenance task for bridge failures. Nothing in this repository grants such consent; without the file the skill sends only what you ask it to send. Keep the file out of Git (it typically contains private conversation URLs).

### Canonical plugin validation

On Windows, validate this plugin through the project-owned wrapper:

```powershell
.\scripts\validate-plugin.ps1
```

The wrapper forces Python UTF-8 mode, supplies `PyYAML` in an isolated `uv` environment, validates both the skill and the plugin with Codex's bundled `skill-creator`/`plugin-creator` helpers under `%USERPROFILE%\.codex\skills\.system\`, and then runs the Node regression suite. It does not install `PyYAML` into system Python. `npm run validate:plugin` is an equivalent shortcut.

Do not invoke the bundled `quick_validate.py` or `validate_plugin.py` scripts with bare system `python`. `ModuleNotFoundError: No module named 'yaml'` means the required isolated dependency was bypassed, while a `UnicodeDecodeError` mentioning `cp1252` means Python UTF-8 mode was bypassed. Neither message by itself indicates invalid skill or plugin content; rerun the canonical wrapper. Use `-SkipTests` only when the Node suite has already passed in the same change cycle.

### Install and update

Register this directory as `chatgpt-pdf-review` in your personal Codex plugin marketplace (for example with Codex's plugin-creator skill), then use the project-owned update wrapper instead of editing the marketplace or manifest version by hand:

```powershell
.\scripts\update-installed-plugin.ps1
```

It runs the validator above, reads your personal marketplace name, refreshes the cachebuster with Codex's bundled helper, reinstalls the plugin with `codex plugin add`, and repairs missing compatibility entrypoints in retained old cache directories (`scripts\repair-installed-cache.ps1`). That last step matters for already-open Codex tasks: Codex can empty an old version directory during reinstall while a task still holds its launcher or adapter path. The repair copies only missing files and never contacts ChatGPT.

## Use

Ask Codex:

```text
Ask ChatGPT for a second opinion on this design.
Send C:\Reports\perception-report.pdf to ChatGPT and ask for a critical review.
Continue the report-review conversation with this follow-up PDF.
```

Codex calls `ask_chatgpt` once with a stable session key. The bridge reuses that session's canonical conversation and first adopts an already-open Chrome tab whose normalized URL exactly matches it; only when none exists does it navigate a managed tab or create a background tab. It validates the destination and optional PDF, preserves existing drafts, fills an empty composer with the exact prompt, verifies the staged state, submits once, records submission, waits for verified completion evidence and stable text, and returns the response with its canonical `https://chatgpt.com/c/...` URL. Explicit operation keys are durable, and unresolved sends remain protected regardless of age. Only completed automatic content fingerprints expire after 30 minutes. Visible throttling opens a 30-minute no-send circuit breaker.

The current page's thinking mode is preserved by default. `prefer_max` requests Max with fallback; strict `max` refuses to send without verified Max. A returned answer is accepted only with `response_extraction_verified: true`; open-ended reviews can require `minimum_response_characters`, and deterministic transcription can supply exact character count, line count and SHA-256 expectations. If extraction is incomplete or the post-submission callback times out, the bridge performs one read-only recovery bound to the submitted message ID and can return `completed_after_read_recovery`; it never repeats the prompt.

## Security and data flow

1. WebContentAgent resolves the optional PDF to its real local path and enforces the configured roots, `.pdf` extension, symbolic-link boundaries and size limit.
2. A private task record is saved under the ignored runtime directory, and a token-protected loopback bridge waits on `127.0.0.1`.
3. Chrome Companion uses the Chrome DevTools Protocol only inside the requested `chatgpt.com` tab to stage the file and exact prompt.
4. The Companion verifies the destination, filename, exact prompt and enabled Send form before submitting once.
5. It confirms the submission, waits for a stable assistant response bound to that message, and returns it with the canonical URL.

Task records contain paths, prompts, status and response text only. The registry stores readable aliases and canonical URLs. Neither contains passwords, cookies, authorization headers, session storage or Chrome profile data. See [architecture and security](docs/architecture-and-security.md).

## Troubleshooting

- `extension_not_connected`: preserve the request; retry with the same key only after a verified repair and an authoritative `zero_submission_proven: true` record. A generic timeout is not proof. See [reconnect](skills/chatgpt-pdf-review/references/extension-reconnect.md).
- `submission_unconfirmed` / `timeout_after_submission` / `cancelled_submission_unknown`: the message **may already be posted**. Do not resend; use the single bound read only as the skill describes. See [submission confirmation](skills/chatgpt-pdf-review/references/submission-confirmation.md).
- `duplicate_blocked`: a matching request remains protected; never generate a fresh key to evade it.
- `throttled_cooldown`: wait until `retry_after`.
- `draft_conflict`: you have an unsent draft in that tab; it is never cleared automatically.
- `conversation_mismatch`: the loaded page differs from the requested conversation; the bridge stops and never silently remaps the session.
- `PDF outside configured allowed roots`: move the PDF into an allowed root or rerun `setup.ps1` with the roots you intend.
- Missing path under `.codex\plugins\cache\personal\chatgpt-pdf-review\<version>`: run `scripts\repair-installed-cache.ps1` once, then retry only if no message was submitted.
- Login, MFA, CAPTCHA or verification: complete it manually in Chrome. Authentication does not prove an earlier message was unsent.

More detail: [troubleshooting](docs/troubleshooting.md), [composer diagnostics and retry rules](skills/chatgpt-pdf-review/references/composer-readiness-and-retry.md), [regression acceptance](docs/regression-tests.md).

## Tests

```powershell
npm ci
npm test         # node --test: offline unit, child-process, loopback-HTTP and Companion DOM tests
npm run lint     # ESLint
```

The DOM tests load this repository's `chrome_extension\*.js` and `tests\fixtures\chatgpt-*.html` into a fresh, network-intercepted Playwright Chromium, never your Chrome profile. If Playwright's browser is missing, run `npx playwright install chromium`. The repository-level `scripts\test.ps1` runs this suite too when `node_modules` is present. The opt-in live PDF acceptance procedure in [regression acceptance](docs/regression-tests.md) sends real messages and is never part of `npm test`.

## Uninstall

```powershell
codex plugin remove chatgpt-pdf-review@personal
```

Removing the plugin does not delete WebContentAgent, Chrome authentication, the conversation registry or private task history.
