"""Local persisted-state regressions; no Chrome, bridge listener or live send."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from web_content_agent.chatgpt_chrome_bridge import ChatGptChromeBridge


def failed_preparation(root):
    bridge = ChatGptChromeBridge(root)
    run_id = bridge.start_message("Original review", idempotency_key="same-request")
    bridge.claim_command()
    bridge.complete(run_id, {"ok": False, "status": "failed", "error": "composer missing"})
    return bridge, run_id


def retry(root):
    bridge = ChatGptChromeBridge(root)
    run_id = bridge.start_message("Original review", idempotency_key="same-request")
    return bridge, run_id


def test_proven_zero_preparation_failure_allows_explicit_same_key_retry(tmp_path):
    old, failed = failed_preparation(tmp_path)
    preserved = old.record_path(failed).read_bytes()
    new, run_id = retry(tmp_path)
    assert run_id and run_id != failed
    record = new._read_record(run_id)
    assert record["retry_of_run_id"] == failed
    assert record["question"] == "Original review"
    assert old.record_path(failed).read_bytes() == preserved
    assert not old.begin_submission(failed)
    assert new.claim_command()["run_id"] == run_id
    assert new.begin_submission(run_id)
    assert not new.begin_submission(run_id)
    assert retry(tmp_path)[1] == ""


@pytest.mark.parametrize(
    "case",
    [
        "exact",
        "other-error",
        "no-tab",
        "changed-question",
        "send-evidence",
        "no-session",
        "auto-key",
        "send-unready",
        "send-unready-no-match",
        "send-unready-old-phase",
    ],
)
def test_only_matching_unsent_input_failure_can_preserve_queued_draft(tmp_path, case):
    args = {"idempotency_key": "queued-draft", "session_key": "draft-session"}
    if case == "no-session":
        args.pop("session_key")
    if case == "auto-key":
        args.pop("idempotency_key")
    old = ChatGptChromeBridge(tmp_path)
    parent = old.start_message("Exact queued text", **args)
    old.claim_command()
    old.observe(
        parent,
        {
            "phase": "composer_input_verification"
            if case.startswith("send-unready") and case != "send-unready-old-phase"
            else "composer_ready",
            "matches": case != "send-unready-no-match",
            **({"tabId": 17} if case != "no-tab" else {}),
        },
    )
    old.complete(
        parent,
        {
            "ok": False,
            "status": "failed",
            "error": "other"
            if case == "other-error"
            else "ChatGPT Send button is not ready"
            if case.startswith("send-unready")
            else "ChatGPT composer does not contain the exact queued question",
        },
    )
    if case == "send-evidence":
        old._update_record(parent, submission_attempted=True)
    original = old.record_path(parent).read_bytes()
    new = ChatGptChromeBridge(tmp_path)
    run_id = new.start_message(
        "Changed text" if case == "changed-question" else "Exact queued text", **args
    )
    command = new.claim_command()
    if case in {"exact", "send-unready"}:
        assert command["preserved_queued_draft"] == {"run_id": parent, "tab_id": 17}
        assert (
            new._read_record(run_id)["preserved_queued_draft"] == command["preserved_queued_draft"]
        )
    else:
        assert "preserved_queued_draft" not in command
    assert old.record_path(parent).read_bytes() == original


@pytest.mark.parametrize(
    "field",
    ["submission_authorized", "submission_attempted", "message_submitted", "submitted_message_id"],
)
def test_any_durable_send_evidence_blocks_retry_despite_zero_flag(tmp_path, field):
    old, run_id = failed_preparation(tmp_path)
    old._update_record(run_id, **{field: True})
    assert retry(tmp_path)[0]._preflight_result["status"] == "duplicate_blocked"


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "corrupt",
        "missing-proof",
        "false-proof",
        "string-proof",
        "running",
        "mismatched-id",
    ],
)
def test_missing_ambiguous_or_inflight_evidence_never_allows_retry(tmp_path, damage):
    old, run_id = failed_preparation(tmp_path)
    record = old._read_record(run_id)
    if damage == "missing":
        old.record_path(run_id).unlink()
    elif damage == "corrupt":
        old.record_path(run_id).write_text("{", encoding="utf-8")
    else:
        if damage == "missing-proof":
            record.pop("zero_submission_proven")
        if damage == "false-proof":
            record["zero_submission_proven"] = False
        if damage == "string-proof":
            record["zero_submission_proven"] = "true"
        if damage == "running":
            record["status"] = "running"
        if damage == "mismatched-id":
            record["run_id"] = "another-run"
        old._write_record(run_id, record)
    assert retry(tmp_path)[1] == ""


def test_two_same_key_retries_cannot_both_reserve_the_failed_request(tmp_path):
    failed_preparation(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: retry(tmp_path)[1], range(2)))
    assert sum(bool(run_id) for run_id in results) == 1


def test_throttle_cooldown_precedes_zero_submission_exception(tmp_path):
    bridge, _ = failed_preparation(tmp_path)
    run_id = bridge.start_message("A different request")
    bridge.claim_command()
    bridge.complete(run_id, {"ok": False, "status": "throttled"})
    assert retry(tmp_path)[0]._preflight_result["status"] == "throttled_cooldown"


@pytest.mark.parametrize(
    "status",
    [
        "queued",
        "running",
        "submitted",
        "response_unverified",
        "timeout_before_submission",
        "cancelled_submission_unknown",
        "throttled",
    ],
)
def test_non_retryable_state_cannot_use_terminal_record_exception(tmp_path, status):
    old, _ = failed_preparation(tmp_path)
    with old._shared_state() as state:
        state["requests"]["explicit:same-request"]["status"] = status
    assert retry(tmp_path)[1] == ""


def test_inflight_duplicate_keeps_existing_command_and_duplicate_response(tmp_path):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Original review", idempotency_key="same-request")
    assert bridge.start_message("Original review", idempotency_key="same-request") == ""
    assert bridge._preflight_result["status"] == "duplicate_blocked"
    assert bridge.claim_command()["run_id"] == run_id
    with pytest.raises(RuntimeError, match="already running"):
        bridge.start_message("A genuinely different request")


@pytest.mark.parametrize("field", ["submission_attempted", "message_submitted"])
def test_callback_send_evidence_cannot_be_overridden_by_claimed_zero(tmp_path, field):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Original review", idempotency_key="same-request")
    bridge.claim_command()
    bridge.complete(
        run_id, {"ok": False, "status": "failed", "zero_submission_proven": True, field: True}
    )
    assert not bridge._read_record(run_id)["zero_submission_proven"]
    assert retry(tmp_path)[1] == ""


@pytest.mark.parametrize("permit", [False, True])
def test_cancelled_request_requires_user_retry_and_no_granted_permit(tmp_path, permit):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Original review", idempotency_key="same-request")
    bridge.claim_command()
    if permit:
        assert bridge.begin_submission(run_id)
    bridge.cancel_active()
    assert bool(retry(tmp_path)[1]) is not permit


def test_unclaimed_disconnect_proof_survives_restart_and_allows_retry(tmp_path):
    bridge = ChatGptChromeBridge(tmp_path)
    bridge.start_message("Original review", idempotency_key="same-request")
    with pytest.raises(TimeoutError):
        bridge.wait(0.001)
    assert retry(tmp_path)[1]


def test_composer_observation_is_bounded_and_survives_completion(tmp_path):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Readiness evidence")
    bridge.claim_command()
    assert bridge.observe(
        run_id,
        {
            "phase": "composer_loading",
            "composerErrorCode": "not_signed_in",
            "composerElapsedMs": 500,
            "composerBudgetMs": 30000,
            "composerEvidence": {"present": False, "visible": False, "pageText": "private account"},
            "pageText": "private account",
            "question": "private question",
        },
    )
    bridge.complete(run_id, {"ok": False, "status": "failed"})
    observed = bridge._read_record(run_id)["last_observation"]
    assert observed["composerErrorCode"] == "not_signed_in"
    assert observed["composerEvidence"] == {"present": False, "visible": False}
    assert observed["composerElapsedMs"] == 500
    assert "private" not in json.dumps(observed)


def test_thinking_failure_diagnostics_survive_without_private_payloads(tmp_path):
    bridge = ChatGptChromeBridge(tmp_path)
    run_id = bridge.start_message("Review", thinking_mode="max")
    bridge.claim_command()
    bridge.complete(
        run_id,
        {
            "ok": False,
            "status": "failed",
            "last_observation": {
                "phase": "thinking_mode_validation_failed",
                "thinkingEvidence": {
                    "unsupportedReason": "unsupported_thinking_effort_slider",
                    "sliderValue": "4",
                    "sliderDescription": "x" * 600,
                    "verified": False,
                    "question": "private question",
                    "html": "private page",
                },
            },
        },
    )
    result = bridge._read_record(run_id)["result"]
    evidence = result["last_observation"]["thinkingEvidence"]
    assert evidence["sliderValue"] == "4"
    assert len(evidence["sliderDescription"]) == 256
    assert evidence["verified"] is False
    assert "private" not in json.dumps(evidence)
    assert result["zero_submission_proven"] is True
