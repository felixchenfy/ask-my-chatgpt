from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

sys.stdin.reconfigure(encoding="utf-8", errors="strict")
sys.stdout.reconfigure(encoding="utf-8", errors="strict")
sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

WEB_CONTENT_AGENT_ROOT = Path(os.environ["WCA_PROJECT_ROOT"])
sys.path.insert(0, str(WEB_CONTENT_AGENT_ROOT / "src"))

from web_content_agent.chatgpt_chrome_bridge import (  # noqa: E402
    _bridge,
    ask_chatgpt_data,
    read_chatgpt_conversation_data,
)


def main() -> int:
    request = json.loads(sys.stdin.readline())
    cancel_event = threading.Event()

    def watch_cancellation() -> None:
        for line in sys.stdin:
            if json.loads(line).get("action") == "cancel":
                break
        cancel_event.set()
        _bridge.cancel_active()

    if request.get("_cancel_protocol") == 1:
        threading.Thread(target=watch_cancellation, daemon=True).start()
    if request.get("action", "ask_chatgpt") == "read_chatgpt_conversation":
        result = read_chatgpt_conversation_data(
            conversation_url=request["conversation_url"],
            timeout_seconds=request.get("timeout_seconds", 120),
            minimum_response_characters=request.get("minimum_response_characters"),
            expected_response_characters=request.get("expected_response_characters"),
            expected_response_lines=request.get("expected_response_lines"),
            expected_response_sha256=request.get("expected_response_sha256"),
            expected_message_id=request.get("expected_message_id"),
            cancel_event=cancel_event,
        )
    else:
        result = ask_chatgpt_data(
            question=request["question"],
            conversation_url=request.get("conversation_url"),
            attachment_path=request.get("attachment_path"),
            timeout_seconds=request.get("timeout_seconds", 900),
            thinking_mode=request.get("thinking_mode", "current"),
            session_key=request.get("session_key"),
            idempotency_key=request.get("idempotency_key"),
            new_conversation=request.get("new_conversation", False),
            force_send=request.get("force_send", False),
            minimum_response_characters=request.get("minimum_response_characters"),
            expected_response_characters=request.get("expected_response_characters"),
            expected_response_lines=request.get("expected_response_lines"),
            expected_response_sha256=request.get("expected_response_sha256"),
            cancel_event=cancel_event,
        )
    result["adapter_protocol_version"] = 2
    result["cancellation_supported"] = request.get("_cancel_protocol") == 1
    json.dump(result, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # keep MCP stdout JSON-only
        json.dump(
            {"error": type(exc).__name__, "message": str(exc)},
            sys.stderr,
            ensure_ascii=False,
        )
        raise SystemExit(1) from exc
