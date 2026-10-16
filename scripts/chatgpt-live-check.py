"""Opt-in live acceptance check for the ChatGPT bridge over real MCP stdio.

This SENDS synthetic messages to your signed-in ChatGPT account through Chrome.
It is never run by the offline test suite or CI. It speaks to `wca-mcp` exactly as
an MCP client such as Codex or Claude Code would: initialize, list tools, call tools.

Steps (each prints one JSON line; stops at the first failure, never resends):
  1. ask_chatgpt in a new conversation, expecting a known token in the reply.
  2. ask_chatgpt again with the returned conversation_url (follow-up in the same chat).
  3. read_chatgpt_conversation for that URL and expected message id (read-only).

Usage (PowerShell, from the checkout):
  uv run python scripts/chatgpt-live-check.py --confirm-send
  uv run python scripts/chatgpt-live-check.py --confirm-send --conversation-url https://chatgpt.com/c/...

Conversation URLs are private: the script prints them only with --show-url.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
import time
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from web_content_agent.paths import project_root

REQUIRED_TOOLS = {"ask_chatgpt", "read_chatgpt_conversation"}


def _payload(result) -> dict:
    if result.isError:
        return {"ok": False, "status": "tool_error", "error": result.content[0].text[:500]}
    return json.loads(result.content[0].text)


def _report(step: str, payload: dict, started: float, show_url: bool) -> None:
    url = str(payload.get("conversation_url", ""))
    print(
        json.dumps(
            {
                "step": step,
                "ok": bool(payload.get("ok")),
                "status": payload.get("status"),
                "seconds": round(time.monotonic() - started, 1),
                "response_preview": str(payload.get("response_text", ""))[:200],
                "conversation_url": url if show_url else ("<hidden>" if url else ""),
                "message_submitted": payload.get("message_submitted"),
                "error": payload.get("error"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


async def run(args: argparse.Namespace) -> int:
    marker = f"WCA_LIVE_{secrets.token_hex(3).upper()}"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "web_content_agent.mcp.server"],
        cwd=str(project_root()),
        env=dict(os.environ),
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(
            read_stream, write_stream, read_timeout_seconds=timedelta(seconds=args.timeout + 120)
        ) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            missing = REQUIRED_TOOLS - names
            print(json.dumps({"step": "list_tools", "ok": not missing, "missing": sorted(missing)}))
            if missing:
                return 2

            started = time.monotonic()
            first = _payload(
                await session.call_tool(
                    "ask_chatgpt",
                    {
                        "question": (
                            "This is an automated connectivity check from WebContentAgent. "
                            f"Reply with exactly this token and nothing else: {marker}"
                        ),
                        "conversation_url": args.conversation_url,
                        "thinking_mode": "current",
                        "timeout_seconds": args.timeout,
                    },
                )
            )
            _report(
                "ask_new" if not args.conversation_url else "ask_existing",
                first,
                started,
                args.show_url,
            )
            if not first.get("ok") or marker not in str(first.get("response_text", "")):
                print("First exchange did not complete; inspect the task record. Not resending.")
                return 1
            url = first.get("conversation_url")

            started = time.monotonic()
            follow = _payload(
                await session.call_tool(
                    "ask_chatgpt",
                    {
                        "question": (
                            "Follow-up connectivity check. Repeat the token from my previous "
                            "message, followed by the word CONTINUED."
                        ),
                        "conversation_url": url,
                        "thinking_mode": "current",
                        "timeout_seconds": args.timeout,
                    },
                )
            )
            _report("ask_follow_up", follow, started, args.show_url)
            same_chat = follow.get("conversation_url") == url
            if not follow.get("ok") or marker not in str(follow.get("response_text", "")):
                print("Follow-up did not complete with the expected token. Not resending.")
                return 1
            if not same_chat:
                print("Follow-up landed in a different conversation.")
                return 1

            started = time.monotonic()
            read = _payload(
                await session.call_tool(
                    "read_chatgpt_conversation",
                    {
                        "conversation_url": url,
                        "timeout_seconds": 120,
                        "expected_message_id": follow.get("submitted_message_id") or None,
                    },
                )
            )
            _report("read_existing", read, started, args.show_url)
            if not read.get("ok") or "CONTINUED" not in str(read.get("response_text", "")):
                return 1
    print(json.dumps({"result": "passed", "marker": marker}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--confirm-send",
        action="store_true",
        help="Required. Acknowledge that synthetic messages will be sent to your ChatGPT account.",
    )
    parser.add_argument("--conversation-url", default=None, help="Continue an existing chat")
    parser.add_argument("--timeout", type=int, default=600, help="Seconds per send (max 3600)")
    parser.add_argument("--show-url", action="store_true", help="Print conversation URLs")
    args = parser.parse_args()
    if not args.confirm_send:
        parser.error("refusing to send without --confirm-send")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
