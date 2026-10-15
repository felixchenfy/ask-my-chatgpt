"""Negotiate the real Companion version without queueing a send or read."""

import json
import threading

from web_content_agent.chatgpt_chrome_bridge import ChatGptBridgeServer, ChatGptChromeBridge
from web_content_agent.chrome_companion import read_companion_extension_version


def main():
    ready = threading.Event()
    seen = []

    class ObservedBridge(ChatGptChromeBridge):
        def claim_command(self, **metadata):
            decision = super().claim_command(**metadata)
            seen.append(
                {
                    "version": metadata.get("extension_version"),
                    "action": decision["action"],
                }
            )
            if (
                decision["action"] == "idle"
                and metadata.get("extension_version") == read_companion_extension_version()
            ):
                ready.set()
            return decision

    bridge = ObservedBridge()
    # Binding fails if another request owns the port; never interrupt that owner.
    server = ChatGptBridgeServer(bridge)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        matched = ready.wait(75)
        assert bridge._command is None
        print(
            json.dumps(
                {"compatible_worker_observed": matched, "observations": seen, "tasks_queued": 0}
            )
        )
        return 0 if matched else 1
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
