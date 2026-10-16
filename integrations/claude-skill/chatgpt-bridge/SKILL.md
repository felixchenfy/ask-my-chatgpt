---
name: chatgpt-bridge
description: Send a message to the user's signed-in ChatGPT (a specific chatgpt.com conversation or a new one) and bring the verified reply back, via the local WebContentAgent Chrome Companion bridge. Use when the user asks to "send to GPT", "ask ChatGPT", discuss/review something with a GPT session, continue a GPT conversation, or read a GPT reply. Also use to recover a reply after a failed send. Not for unrelated web browsing.
---

# ChatGPT bridge (Claude Code)

Every `ask` sends a real message from the user's ChatGPT account. Send only content the
user asked to share (or that a standing consent the user has given you covers). Consent
never relaxes duplicate protection or submission verification.

## How it works

`scripts/gpt.py` → `integrations/codex-plugin/scripts/ask-chatgpt-adapter.py` (run in the
WebContentAgent venv) → token-protected loopback bridge → **Chrome Companion** extension
in the user's normal signed-in Chrome → the real `chatgpt.com` tab. It types the exact
prompt, submits **once**, waits for the completed reply bound to that submitted message,
and returns it. No OpenAI API, cookies or passwords. This is the same production path the
Codex `chatgpt_pdf_review` plugin uses; Codex and Claude sessions share it.

Prerequisite: Chrome open with WebContentAgent Chrome Companion loaded and ChatGPT signed in.
`gpt.py` finds the WebContentAgent checkout that contains this skill (also through a symlink
or junction); for a plain copy, set `WCA_PROJECT_ROOT` (optionally `WCA_PYTHON_EXECUTABLE`,
`CHATGPT_BRIDGE_SOURCE`). See README.md next to this file.
Only one bridge operation runs at a time machine-wide (others get `bridge_busy`).

## Send

Run with the Bash tool (any Python 3.10+; the script launches the venv itself).
Use `run_in_background: true` for long answers (GPT can take several minutes):

```bash
python ~/.claude/skills/chatgpt-bridge/scripts/gpt.py ask --to https://chatgpt.com/c/<id> --name <alias> --message "..."
python ~/.claude/skills/chatgpt-bridge/scripts/gpt.py ask --to <alias> --file /path/to/prompt.md
python ~/.claude/skills/chatgpt-bridge/scripts/gpt.py ask --new --name <alias> --message "..."
python ~/.claude/skills/chatgpt-bridge/scripts/gpt.py list
```

- Long or multi-line messages: write them to a UTF-8 file (scratchpad) and use `--file`.
- `--name <alias>` saves the canonical URL in `~/.claude/chatgpt-bridge/conversations.json`
  so later calls can use `--to <alias>`. `list` also shows Codex's registry (read-only).
- Optional: `--attach <pdf>` (must be under an `allowedPdfRoots` entry of the PDF policy,
  `CHATGPT_PDF_REVIEW_CONFIG` or `~/.codex/chatgpt-pdf-review/config.json`),
  `--thinking prefer_max` when the user asks for max thinking, `--min-chars N` for a required minimum reply length, `--timeout` (≤3600 s).
- stdout = the verified reply text; stderr = one `[gpt] {...}` status line incl. the
  receipt path. Exit 0 = verified reply; 2 = not verified/failed. `--json` prints the full result.
- Full request/response receipts: `~/.claude/chatgpt-bridge/receipts/`.

## Safety rules (never break these)

1. **One send per message.** The idempotency key defaults to a hash of session+message,
   so re-running the identical command is safe (it returns the cached result or
   `duplicate_blocked`; it does not post twice). Never pass a new `--key` or tweak the
   text just to get around a block.
2. `submission_unconfirmed`, `timeout_after_submission`, `cancelled_submission_unknown`,
   or `message_submitted: true` without a verified reply → the message **may already be
   posted. Do not resend.** If a `submitted_message_id` exists and
   `automatic_read_recovery_attempted` is false, do exactly one read:
   `gpt.py read --to <alias|url> --expected-message-id <id>`. Never ask GPT to repeat itself.
3. Only retry a send (same command, same key) when the result says `zero_submission_proven: true`.
4. `throttled_cooldown`: wait until `retry_after`. `draft_conflict`: the user has a draft in
   that tab; never clear it.
5. Don't fall back to driving chatgpt.com with browser tools.
6. Report the reply as GPT's only when `verified: true`. Don't claim GPT reviewed
   something it never answered.

## When communication breaks

If the bridge fails for any operational reason (adapter error, `extension_not_connected`,
`bridge_busy` that persists, composer/readiness errors, unverified extraction after the
allowed read, crashes, unexpected output), **stop and report; do not resend.** Tell the user
the exact command/arguments (incl. idempotency key), the `[gpt]` status line, the receipt
path, and whether `message_submitted` / `zero_submission_proven` were reported. Do not paste
cookies or tokens. If the user has designated a maintenance session for the bridge, you may
hand the same report to it instead and wait for its reply.

Keep the original request and key. After a verified fix, retry with the same command only if
zero submission was proven; otherwise do the single bound read. To diagnose, use the
WebContentAgent checkout (bridge: `src/web_content_agent/chatgpt_chrome_bridge.py`,
extension: `chrome_extension/`, task records: `runtime/state/chatgpt-bridge/`) and
`integrations/codex-plugin/` (adapter, `docs/troubleshooting.md`, and the Codex skill's
`skills/chatgpt-pdf-review/references/` for detailed recovery rules).
