"""Inspect an existing failed request's composer; never queue a send or response read."""

import argparse
import hashlib
import json
import re
import threading
from pathlib import Path

from web_content_agent.chatgpt_chrome_bridge import (
    ChatGptBridgeServer,
    ChatGptChromeBridge,
    _bounded_observation,
    _canonical_conversation_url,
    _now,
    _validate_attachment_path,
)
from web_content_agent.chrome_companion import negotiate_companion


class ComposerDiagnosticBridge(ChatGptChromeBridge):
    def __init__(self, failed_run_id: str, state_root: Path | None = None):
        super().__init__(state_root)
        if not re.fullmatch(r"chatgpt-\d{8}-\d{6}-[0-9a-f]{6}", failed_run_id):
            raise ValueError("Expected a failed send run ID")
        self.failed_run_id = failed_run_id
        self.diagnostic_id = f"inspect-{failed_run_id}"
        self.done = threading.Event()
        self.report = None
        self.dispatched = False
        self._diagnostic_command()  # Validate before opening the listener.

    def _diagnostic_command(self):
        record = self._read_record(self.failed_run_id)
        state = json.loads(self.shared_state_path.read_text(encoding="utf-8"))
        key = record.get("idempotency_key")
        previous = state.get("requests", {}).get(f"explicit:{key}", {})
        if previous.get("run_id") != self.failed_run_id or not self._retryable_zero_submission(
            previous
        ):
            raise ValueError("Diagnostic requires the current terminal zero-submission request")
        destination = _canonical_conversation_url(record.get("conversation_url"))
        if not destination or not record.get("session_key"):
            raise ValueError("Diagnostic requires an existing conversation and session")
        return {
            "action": "inspect_chatgpt_composer",
            "run_id": self.diagnostic_id,
            "conversation_url": destination,
            "session_key": record["session_key"],
            "attachment_filename": record.get("attachment_filename") or "",
        }

    def claim_command(
        self,
        extension_version="",
        worker_instance_id="",
        protocol_version=None,
        expected_version=None,
    ):
        with self._lock:
            if expected_version is not None:
                decision = negotiate_companion(
                    observed_version=extension_version,
                    observed_protocol=protocol_version,
                    expected_version=expected_version,
                )
                if not decision["compatible"]:
                    return decision
            if self.dispatched:
                return {"action": "idle"}
            command = self._diagnostic_command()
            self.dispatched = True
            self.companion = {
                "extension_version": extension_version,
                "worker_instance_id": worker_instance_id,
            }
            return command

    def observe(self, run_id, observation):
        with self._lock:
            if run_id != self.diagnostic_id or not self.dispatched or self.done.is_set():
                return False
            self.report = {
                "failed_run_id": self.failed_run_id,
                "companion": self.companion,
                "last_observation": _bounded_observation(observation),
                "send_tasks_queued": 0,
                "response_reads_queued": 0,
            }
            self.done.set()
            return True

    # The normal command slot remains None. Inherited submission/event/complete
    # handlers cannot grant a permit or touch a request; diagnostics use observe.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failed-run-id", required=True)
    args = parser.parse_args()
    bridge = ComposerDiagnosticBridge(args.failed_run_id)
    original = bridge.record_path(args.failed_run_id).read_bytes()
    server = ChatGptBridgeServer(bridge)  # Refuse a busy listener; never interrupt it.
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        if not bridge.done.wait(150):
            raise TimeoutError("Companion did not return composer diagnostics; no send was queued")
        assert bridge._command is None
        report = dict(bridge.report)
        report["observed_at"] = _now().isoformat()
        report["failed_record_unchanged"] = (
            bridge.record_path(args.failed_run_id).read_bytes() == original
        )
        report["failed_record_sha256"] = hashlib.sha256(original).hexdigest()
        attachment, _ = _validate_attachment_path(json.loads(original).get("attachment_path"))
        if attachment:
            report["attachment_sha256"] = hashlib.sha256(Path(attachment).read_bytes()).hexdigest()
        path = bridge.state_root / f"{bridge.diagnostic_id}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"diagnostic_file": str(path), **report}, ensure_ascii=True))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
