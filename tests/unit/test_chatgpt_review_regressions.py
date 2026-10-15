from __future__ import annotations

import http.client
import json
import secrets
import threading
from http.server import ThreadingHTTPServer

import pytest

from web_content_agent.chatgpt_chrome_bridge import (
    ChatGptBridgeHandler,
    ChatGptChromeBridge,
    _response_metrics,
)
from web_content_agent.chatgpt_pairing import load_pairing_token, prepare_pairing


def test_claim_at_timeout_boundary_is_not_reported_as_zero_submission(tmp_path, monkeypatch):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("PDF review")

    def expired_wait(_timeout):
        bridge.claim_command(worker_instance_id="race-worker")
        assert bridge.begin_submission(run_id)
        bridge.mark_submitted(run_id, "https://chatgpt.com/c/race", "message-1")
        bridge.complete(
            run_id,
            {
                "ok": True,
                "status": "completed",
                "response_text": "Done.",
                "conversation_url": "https://chatgpt.com/c/race",
                "response_extraction_verified": True,
            },
        )
        return False

    monkeypatch.setattr(bridge._claimed_event, "wait", expired_wait)
    assert bridge.wait(0.01)["ok"]
    record = json.loads(bridge.record_path(run_id).read_text())
    assert record["status"] == "completed"
    assert record["message_submitted"] is True
    assert record["zero_submission_proven"] is False


def test_timeout_revokes_command_and_proves_zero_only_if_never_claimed(tmp_path):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("PDF review")
    with pytest.raises(TimeoutError):
        bridge.wait(0.001)
    record = json.loads(bridge.record_path(run_id).read_text())
    assert record["zero_submission_proven"] is True
    assert bridge.claim_command() == {"action": "idle"}
    assert not bridge.begin_submission(run_id)


@pytest.mark.parametrize("permit", [False, True])
def test_cancel_revokes_new_submit_permits_and_preserves_ambiguity(tmp_path, permit):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Cancel this PDF")
    bridge.claim_command()
    if permit:
        assert bridge.begin_submission(run_id)
    bridge.cancel_active()
    assert not bridge.begin_submission(run_id)
    result = bridge.wait(0.01)
    assert result["zero_submission_proven"] is (not permit)
    assert result["status"] == (
        "cancelled_submission_unknown" if permit else "cancelled_before_submission"
    )


def test_submit_permit_is_single_use_and_read_only_runs_cannot_obtain_it(tmp_path):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Single send")
    bridge.claim_command()
    assert bridge.begin_submission(run_id)
    assert not bridge.begin_submission(run_id)
    bridge.cancel_active()
    bridge.cancel_event.clear()
    read_id = bridge.start_read("https://chatgpt.com/c/read", expected_message_id="message-1")
    assert not bridge.begin_submission(read_id)


def test_restart_recovery_carries_original_message_identity(tmp_path):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("New PDF", conversation_url="https://chatgpt.com/c/review")
    bridge.claim_command(worker_instance_id="before")
    bridge.mark_submitted(run_id, "https://chatgpt.com/c/review", "message-new-pdf")
    recovered = bridge.claim_command(worker_instance_id="after")
    assert recovered["expected_message_id"] == "message-new-pdf"
    assert recovered["action"] == "read_chatgpt_conversation"


@pytest.mark.parametrize(
    "provisional",
    [
        "https://chatgpt.com/c/WEB:pending",
        "https://chatgpt.com/c/local-chatgpt%3A00000000-0000-4000-8000-000000000001",
    ],
)
def test_provisional_submission_keeps_identity_until_canonical_resolution(tmp_path, provisional):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("New PDF", session_key="provisional-test")
    bridge.claim_command()
    assert bridge.begin_submission(run_id)
    bridge.mark_submitted(run_id, provisional, "original-user")
    record = json.loads(bridge.record_path(run_id).read_text())
    assert record["message_submitted"]
    assert record["submitted_message_id"] == "original-user"
    assert not bridge.begin_submission(run_id)
    with bridge._shared_state() as state:
        assert "provisional-test" not in state.get("sessions", {})
    bridge.mark_submitted(run_id, "https://chatgpt.com/c/resolved", "original-user")
    with bridge._shared_state() as state:
        assert state["sessions"]["provisional-test"]["conversation_url"].endswith("/c/resolved")
    assert (
        json.loads(bridge.record_path(run_id).read_text())["submitted_message_id"]
        == "original-user"
    )


@pytest.mark.parametrize(
    "provisional",
    [
        "https://chatgpt.com/c/WEB:pending",
        "https://chatgpt.com/c/local-chatgpt%3A00000000-0000-4000-8000-000000000001",
    ],
)
def test_complete_cannot_accept_a_provisional_url_as_success(tmp_path, provisional):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("New PDF")
    bridge.claim_command()
    bridge.mark_submitted(run_id, provisional, "original-user")
    bridge.complete(
        run_id,
        {
            "ok": True,
            "status": "completed",
            "response_text": "A complete review.",
            "conversation_url": provisional,
            "response_extraction_verified": True,
        },
    )
    result = bridge.wait(0.01)
    assert not result["ok"]
    assert result["status"] == "response_unverified"
    assert result["message_submitted"]
    assert result["submitted_message_id"] == "original-user"


def test_pairing_is_random_per_install_and_stable_across_reinstall(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    prepare_pairing(first)
    token = load_pairing_token(first)
    prepare_pairing(first)
    assert load_pairing_token(first) == token
    prepare_pairing(second)
    assert load_pairing_token(second) != token
    assert token in (first / "chrome_extension/chatgpt-pairing.js").read_text()


def test_http_rejects_foreign_origins_and_wrong_tokens_without_claiming(tmp_path):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Private test question")
    server = ThreadingHTTPServer(("127.0.0.1", 0), ChatGptBridgeHandler)
    server.bridge = bridge
    server.pairing_token = secrets.token_hex(32)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
    try:
        for method, route in [("OPTIONS", "/command"), ("GET", "/command"), ("POST", "/complete")]:
            conn.request(
                method,
                route,
                body=json.dumps({"run_id": run_id, "ok": True}),
                headers={
                    "Origin": "https://untrusted.example",
                    "X-WCA-Token": server.pairing_token,
                },
            )
            response = conn.getresponse()
            assert response.status == 403
            assert response.getheader("Access-Control-Allow-Origin") is None
            response.read()
        conn.request("POST", "/complete", body="{}", headers={"X-WCA-Token": "old-public-token"})
        response = conn.getresponse()
        assert response.status == 403
        response.read()
        assert not bridge._claimed
        assert bridge.claim_command()["run_id"] == run_id
        conn.request(
            "POST",
            "/before-submit",
            body=json.dumps({"run_id": run_id}),
            headers={
                "X-WCA-Token": server.pairing_token,
                "Origin": "chrome-extension://" + "a" * 32,
            },
        )
        response = conn.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["ok"] is True
    finally:
        conn.close()
        server.shutdown()
        server.server_close()
        thread.join()


def test_unicode_metrics_count_codepoints_and_normalize_cr():
    assert _response_metrics("😀")[1:3] == (1, 1)
    assert _response_metrics("abc\rdef")[0:3] == ("abc\ndef", 6, 2)


def test_submission_observation_survives_failure_without_promoting_candidate(tmp_path):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("PDF review", conversation_url="https://chatgpt.com/c/fixture")
    bridge.claim_command()
    assert bridge.begin_submission(run_id)
    observation = {
        "phase": "submission_confirmation",
        "candidateMessageId": "unverified-candidate",
        "candidateVerified": False,
        "matches": True,
        "confirmationElapsedMs": 120000,
        "submissionAttempted": True,
        "attachment": {
            "expectedFilename": "report.pdf",
            "observedFilename": "report(1).pdf",
            "filenameMatch": "mismatch",
        },
        "pageBody": "Private unrelated text must not be retained",
    }
    assert bridge.observe(run_id, observation)
    bridge.complete(run_id, {"ok": False, "status": "submission_unconfirmed"})
    result = bridge.wait(0.01)
    assert result["last_observation"]["candidateMessageId"] == "unverified-candidate"
    assert not result["last_observation"]["candidateVerified"]
    assert "pageBody" not in result["last_observation"]
    assert result["last_observation"]["attachment"] == observation["attachment"]
    assert result["submitted_message_id"] == ""
    assert result["submission_attempted"] is True
    assert result["zero_submission_proven"] is False
    assert result["message_submitted"] is False
    assert not bridge.observe(run_id, {"matches": False})


def test_cancellation_keeps_last_candidate_and_revokes_observation(tmp_path):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Cancel confirmation")
    bridge.claim_command()
    assert bridge.begin_submission(run_id)
    assert bridge.observe(run_id, {"candidateMessageId": "seen", "submissionAttempted": True})
    bridge.cancel_active()
    assert not bridge.observe(run_id, {"candidateMessageId": "replacement"})
    result = bridge.wait(0.01)
    assert result["last_observation"]["candidateMessageId"] == "seen"
    assert not result["zero_submission_proven"]
    assert not result["message_submitted"]


def test_observations_are_bounded_and_cannot_grant_a_submit_permit(tmp_path):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Bound diagnostics")
    bridge.claim_command()
    assert not bridge.observe("wrong-run", {})
    assert bridge.observe(
        run_id,
        {
            "candidateMessageId": "x" * 10000,
            "submitted": True,
            "attachment": {"observedFilename": "x" * 10000, "filenameMatch": "mismatch"},
        },
    )
    record = json.loads(bridge.record_path(run_id).read_text())
    assert len(record["last_observation"]["candidateMessageId"]) <= 256
    assert len(record["last_observation"]["attachment"]["observedFilename"]) <= 256
    assert not record["message_submitted"]
    assert not record.get("submitted_message_id")
    assert bridge.begin_submission(run_id)


def test_response_text_diagnostics_survive_without_granting_submission(tmp_path):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Inspect response text diagnostics")
    bridge.claim_command()
    evidence = {"fileCitationGroupCount": 11, "structuredFileCitationExtraction": True}
    assert bridge.observe(run_id, {"responseTextEvidence": evidence, "pageBody": "private"})
    record = json.loads(bridge.record_path(run_id).read_text())
    assert record["last_observation"]["responseTextEvidence"] == evidence
    assert "pageBody" not in record["last_observation"]
    assert not record["message_submitted"]
    assert not record.get("submission_authorized")


def test_tab_loading_diagnostics_survive_without_granting_submission(tmp_path):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Inspect loading diagnostics")
    bridge.claim_command()
    evidence = {"status": "loading", "discarded": False, "elapsedMs": 30000, "budgetMs": 30000}
    assert bridge.observe(run_id, {"tabLoadEvidence": evidence, "pageBody": "private"})
    record = json.loads(bridge.record_path(run_id).read_text())
    assert record["last_observation"]["tabLoadEvidence"] == evidence
    assert "pageBody" not in record["last_observation"]
    assert not record["message_submitted"]
    assert not record.get("submission_authorized")


def test_destination_diagnostics_survive_without_granting_submission(tmp_path):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Inspect destination diagnostics")
    bridge.claim_command()
    evidence = {
        "reasonCode": "canonical_redirected_home",
        "expected": "https://chatgpt.com/c/expected",
        "observed": "https://chatgpt.com/",
    }
    assert bridge.observe(
        run_id,
        {"destinationEvidence": evidence, "tabSource": "created", "pageBody": "private"},
    )
    record = json.loads(bridge.record_path(run_id).read_text())
    assert record["last_observation"]["destinationEvidence"] == evidence
    assert record["last_observation"]["tabSource"] == "created"
    assert "pageBody" not in record["last_observation"]
    assert not record["message_submitted"]
    assert not record.get("submission_authorized")


def test_preupload_conflict_keeps_target_and_trigger_counts_without_file_list(tmp_path):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Inspect draft conflict")
    bridge.claim_command()
    observation = {
        "phase": "composer_ready",
        "tabId": 17,
        "tabSource": "managed",
        "attachment": {"cardCount": 0, "state": "selected"},
        "selectedFileCount": 1,
        "selectedFiles": ["private-file.pdf"],
    }
    assert bridge.observe(run_id, observation)
    bridge.complete(run_id, {"ok": False, "status": "failed", "error": "draft conflict"})
    record = json.loads(bridge.record_path(run_id).read_text())
    evidence = record["last_observation"]
    assert evidence["tabId"] == 17
    assert evidence["tabSource"] == "managed"
    assert evidence["attachment"]["cardCount"] == 0
    assert evidence["selectedFileCount"] == 1
    assert "selectedFiles" not in evidence
    assert record["zero_submission_proven"]
    assert not record.get("submission_authorized")
