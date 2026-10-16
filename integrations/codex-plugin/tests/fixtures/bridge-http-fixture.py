"""Isolated real bridge protocol; ephemeral port, synthetic token, no Chrome launch."""

import hashlib
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import web_content_agent.chatgpt_chrome_bridge as bridge_module

bridge_module.BRIDGE_PORT = 0
bridge_module.load_pairing_token = lambda: "offline-fixture-token"
with tempfile.TemporaryDirectory(prefix="bridge-contract-") as directory:
    bridge = bridge_module.ChatGptChromeBridge(Path(directory))
    scenario = sys.argv[2] if len(sys.argv) > 2 else ""
    attachment = None
    if scenario.startswith("accessible-pdf-resume") or scenario == "accessible-pdf-upload":
        pdf = Path(directory) / "review.pdf"
        pdf.write_bytes(b"%PDF-1.4\nOffline attachment fixture\n")
        # Resolve like the production policy does: CI temp dirs can be 8.3 short paths.
        bridge_module._attachment_policy = lambda: ([Path(directory).resolve()], 1024)
        attachment = str(pdf)
    if scenario.startswith("accessible-pdf-resume"):
        parent = bridge.start_message(
            "Review this exact question.",
            attachment_path=attachment,
            conversation_url="https://chatgpt.com/c/offline-contract",
            idempotency_key="offline-operation",
            session_key="offline-session",
            response_timeout_seconds=90,
        )
        bridge.claim_command()
        parent_observation = {"phase": "attachment_staging"}
        if scenario == "accessible-pdf-resume-pending":
            parent_observation.update(
                composerDiagnostic={"composerCharacterCount": 0, "draftUploadInProgress": True},
                attachment={
                    "state": "unexpected",
                    "ready": False,
                    "cardCount": 1,
                    "expectedCardCount": 0,
                    "expectedFilename": "review.pdf",
                    "observedFilename": "",
                    "filenameMatch": "mismatch",
                },
            )
        bridge.observe(parent, parent_observation)
        bridge.complete(
            parent,
            {
                "ok": False,
                "status": "failed",
                "error": "ChatGPT attachment staging is unexpected"
                if scenario == "accessible-pdf-resume-pending"
                else "ChatGPT did not finish staging review.pdf",
            },
        )
        diagnostic = {
            "failed_run_id": parent,
            "failed_record_unchanged": True,
            "failed_record_sha256": hashlib.sha256(
                bridge.record_path(parent).read_bytes()
            ).hexdigest(),
            "attachment_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
            "observed_at": bridge_module._now().isoformat(),
            "send_tasks_queued": 0,
            "response_reads_queued": 0,
            "last_observation": {
                "phase": "composer_diagnostic",
                "tabId": 2 if scenario.endswith("wrong-tab") else 1,
                "tabSource": "existing" if "restored" in scenario else "managed",
                "composerDiagnostic": {
                    "matchingTabCount": 1,
                    "composerCharacterCount": 0,
                    "draftUploadInProgress": False,
                },
                "attachment": {
                    "state": "ready",
                    "ready": True,
                    "visible": True,
                    "filenameMatch": "exact",
                    "cardCount": 1,
                    "expectedCardCount": 1,
                    "expectedFilename": "review.pdf",
                    "observedFilename": "review.pdf",
                },
            },
        }
        bridge.record_path(f"inspect-{parent}").write_text(json.dumps(diagnostic), encoding="utf-8")
    if scenario.startswith("accessible-resume"):
        parent = bridge.start_message(
            "Review this exact question.",
            conversation_url="https://chatgpt.com/c/offline-contract",
            idempotency_key="offline-operation",
            session_key="offline-session",
            response_timeout_seconds=90,
        )
        bridge.claim_command()
        bridge.observe(
            parent,
            {
                "phase": "composer_input_verification"
                if scenario == "accessible-resume-send"
                else "composer_ready",
                "tabId": 1,
                "matches": True,
            },
        )
        bridge.complete(
            parent,
            {
                "ok": False,
                "status": "failed",
                "error": "ChatGPT Send button is not ready"
                if scenario == "accessible-resume-send"
                else "ChatGPT composer does not contain the exact queued question",
            },
        )
    run = bridge.start_message(
        (
            "Review " + " ".join(f"`token{i}`" for i in range(12)) + "."
            if scenario == "accessible-inline-code"
            else "Review `alpha` plus `beta` and `gamma`."
            if scenario == "inline-code"
            else "Review this exact question."
        ),
        conversation_url="https://chatgpt.com/c/offline-contract",
        attachment_path=attachment,
        idempotency_key="offline-operation",
        session_key="offline-session",
        thinking_mode=(
            "prefer_max"
            if len(sys.argv) > 2 and sys.argv[2] == "thinking-preference"
            else "max"
            if len(sys.argv) > 2 and sys.argv[2].startswith("thinking-")
            else "current"
        ),
        response_timeout_seconds=90,
    )
    server = bridge_module.ChatGptBridgeServer(bridge)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(json.dumps({"port": server.server_port, "command": bridge.claim_command()}), flush=True)
    if not bridge._completed.wait(45):
        bridge.cancel_active()
    print(json.dumps(bridge._result), flush=True)
    server.shutdown()
    server.server_close()
