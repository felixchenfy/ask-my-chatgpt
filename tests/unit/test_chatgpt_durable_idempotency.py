"""Persistent operation protection across days and process restarts; no browser."""

import errno
import json
import re
from datetime import timedelta

import pytest

import web_content_agent.chatgpt_chrome_bridge as module


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("outcome", ["submission_unconfirmed", "queued", "completed", "zero"])
def test_operation_protection_survives_window(tmp_path, monkeypatch, explicit, outcome):
    options = {"idempotency_key": "operation"} if explicit else {}
    first = module.ChatGptChromeBridge(tmp_path)
    old = first.start_message("Original question", **options)
    if outcome != "queued":
        first.claim_command()
        if outcome != "zero":
            assert first.begin_submission(old)
        if outcome == "completed":
            first.mark_submitted(old, "https://chatgpt.com/c/offline", "original-message")
        first.complete(
            old,
            {
                "ok": outcome == "completed",
                "status": "failed" if outcome == "zero" else outcome,
                "response_text": "Complete answer." if outcome == "completed" else "",
                "conversation_url": "https://chatgpt.com/c/offline",
                "response_extraction_verified": outcome == "completed",
            },
        )
    preserved = first.record_path(old).read_bytes()
    future = module._now() + timedelta(days=2)
    monkeypatch.setattr(module, "_now", lambda: future)
    second = module.ChatGptChromeBridge(tmp_path)
    new = second.start_message("Original question", **options)
    allowed = outcome == "zero" or (outcome == "completed" and not explicit)
    assert bool(new) == allowed
    if outcome == "completed" and explicit:
        assert second._preflight_result["deduplicated"] is True
    if outcome in {"queued", "submission_unconfirmed"}:
        assert second._preflight_result["status"] == "duplicate_blocked"
    assert first.record_path(old).read_bytes() == preserved


def test_duplicate_block_carries_original_message_identity(tmp_path):
    # A caller interrupted after submission re-runs with the same key: it must stay
    # blocked, but get the original message id for the single bound read.
    first = module.ChatGptChromeBridge(tmp_path)
    old = first.start_message("Original question", idempotency_key="operation")
    first.claim_command()
    assert first.begin_submission(old)
    first.mark_submitted(old, "https://chatgpt.com/c/offline", "original-message")
    second = module.ChatGptChromeBridge(tmp_path)
    assert second.start_message("Original question", idempotency_key="operation") == ""
    blocked = second._preflight_result
    assert blocked["status"] == "duplicate_blocked"
    assert blocked["message_submitted"] is True
    assert blocked["submitted_message_id"] == "original-message"
    assert blocked["conversation_url"] == "https://chatgpt.com/c/offline"
    assert blocked["original_run_id"] == old


def test_duplicate_block_without_readable_record_stays_blocked(tmp_path):
    first = module.ChatGptChromeBridge(tmp_path)
    old = first.start_message("Original question", idempotency_key="operation")
    first.record_path(old).unlink()
    second = module.ChatGptChromeBridge(tmp_path)
    assert second.start_message("Original question", idempotency_key="operation") == ""
    assert second._preflight_result["status"] == "duplicate_blocked"
    assert second._preflight_result["submitted_message_id"] == ""


def test_explicit_key_cannot_silently_change_question(tmp_path):
    bridge = module.ChatGptChromeBridge(tmp_path)
    bridge.start_message("Original", idempotency_key="operation")
    another = module.ChatGptChromeBridge(tmp_path)
    assert another.start_message("Changed", idempotency_key="operation") == ""
    assert another._preflight_result["status"] == "idempotency_conflict"


def test_legacy_ambiguous_record_without_timestamp_stays_protected(tmp_path):
    bridge = module.ChatGptChromeBridge(tmp_path)
    run = bridge.start_message("Legacy")
    with bridge._shared_state() as state:
        old = next(iter(state["requests"].values()))
        old.pop("created_at")
        old.pop("request_fingerprint")
    restarted = module.ChatGptChromeBridge(tmp_path)
    assert restarted.start_message("Legacy") == ""
    assert restarted._preflight_result["original_run_id"] == run


def test_idle_bridge_can_negotiate_version_without_a_synthetic_command(tmp_path):
    bridge = module.ChatGptChromeBridge(tmp_path)
    assert bridge.claim_command("0.4.53", "old", 1, "0.4.54")["action"] == "reload_extension"
    assert bridge.claim_command("0.4.54", "new", 1, "0.4.54")["action"] == "idle"
    assert bridge._command is None
    assert not list(tmp_path.glob("chatgpt-*.json"))


def test_pairing_preparation_preserves_optional_quiz_lane(tmp_path):
    from web_content_agent.chatgpt_pairing import prepare_pairing

    prepare_pairing(tmp_path)
    assert not (tmp_path / "chrome_extension/quiz-pairing.js").exists()
    quiz_state = tmp_path / "runtime/state/quiz-pairing.json"
    quiz_token = "c0" * 32
    quiz_state.write_text(json.dumps({"token": quiz_token}), encoding="utf-8")
    original = (tmp_path / "runtime/state/chatgpt-pairing.json").read_bytes()
    prepare_pairing(tmp_path)
    assert quiz_token in (tmp_path / "chrome_extension/quiz-pairing.js").read_text()
    assert (tmp_path / "runtime/state/chatgpt-pairing.json").read_bytes() == original


def test_quiz_pairing_replaces_fixed_or_malformed_tokens(tmp_path):
    from web_content_agent.chatgpt_pairing import prepare_quiz_pairing

    quiz_state = tmp_path / "runtime/state/quiz-pairing.json"
    quiz_state.parent.mkdir(parents=True)
    quiz_state.write_text(json.dumps({"token": "fixed-shared-token"}), encoding="utf-8")
    token = prepare_quiz_pairing(tmp_path)
    assert re.fullmatch(r"[0-9a-f]{64}", token)
    assert json.loads(quiz_state.read_text(encoding="utf-8"))["token"] == token
    assert token in (tmp_path / "chrome_extension/quiz-pairing.js").read_text()
    assert prepare_quiz_pairing(tmp_path) == token  # stable once valid


@pytest.mark.parametrize("read_only", [False, True])
def test_busy_port_reports_zero_submission_without_queueing(tmp_path, monkeypatch, read_only):
    bridge = module.ChatGptChromeBridge(tmp_path)
    monkeypatch.setattr(module, "_bridge", bridge)
    monkeypatch.setattr(module, "_server", None)

    def occupied(_bridge):
        raise OSError(errno.EADDRINUSE, "fixture occupied port")

    monkeypatch.setattr(module, "ChatGptBridgeServer", occupied)
    result = (
        module.read_chatgpt_conversation_data("https://chatgpt.com/c/offline", timeout_seconds=1)
        if read_only
        else module.ask_chatgpt_data("Original", timeout_seconds=1)
    )
    assert result["status"] == "bridge_busy"
    assert result["zero_submission_proven"] is True
    assert bridge._command is None
    assert not list(tmp_path.glob("*.json"))
