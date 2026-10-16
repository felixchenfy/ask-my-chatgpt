# Troubleshooting

## `ask_chatgpt` is missing

Run `scripts\update-installed-plugin.ps1`; it validates through the isolated UTF-8/PyYAML environment, refreshes the cachebuster with the bundled helper, reinstalls the discovered personal marketplace plugin, and repairs retained old cache directories. Confirm `.mcp.json` calls the plugin-relative `scripts\launch-mcp.cmd`, that the wrapper starts `server.mjs`, and that `server.mjs` publishes `ask_chatgpt`. The Node adapter is intentionally fast because Codex 0.150 can omit an optional MCP server that is still starting while the initial tool catalog is built.

If an already-open task reports a missing file under `.codex\plugins\cache\personal\chatgpt-pdf-review\<old-version>`, do not send through another browser route and do not assume the skill itself is invalid. Codex emptied a cache version that the task or its already-running MCP server still references. Run `scripts\repair-installed-cache.ps1` once; it restores only missing compatibility files, including the Python adapter used by an in-memory old server, and, when `CHATGPT_BRIDGE_SOURCE` is set, redirects future cached launcher starts to that stable source checkout. Retry only when the failed attempt stopped before message submission. A task whose initial MCP catalog omitted the server entirely still needs a fresh task after repair.

## `extension_not_connected`

Inspect the failed run's task record first. Require `status: extension_not_connected`, `message_submitted: false`, and `zero_submission_proven: true`. Older records without the explicit proof are insufficient. Keep normal Google Chrome running, open `chrome://extensions/`, confirm WebContentAgent Chrome Companion is enabled and loaded from `WebContentAgent\chrome_extension`, and reload it once. The displayed extension version should match `chrome_extension\manifest.json`. Ordinary requests do not require reloads.

The active Codex task should preserve the exact unsent call and ask the user only to reply `done` after reload; the user should not have to paste a recovery paragraph or restate the prompt. After `done`, retry once with the same arguments and a fresh idempotency key ending in `-after-confirmed-zero-submit`; do not use `force_send`. If the post-reload retry also cannot connect, verify its explicit zero-submission proof and stop. The complete procedure lives in `skills\chatgpt-pdf-review\references\extension-reconnect.md`.

## Login or security challenge

Complete login, MFA, CAPTCHA, verification, or recovery manually in the normal ChatGPT page. Do not place credentials in prompts or configuration, and do not copy Chrome profile data. Start a fresh bridge call after the signed-in page is normal again.

## Exact composer validation fails

Inspect the task record under `WebContentAgent\runtime\state\chatgpt-bridge` and the visible tab. Do not retry until the record and page show that no user message was submitted. ChatGPT can synchronize unsent drafts across tabs; the current companion selects and replaces the full rich-text draft before validating it.

## Submission validation fails

The bridge stopped because it did not observe the exact new user message and its stable message ID after submission. An empty composer is no longer treated as proof. Inspect the visible tab and task record; do not blindly call the tool again.

## Response timeout

Inspect the canonical conversation URL and task record first. If the user message exists, do not resend it. Automatic recovery requires that URL and `submitted_message_id`. If the result contains `automatic_read_recovery_attempted: true`, do not call the read tool again. Only an older result without that field, with a known submitted message ID, may receive one explicit compatibility read. If recovery remains unverified or encounters a security challenge, stop and report the incomplete stage.

`response_unverified` means the visible assistant text never exposed reliable completion evidence or still looked like a truncated natural-language fragment after the extended verification window. It is deliberately not a successful extraction. Read-only recovery must observe the latest turn's own Copy action, not merely an assistant node. For requests such as “at least 900 characters,” pass `minimum_response_characters`; a shorter prefix cannot then be verified. `completed_after_read_recovery` is successful only when `response_extraction_verified: true`. Never resend the original prompt merely to obtain the missing suffix.

For exact-copy tasks, pass `expected_response_characters`, `expected_response_lines`, and `expected_response_sha256`. Counts use Unicode codepoints; emoji count as one, excluding normalized newline separators. Any mismatch is returned as `response_unverified`; never force-resend automatically.

Recovery of a particular send requires `submitted_message_id` passed as `expected_message_id`. Without it, an older answer could look complete, so compatibility recovery must stop. A standalone request to read the latest answer is different and may omit this field.

`message_submitted: false` alone is not retry evidence. Require `zero_submission_proven: true` from the current bridge. `cancelled_submission_unknown` means a submit permit had already been granted: no automatic resend is safe.

## Max thinking cannot be verified

The skill passes `thinking_mode: "current"` by default. The companion attempts Max only when the user asks for `prefer_max` or strict `max`. This avoids spending higher-compute turns unintentionally.

## ChatGPT reports throttling

The companion recognizes visible usage/rate-limit alerts and returns `throttled` without resending. The local bridge then returns `throttled_cooldown` until the 30-minute cooldown expires and does not open Chrome. Do not use `force_send` unless the user explicitly requests a resend despite that safeguard.

## PDF upload fails

Confirm the resolved file is a regular `.pdf`, lies under an `allowedPdfRoots` entry in `%USERPROFILE%\.codex\chatgpt-pdf-review\config.json`, and is below `maximumPdfSizeMb`. If the ChatGPT file input changed, update WebContentAgent Chrome Companion and its regression tests, then reload the unpacked extension once.
