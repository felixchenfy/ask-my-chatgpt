from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import pytest

import web_content_agent.chatgpt_chrome_bridge as chatgpt_bridge_module
from web_content_agent.chatgpt_chrome_bridge import ChatGptChromeBridge


def test_message_is_persisted_claimed_and_completed(tmp_path: Path) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("What is 2 + 2?")

    queued = json.loads(bridge.record_path(run_id).read_text(encoding="utf-8"))
    assert queued["status"] == "queued"
    assert queued["question"] == "What is 2 + 2?"
    assert queued["conversation_url"] == "https://chatgpt.com/"

    command = bridge.claim_command()
    assert command == {
        "action": "ask_chatgpt",
        "run_id": run_id,
        "question": "What is 2 + 2?",
        "conversation_url": "https://chatgpt.com/",
        "attachment_path": None,
        "attachment_filename": None,
        "thinking_mode": "current",
        "session_key": None,
        "new_conversation": False,
        "response_timeout_seconds": 900,
    }
    assert bridge.claim_command() == {"action": "idle"}

    bridge.event(run_id, "Chrome connected")
    bridge.complete(
        run_id,
        {
            "ok": True,
            "status": "completed",
            "response_text": "4",
            "conversation_url": "https://chatgpt.com/c/example-id",
            "response_extraction_verified": True,
        },
    )
    result = bridge.wait(timeout_seconds=0.1)
    assert result["response_text"] == "4"
    assert result["conversation_url"] == "https://chatgpt.com/c/example-id"

    completed = json.loads(bridge.record_path(run_id).read_text(encoding="utf-8"))
    assert completed["status"] == "completed"
    assert completed["events"][0]["message"] == "Chrome connected"


def test_claim_records_chatgpt_companion_version(tmp_path: Path) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("What version is connected?")

    command = bridge.claim_command(
        extension_version="0.4.16",
        worker_instance_id="worker-example",
        protocol_version=1,
        expected_version="0.4.16",
    )

    assert command["run_id"] == run_id
    record = json.loads(bridge.record_path(run_id).read_text(encoding="utf-8"))
    assert record["companion"] == {
        "extension_version": "0.4.16",
        "worker_instance_id": "worker-example",
        "protocol_version": 1,
    }


def test_chatgpt_command_waits_for_extension_reload(tmp_path: Path) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Wait for the current extension.")

    reload_command = bridge.claim_command(
        extension_version="0.4.15",
        worker_instance_id="worker-old",
        protocol_version=1,
        expected_version="0.4.16",
    )
    assert reload_command["action"] == "reload_extension"
    assert reload_command["expected_extension_version"] == "0.4.16"

    command = bridge.claim_command(
        extension_version="0.4.16",
        worker_instance_id="worker-new",
        protocol_version=1,
        expected_version="0.4.16",
    )
    assert command["run_id"] == run_id


def test_submitted_request_is_reclaimed_read_only_after_worker_restart(
    tmp_path: Path,
) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message(
        "Review this.",
        conversation_url="https://chatgpt.com/c/restart-recovery",
        expected_response_characters=12,
        expected_response_lines=2,
        expected_response_sha256="a" * 64,
    )
    bridge.claim_command(
        extension_version="0.4.22",
        worker_instance_id="worker-before-restart",
        protocol_version=1,
        expected_version="0.4.22",
    )
    bridge.mark_submitted(run_id, "https://chatgpt.com/c/restart-recovery", "message-restart")

    recovery = bridge.claim_command(
        extension_version="0.4.22",
        worker_instance_id="worker-after-restart",
        protocol_version=1,
        expected_version="0.4.22",
    )

    assert recovery == {
        "action": "read_chatgpt_conversation",
        "run_id": run_id,
        "conversation_url": "https://chatgpt.com/c/restart-recovery",
        "message_submitted": True,
        "recovered_after_companion_restart": True,
        "expected_message_id": "message-restart",
        "expected_response_characters": 12,
        "expected_response_lines": 2,
        "expected_response_sha256": "a" * 64,
        "response_timeout_seconds": 900,
    }
    record = json.loads(bridge.record_path(run_id).read_text(encoding="utf-8"))
    assert record["status"] == "recovering_after_companion_restart"
    assert record["recovery_count"] == 1
    assert record["companion"]["worker_instance_id"] == "worker-after-restart"
    assert "read-only recovery mode" in record["events"][-1]["message"]
    assert bridge.claim_command(
        extension_version="0.4.22",
        worker_instance_id="worker-after-restart",
        protocol_version=1,
        expected_version="0.4.22",
    ) == {"action": "idle"}


def test_worker_restart_never_reclaims_unsubmitted_ask(tmp_path: Path) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    bridge.start_message(
        "Do not resend me.",
        conversation_url="https://chatgpt.com/c/unsubmitted-restart",
    )
    bridge.claim_command(
        extension_version="0.4.22",
        worker_instance_id="worker-before-restart",
        protocol_version=1,
        expected_version="0.4.22",
    )

    assert bridge.claim_command(
        extension_version="0.4.22",
        worker_instance_id="worker-after-restart",
        protocol_version=1,
        expected_version="0.4.22",
    ) == {"action": "idle"}


def test_read_only_request_is_safe_to_reclaim_after_worker_restart(tmp_path: Path) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_read("https://chatgpt.com/c/read-restart")
    bridge.claim_command(
        extension_version="0.4.22",
        worker_instance_id="reader-before-restart",
        protocol_version=1,
        expected_version="0.4.22",
    )

    recovery = bridge.claim_command(
        extension_version="0.4.22",
        worker_instance_id="reader-after-restart",
        protocol_version=1,
        expected_version="0.4.22",
    )

    assert recovery["action"] == "read_chatgpt_conversation"
    assert recovery["run_id"] == run_id
    assert recovery["message_submitted"] is False
    assert "question" not in recovery


def test_read_only_recovery_preserves_original_submission_state(tmp_path: Path) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message(
        "Review this.",
        conversation_url="https://chatgpt.com/c/preserve-submitted",
    )
    bridge.claim_command(
        extension_version="0.4.22",
        worker_instance_id="worker-before-restart",
        protocol_version=1,
        expected_version="0.4.22",
    )
    bridge.mark_submitted(run_id, "https://chatgpt.com/c/preserve-submitted", "message-preserved")
    bridge.claim_command(
        extension_version="0.4.22",
        worker_instance_id="worker-after-restart",
        protocol_version=1,
        expected_version="0.4.22",
    )
    bridge.complete(
        run_id,
        {
            "ok": True,
            "status": "read_completed",
            "response_text": "Recovered response.",
            "conversation_url": "https://chatgpt.com/c/preserve-submitted",
            "response_extraction_verified": True,
        },
    )

    assert bridge.wait(timeout_seconds=0.1)["message_submitted"] is True


def test_max_thinking_mode_is_persisted_for_companion_verification(tmp_path: Path) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Review this.", thinking_mode="max")

    command = bridge.claim_command()
    assert command["thinking_mode"] == "max"
    record = json.loads(bridge.record_path(run_id).read_text(encoding="utf-8"))
    assert record["thinking_mode"] == "max"


def test_current_is_the_low_usage_default(tmp_path: Path) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Review this.")

    command = bridge.claim_command()
    assert command["thinking_mode"] == "current"
    bridge.complete(
        run_id,
        {
            "ok": True,
            "status": "completed",
            "response_text": "Review completed in the current mode.",
            "conversation_url": "https://chatgpt.com/c/prefer-max-fallback",
            "thinking_mode": "current",
            "thinking_mode_requested": "current",
            "thinking_mode_verified": False,
            "thinking_mode_selection_attempted": True,
            "thinking_mode_fallback_reason": "Max control was not found",
            "response_extraction_verified": True,
        },
    )
    result = bridge.wait(timeout_seconds=0.1)
    assert result["thinking_mode_requested"] == "current"
    assert result["thinking_mode"] == "current"
    assert result["thinking_mode_verified"] is False
    assert result["thinking_mode_selection_attempted"] is True
    assert result["thinking_mode_fallback_reason"] == "Max control was not found"


def test_unknown_thinking_mode_is_rejected_before_queueing(tmp_path: Path) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    with pytest.raises(ValueError, match="thinking_mode"):
        bridge.start_message("Review this.", thinking_mode="extreme")


def test_read_existing_conversation_never_contains_a_question(tmp_path: Path) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_read("https://chatgpt.com/c/existing-review")

    command = bridge.claim_command()
    assert command == {
        "action": "read_chatgpt_conversation",
        "run_id": run_id,
        "conversation_url": "https://chatgpt.com/c/existing-review",
        "response_timeout_seconds": 120,
    }
    assert "question" not in command

    bridge.complete(
        run_id,
        {
            "ok": True,
            "status": "read_completed",
            "response_text": "Full recovered review",
            "conversation_url": "https://chatgpt.com/c/existing-review",
            "response_extraction_verified": True,
        },
    )
    result = bridge.wait(timeout_seconds=0.1)
    assert result["status"] == "read_completed"
    assert result["response_text"] == "Full recovered review"


def test_success_without_verified_response_extraction_is_rejected(tmp_path: Path) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Review this.")
    bridge.claim_command()

    with pytest.raises(ValueError, match="did not verify"):
        bridge.complete(
            run_id,
            {
                "ok": True,
                "status": "completed",
                "response_text": "An unverified fragment",
                "conversation_url": "https://chatgpt.com/c/example-id",
            },
        )


def test_deterministic_response_expectations_are_persisted_and_verified(tmp_path: Path) -> None:
    response = "line1\nline2"
    digest = hashlib.sha256(response.encode("utf-8")).hexdigest()
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message(
        "Return both lines.",
        expected_response_characters=10,
        expected_response_lines=2,
        expected_response_sha256=digest,
    )
    command = bridge.claim_command()
    assert command["expected_response_characters"] == 10
    assert command["expected_response_lines"] == 2
    assert command["expected_response_sha256"] == digest

    bridge.complete(
        run_id,
        {
            "ok": True,
            "status": "completed",
            "response_text": response,
            "conversation_url": "https://chatgpt.com/c/deterministic-match",
            "response_extraction_verified": True,
        },
    )
    result = bridge.wait(timeout_seconds=0.1)
    assert result["ok"] is True
    assert result["response_character_count"] == 10
    assert result["response_line_count"] == 2
    assert result["response_sha256"] == digest


def test_success_is_downgraded_when_deterministic_expectation_mismatches(
    tmp_path: Path,
) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Return ten characters.", expected_response_characters=10)
    bridge.claim_command()
    bridge.complete(
        run_id,
        {
            "ok": True,
            "status": "completed",
            "response_text": "four",
            "conversation_url": "https://chatgpt.com/c/deterministic-mismatch",
            "response_extraction_verified": True,
        },
    )
    result = bridge.wait(timeout_seconds=0.1)
    assert result["ok"] is False
    assert result["status"] == "response_unverified"
    assert result["response_extraction_verified"] is False
    assert "expected 10 characters but received 4" in result["error"]


def test_success_is_downgraded_below_minimum_response_characters(tmp_path: Path) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message(
        "Return a detailed review.",
        minimum_response_characters=900,
    )
    command = bridge.claim_command()
    assert command["minimum_response_characters"] == 900
    bridge.complete(
        run_id,
        {
            "ok": True,
            "status": "completed",
            "response_text": "我已经直接打开并",
            "conversation_url": "https://chatgpt.com/c/minimum-mismatch",
            "response_extraction_verified": True,
        },
    )
    result = bridge.wait(timeout_seconds=0.1)
    assert result["ok"] is False
    assert result["status"] == "response_unverified"
    assert result["response_extraction_verified"] is False
    assert "expected at least 900 characters but received 8" in result["error"]


@pytest.mark.parametrize(
    "url",
    [
        "http://chatgpt.com/",
        "https://example.com/",
        "https://chatgpt.com/share/example",
        "https://chatgpt.com/?temporary-chat=true",
    ],
)
def test_message_rejects_unsafe_conversation_urls(tmp_path: Path, url: str) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    with pytest.raises(ValueError):
        bridge.start_message("hello", url)


def test_provisional_web_conversation_url_is_not_persisted_as_canonical() -> None:
    assert (
        chatgpt_bridge_module._canonical_conversation_url(
            "https://chatgpt.com/c/WEB:966ad82a-7f1b-4213-965a-302e0aeed1bb"
        )
        is None
    )


def test_ask_automatically_recovers_one_unverified_submitted_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeBridge:
        def start_message(self, *args: object, **kwargs: object) -> str:
            return "send-run"

        def wait(self, timeout_seconds: float) -> dict[str, object]:
            return {
                "ok": False,
                "status": "response_unverified",
                "response_text": "1",
                "submitted_message_id": "message-blog",
                "conversation_url": "https://chatgpt.com/c/final-conversation",
                "message_submitted": True,
                "response_extraction_verified": False,
            }

        def record_path(self, run_id: str) -> Path:
            return tmp_path / f"{run_id}.json"

    recoveries: list[dict[str, object]] = []

    def fake_read(**kwargs: object) -> dict[str, object]:
        recoveries.append(kwargs)
        return {
            "ok": True,
            "status": "read_completed",
            "response_text": "1. Verdict: PUBLISH-READY.",
            "conversation_url": "https://chatgpt.com/c/final-conversation",
            "message_submitted": False,
            "response_extraction_verified": True,
            "run_id": "read-run",
            "task_file": str(tmp_path / "read-run.json"),
        }

    monkeypatch.setattr(chatgpt_bridge_module, "ensure_bridge_server", lambda: FakeBridge())
    monkeypatch.setattr(chatgpt_bridge_module, "ensure_chrome_running", lambda: None)
    monkeypatch.setattr(chatgpt_bridge_module, "read_chatgpt_conversation_data", fake_read)

    result = chatgpt_bridge_module.ask_chatgpt_data("Review both posts.")

    assert len(recoveries) == 1
    assert result["ok"] is True
    assert result["status"] == "completed_after_read_recovery"
    assert result["message_submitted"] is True
    assert result["automatic_read_recovery_attempted"] is True
    assert result["send_run_id"] == "send-run"
    assert result["recovery_run_id"] == "read-run"
    assert recoveries[0]["timeout_seconds"] == 600
    assert recoveries[0]["expected_message_id"] == "message-blog"


def test_ask_automatically_recovers_after_submitted_wait_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record_path = tmp_path / "timed-out-send.json"

    class FakeBridge:
        def start_message(self, *args: object, **kwargs: object) -> str:
            record_path.write_text(
                json.dumps(
                    {
                        "status": "submitted",
                        "submitted_message_id": "message-timeout",
                        "message_submitted": True,
                        "conversation_url": "https://chatgpt.com/c/final-conversation",
                    }
                ),
                encoding="utf-8",
            )
            return "timed-out-send"

        def wait(self, timeout_seconds: float) -> dict[str, object]:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["status"] = "timeout_after_submission"
            record_path.write_text(json.dumps(record), encoding="utf-8")
            raise TimeoutError("message submitted but result callback was missed")

        def record_path(self, run_id: str) -> Path:
            assert run_id == "timed-out-send"
            return record_path

    recoveries: list[dict[str, object]] = []

    def fake_read(**kwargs: object) -> dict[str, object]:
        recoveries.append(kwargs)
        return {
            "ok": True,
            "status": "read_completed",
            "response_text": "The page had already finished.",
            "conversation_url": "https://chatgpt.com/c/final-conversation",
            "message_submitted": False,
            "response_extraction_verified": True,
            "run_id": "timeout-read-run",
            "task_file": str(tmp_path / "timeout-read-run.json"),
        }

    monkeypatch.setattr(chatgpt_bridge_module, "ensure_bridge_server", lambda: FakeBridge())
    monkeypatch.setattr(chatgpt_bridge_module, "ensure_chrome_running", lambda: None)
    monkeypatch.setattr(chatgpt_bridge_module, "read_chatgpt_conversation_data", fake_read)

    result = chatgpt_bridge_module.ask_chatgpt_data("Review the published page.")

    assert len(recoveries) == 1
    assert result["ok"] is True
    assert result["status"] == "completed_after_read_recovery"
    assert result["message_submitted"] is True
    assert result["automatic_read_recovery_attempted"] is True
    assert result["send_run_id"] == "timed-out-send"
    assert result["recovery_run_id"] == "timeout-read-run"


def test_only_one_message_can_be_in_flight(tmp_path: Path) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    bridge.start_message("first")
    with pytest.raises(RuntimeError, match="already running"):
        bridge.start_message("second")


def test_pdf_attachment_uses_existing_allowed_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    pdf = allowed / "review.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"allowedPdfRoots": [str(allowed)], "maximumPdfSizeMb": 1}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHATGPT_PDF_REVIEW_CONFIG", str(config))

    bridge = ChatGptChromeBridge(tmp_path / "state")
    bridge.start_message("Review this.", attachment_path=str(pdf))
    command = bridge.claim_command()
    assert command["attachment_path"] == str(pdf.resolve())
    assert command["attachment_filename"] == "review.pdf"


def test_pdf_attachment_outside_allowed_roots_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\nfixture")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"allowedPdfRoots": [str(allowed)], "maximumPdfSizeMb": 1}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHATGPT_PDF_REVIEW_CONFIG", str(config))

    bridge = ChatGptChromeBridge(tmp_path / "state")
    with pytest.raises(PermissionError, match="outside"):
        bridge.start_message("Review this.", attachment_path=str(outside))


def test_wait_receives_result_from_companion_thread(tmp_path: Path) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("hello")

    def companion() -> None:
        bridge.claim_command()
        bridge.complete(
            run_id,
            {
                "ok": True,
                "response_text": "Hi there",
                "conversation_url": "https://chatgpt.com/c/threaded-example",
                "response_extraction_verified": True,
            },
        )

    thread = threading.Thread(target=companion)
    thread.start()
    assert bridge.wait(timeout_seconds=1)["response_text"] == "Hi there"
    thread.join()


def test_wait_resets_the_response_window_after_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chatgpt_bridge_module, "COMPANION_RESULT_GRACE_SECONDS", 0.05)
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message(
        "wait for the submitted response",
        conversation_url="https://chatgpt.com/c/phase-timeout",
        response_timeout_seconds=1,
    )

    def companion() -> None:
        bridge.claim_command()
        time.sleep(0.4)
        bridge.mark_submitted(run_id, "https://chatgpt.com/c/phase-timeout")
        time.sleep(0.8)
        bridge.complete(
            run_id,
            {
                "ok": True,
                "status": "completed",
                "response_text": "Finished after the original deadline.",
                "conversation_url": "https://chatgpt.com/c/phase-timeout",
                "response_extraction_verified": True,
                "message_submitted": True,
            },
        )

    thread = threading.Thread(target=companion)
    thread.start()
    assert bridge.wait(timeout_seconds=1)["ok"] is True
    thread.join()


def test_duplicate_completed_request_returns_cached_result(tmp_path: Path) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("same question", session_key="review-session")
    bridge.claim_command()
    bridge.mark_submitted(run_id, "https://chatgpt.com/c/reused")
    bridge.complete(
        run_id,
        {
            "ok": True,
            "status": "completed",
            "response_text": "answer",
            "conversation_url": "https://chatgpt.com/c/reused",
            "response_extraction_verified": True,
            "message_submitted": True,
        },
    )

    assert bridge.start_message("same question", session_key="review-session") == ""
    assert bridge._preflight_result["deduplicated"] is True
    assert bridge._preflight_result["original_run_id"] == run_id


def test_session_key_reuses_canonical_conversation(tmp_path: Path) -> None:
    first = ChatGptChromeBridge(tmp_path)
    run_id = first.start_message("first", session_key="blog-review")
    first.claim_command()
    first.complete(
        run_id,
        {
            "ok": True,
            "status": "completed",
            "response_text": "done",
            "conversation_url": "https://chatgpt.com/c/session-example",
            "response_extraction_verified": True,
        },
    )

    second = ChatGptChromeBridge(tmp_path)
    second.start_message("follow up", session_key="blog-review")
    assert second.claim_command()["conversation_url"] == "https://chatgpt.com/c/session-example"


def test_session_key_survives_response_failure_after_submission(tmp_path: Path) -> None:
    first = ChatGptChromeBridge(tmp_path)
    run_id = first.start_message("first", session_key="failed-response-session")
    first.claim_command()
    first.mark_submitted(run_id, "https://chatgpt.com/c/failed-response-example")
    first.complete(
        run_id,
        {
            "ok": False,
            "status": "failed",
            "error": "response extraction failed",
            "conversation_url": "https://chatgpt.com/c/failed-response-example",
            "message_submitted": True,
        },
    )

    second = ChatGptChromeBridge(tmp_path)
    second.start_message("different follow-up", session_key="failed-response-session")
    assert (
        second.claim_command()["conversation_url"]
        == "https://chatgpt.com/c/failed-response-example"
    )


def test_throttle_completion_opens_no_send_cooldown(tmp_path: Path) -> None:
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("first")
    bridge.claim_command()
    bridge.complete(
        run_id,
        {"ok": False, "status": "throttled", "error": "usage limit"},
    )

    assert bridge.start_message("different question") == ""
    assert bridge._preflight_result["status"] == "throttled_cooldown"
    assert bridge._preflight_result["message_submitted"] is False
