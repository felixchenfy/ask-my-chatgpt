"""Configuration amendment is audited, constrained, and cannot submit a request."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from web_content_agent.chatgpt_chrome_bridge import ChatGptChromeBridge


def failed_max(root):
    bridge = ChatGptChromeBridge(root)
    run = bridge.start_message(
        "Original question",
        thinking_mode="max",
        idempotency_key="operation",
        session_key="review",
        minimum_response_characters=5000,
    )
    bridge.claim_command()
    bridge.complete(run, {"ok": False, "status": "failed", "error": "Max unavailable"})
    return bridge, run


def correct(bridge, run, apply=True):
    return bridge.correct_unsent_thinking_mode(
        idempotency_key="operation",
        expected_run_id=run,
        source_task="source-task",
        reason="Caller chose strict Max although the authorized preference permits fallback",
        apply=apply,
    )


def retry(root, **changes):
    bridge = ChatGptChromeBridge(root)
    args = dict(
        question="Original question",
        thinking_mode="prefer_max",
        idempotency_key="operation",
        session_key="review",
        minimum_response_characters=5000,
    )
    args.update(changes)
    return bridge, bridge.start_message(**args)


def test_correction_preserves_failure_and_does_not_queue_then_links_one_same_key_retry(tmp_path):
    bridge, run = failed_max(tmp_path)
    original = bridge.record_path(run).read_bytes()
    ledger = bridge.shared_state_path.read_bytes()
    assert retry(tmp_path)[0]._preflight_result["status"] == "idempotency_conflict"
    assert correct(bridge, run, apply=False)["status"] == "correction_ready"
    assert bridge.shared_state_path.read_bytes() == ledger
    assert bridge.claim_command() == {"action": "idle"}
    assert correct(bridge, run)["tasks_queued"] == 0
    assert correct(bridge, run)["status"] == "already_corrected"
    assert bridge.record_path(run).read_bytes() == original
    assert bridge.claim_command() == {"action": "idle"}
    assert (
        retry(tmp_path, thinking_mode="max")[0]._preflight_result["status"]
        == "idempotency_conflict"
    )
    new, new_run = retry(tmp_path)
    record = new._read_record(new_run)
    assert record["retry_of_run_id"] == run
    assert record["thinking_mode_correction"]["from_mode"] == "max"
    assert record["thinking_mode"] == "prefer_max"
    assert record["minimum_response_characters"] == 5000
    assert retry(tmp_path)[1] == ""
    assert not bridge.begin_submission(run)
    assert run in json.loads(bridge.shared_state_path.read_text())["thinking_mode_corrections"]


@pytest.mark.parametrize(
    "damage", ["permit", "attempt", "submitted", "no-proof", "stale", "fingerprint", "other-mode"]
)
def test_correction_rejects_unsafe_or_inconsistent_evidence(tmp_path, damage):
    bridge, run = failed_max(tmp_path)
    if damage == "permit":
        bridge._update_record(run, submission_authorized=True)
    elif damage == "attempt":
        bridge._update_record(run, submission_attempted=True)
    elif damage == "submitted":
        bridge._update_record(run, submitted_message_id="existing-turn")
    elif damage == "no-proof":
        bridge._update_record(run, zero_submission_proven=False)
    elif damage == "other-mode":
        bridge._update_record(run, thinking_mode="current")
    elif damage == "fingerprint":
        with bridge._shared_state() as state:
            state["requests"]["explicit:operation"]["request_fingerprint"] = "unrelated"
    else:
        run = "chatgpt-20000101-000000-abcdef"
    with pytest.raises(ValueError):
        correct(bridge, run)


@pytest.mark.parametrize(
    "changes",
    [
        {"question": "Changed"},
        {"minimum_response_characters": 1},
        {"session_key": "other"},
        {"thinking_mode": "current"},
    ],
)
def test_correction_does_not_allow_other_argument_changes(tmp_path, changes):
    bridge, run = failed_max(tmp_path)
    correct(bridge, run)
    assert retry(tmp_path, **changes)[0]._preflight_result["status"] == "idempotency_conflict"


def test_corrected_key_still_has_one_reservation_and_obeys_cooldown(tmp_path):
    bridge, run = failed_max(tmp_path)
    correct(bridge, run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(lambda _: retry(tmp_path)[1], range(2)))
    assert sum(bool(run) for run in runs) == 1
    with pytest.raises(ValueError):
        correct(bridge, run)


def test_correction_does_not_clear_a_throttle(tmp_path):
    bridge, run = failed_max(tmp_path)
    correct(bridge, run)
    other = bridge.start_message("Other operation")
    bridge.claim_command()
    bridge.complete(other, {"ok": False, "status": "throttled"})
    assert retry(tmp_path)[0]._preflight_result["status"] == "throttled_cooldown"
