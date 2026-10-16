# chatgpt-bridge skill for Claude Code

A [Claude Code skill](https://docs.claude.com/en/docs/claude-code/skills) that lets Claude send a message to your signed-in ChatGPT (an existing `chatgpt.com/c/...` conversation or a new one) and bring the verified reply back. `SKILL.md` holds the instructions Claude follows; `scripts/gpt.py` is a small CLI over the same adapter the [Codex plugin](../../codex-plugin/README.md) uses:

```text
gpt.py -> integrations/codex-plugin/scripts/ask-chatgpt-adapter.py (WebContentAgent venv)
       -> token-protected 127.0.0.1 bridge -> Chrome Companion -> your chatgpt.com tab
```

> **It sends real messages.** `gpt.py ask` types and submits one message in your ChatGPT account. `gpt.py read` and `gpt.py list` never send.

## Prerequisites

- This repository set up per the [root README](../../../README.md): `uv sync` (so `.venv` exists) and the unpacked Chrome Companion from `chrome_extension/` loaded in Chrome, with ChatGPT signed in.
- The Codex plugin's adapter at `integrations/codex-plugin/scripts/ask-chatgpt-adapter.py` (part of this checkout; its Node dependencies are not needed by `gpt.py`).
- Any Python 3.10+ on `PATH` to start `gpt.py`; it launches the WebContentAgent venv itself.
- For `--attach`, a PDF policy file (`~/.codex/chatgpt-pdf-review/config.json` or `CHATGPT_PDF_REVIEW_CONFIG`), e.g. written by `integrations/codex-plugin/scripts/setup.ps1`.

## Install

Claude Code loads personal skills from `~/.claude/skills/<name>/`. Link this folder there so it keeps resolving the checkout it lives in (PowerShell, from the repository root):

```powershell
# A junction needs no administrator rights or Developer Mode.
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\chatgpt-bridge" `
  -Target (Resolve-Path .\integrations\claude-skill\chatgpt-bridge)
```

On macOS/Linux use `ln -s "$PWD/integrations/claude-skill/chatgpt-bridge" ~/.claude/skills/chatgpt-bridge`. Alternatively copy the folder; a copy is outside the repository, so tell `gpt.py` where the checkout is.

## Path resolution and overrides

| Variable | Meaning | Default |
| --- | --- | --- |
| `WCA_PROJECT_ROOT` | WebContentAgent checkout | The repository containing this skill (`../../..` from the skill folder, after resolving links) |
| `CHATGPT_BRIDGE_SOURCE` | Plugin directory containing `scripts/ask-chatgpt-adapter.py` | `../../codex-plugin` next to this skill, else `<WCA_PROJECT_ROOT>/integrations/codex-plugin` |
| `WCA_PYTHON_EXECUTABLE` | Python used for the adapter | `<WCA_PROJECT_ROOT>/.venv/Scripts/python.exe` (`.venv/bin/python` elsewhere) |
| `CHATGPT_PDF_REVIEW_CONFIG` | PDF allowlist policy read by the bridge | `~/.codex/chatgpt-pdf-review/config.json` |

For a copied skill, set at least `WCA_PROJECT_ROOT`, for example in `~/.claude/settings.json` under `"env"`.

## State

`gpt.py` keeps its alias registry at `~/.claude/chatgpt-bridge/conversations.json` and full request/response receipts in `~/.claude/chatgpt-bridge/receipts/`. It reads the Codex plugin's registry (`~/.codex/chatgpt-pdf-review/conversations.json`) without writing it. The bridge's private task records stay under the checkout's ignored `runtime/state/chatgpt-bridge/`. None of these are committed.

## Usage

See `SKILL.md`. In short:

```bash
python ~/.claude/skills/chatgpt-bridge/scripts/gpt.py ask --new --name my-topic --message "..."
python ~/.claude/skills/chatgpt-bridge/scripts/gpt.py ask --to my-topic --file prompt.md
python ~/.claude/skills/chatgpt-bridge/scripts/gpt.py read --to my-topic --expected-message-id <id>
python ~/.claude/skills/chatgpt-bridge/scripts/gpt.py list
```

The safety rules in `SKILL.md` are the contract: send once; never resend after `submission_unconfirmed`, `timeout_after_submission` or `cancelled_submission_unknown`; recover with a single `read --expected-message-id`; retry a send with the same key only when `zero_submission_proven: true`; never fall back to browser automation; report a reply as GPT's only when verified.
