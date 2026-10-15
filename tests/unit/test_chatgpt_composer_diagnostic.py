import json
import runpy
from pathlib import Path

import pytest

from web_content_agent.chatgpt_chrome_bridge import ChatGptChromeBridge

Diagnostic = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts/inspect-chatgpt-composer.py")
)["ComposerDiagnosticBridge"]


def failed(root):
    bridge = ChatGptChromeBridge(root)
    rid = bridge.start_message(
        "private question",
        conversation_url="https://chatgpt.com/c/example",
        session_key="diagnostic-test",
        idempotency_key="diagnostic-test",
    )
    bridge.claim_command()
    bridge.complete(rid, {"ok": False, "status": "failed", "error": "draft"})
    return bridge, rid


def test_diagnostic_is_single_claim_no_send_no_answer_read_and_no_state_edit(tmp_path):
    bridge, rid = failed(tmp_path)
    record = bridge.record_path(rid).read_bytes()
    shared = bridge.shared_state_path.read_bytes()
    diagnostic = Diagnostic(rid, tmp_path)
    command = diagnostic.claim_command(extension_version="test", worker_instance_id="worker")
    assert command["action"] == "inspect_chatgpt_composer"
    assert not {"question", "attachment_path", "expected_message_id"}.intersection(command)
    assert diagnostic.claim_command()["action"] == "idle"
    assert not diagnostic.begin_submission(command["run_id"])
    assert not diagnostic.begin_submission(rid)
    assert not diagnostic.observe("wrong", {})
    assert diagnostic.observe(
        command["run_id"],
        {
            "tabId": 17,
            "selectedFileCount": 1,
            "composerDiagnostic": {
                "formPresent": False,
                "selectedFilename": "x" * 1000,
                "draftText": "private",
            },
            "pageBody": "private",
        },
    )
    assert not diagnostic.observe(command["run_id"], {})
    evidence = diagnostic.report["last_observation"]
    assert evidence["tabId"] == 17
    assert len(evidence["composerDiagnostic"]["selectedFilename"]) == 256
    assert "private" not in json.dumps(diagnostic.report)
    assert bridge.record_path(rid).read_bytes() == record
    assert bridge.shared_state_path.read_bytes() == shared


def test_changed_request_or_send_evidence_rejected_before_dispatch(tmp_path):
    bridge, rid = failed(tmp_path)
    diagnostic = Diagnostic(rid, tmp_path)
    bridge._update_record(rid, submission_authorized=True)
    with pytest.raises(ValueError, match="zero-submission"):
        diagnostic.claim_command()
    assert not diagnostic.dispatched
    with pytest.raises(ValueError, match="zero-submission"):
        Diagnostic(rid, tmp_path)


def test_invalid_run_id_rejected(tmp_path):
    with pytest.raises(ValueError, match="run ID"):
        Diagnostic("../other", tmp_path)
