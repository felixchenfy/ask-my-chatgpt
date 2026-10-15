from __future__ import annotations

import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime

import httpx
import pytest

from web_content_agent.onepoint3acres import chrome_bridge as module
from web_content_agent.onepoint3acres.chrome_bridge import BRIDGE_TOKEN, BridgeServer
from web_content_agent.onepoint3acres.journal import QuizJournal
from web_content_agent.onepoint3acres.models import QuizAnswer
from web_content_agent.onepoint3acres.scheduler import DailyScheduleLedger
from web_content_agent.onepoint3acres.supervision import SupervisedChromeBridge

QUIZ = {"question": "测试题？", "choices": ["选项一", "选项二"]}


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    from web_content_agent.onepoint3acres import runtime

    def factory(**kwargs):
        assert kwargs["advisory"] is True

        class Solver:
            def solve(self, quiz):
                kwargs["record"]({"quiz": quiz.model_dump(), "model_key": "fake-local-model"})
                return QuizAnswer(
                    choice_index=1,
                    choice_text="选项一",
                    confidence=1,
                    reason="fixture",
                    evidence_sufficient=True,
                )

        return Solver()

    monkeypatch.setattr(runtime, "create_solver", factory)
    return SupervisedChromeBridge(
        lambda _: None, QuizJournal(tmp_path), token_path=tmp_path / "token"
    )


def ready(bridge, *, simulation=False):
    run = bridge.start_run(simulation=simulation)
    bridge._solve_jobs[run] = {"status": "pending", "started_monotonic": time.monotonic()}
    bridge._run_solve_job(run, QUIZ)
    return {
        "run_id": run,
        "review_id": bridge.review()["review_id"],
        "action": "answer",
        "choice_index": 2,
        "choice_text": "选项二",
        "reason": "Codex checks both options",
        "sources": ["https://www.1point3acres.com/points.html"],
    }


def records(tmp_path):
    return [
        json.loads(line)
        for path in (tmp_path / "runs/onepoint3acres").glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


@pytest.mark.parametrize("simulation", [False, True])
def test_verified_checkin_survives_codex_skip(bridge, simulation):
    run = bridge.start_run(simulation=simulation)
    bridge.poll_solve(run, {**QUIZ, "checkin_verified": True})
    for _ in range(100):
        if bridge.review().get("status") == "awaiting_review":
            break
        time.sleep(0.005)
    bridge.decide(
        {
            "run_id": run,
            "review_id": bridge.review()["review_id"],
            "action": "skip",
            "reason": "missing independent evidence",
            "sources": [],
        }
    )
    progress = bridge.journal.daily_progress()
    assert progress["checkin_completed"] is (not simulation)
    assert progress["quiz_completed"] is False


def test_local_answer_is_never_automatically_released(bridge):
    run = bridge.start_run()
    assert bridge.poll_solve(run, QUIZ)["status"] == "pending"
    for _ in range(100):
        if bridge.review().get("status") == "awaiting_review":
            break
        time.sleep(0.005)
    assert bridge.review()["local_answer"]["confidence"] == 1
    result = bridge.poll_solve(run, QUIZ)
    assert result == {"status": "pending", "waiting_for": "codex_review"}
    assert "answer" not in result
    assert bridge.status()["internal_schedule_enabled"] is False


def test_codex_may_disagree_and_both_choices_are_recorded(bridge, tmp_path):
    decision = ready(bridge)
    assert bridge.decide(decision)["accepted"]
    job = bridge._solve_jobs[decision["run_id"]]
    assert job["answer"]["choice_index"] == 2
    assert job["local_answer"]["choice_index"] == 1
    event = next(r for r in records(tmp_path) if r["event"] == "codex_decision")
    assert event["data"]["choice_index"] == 2
    assert event["data"]["local_answer"]["choice_index"] == 1
    assert event["data"]["quiz"] == QUIZ
    assert bridge.decide(decision)["duplicate"]
    assert len([r for r in records(tmp_path) if r["event"] == "codex_decision"]) == 1


@pytest.mark.parametrize("verdict", ["correct", "incorrect", "already_completed_unknown"])
def test_live_audit_preserves_all_options_decisions_and_explicit_outcome(bridge, tmp_path, verdict):
    # Mock website/model only: tests the production journal, not a real website grade.
    quiz = {
        "question": "如果希望匿名发帖，以下哪些说法正确？",
        "choices": ["选项一", "选项二", "仅限满足条件时", "永久有效（错误变体）", "这些都正确"],
    }
    run = bridge.start_run()
    bridge._solve_jobs[run] = {"status": "pending", "started_monotonic": time.monotonic()}
    bridge._run_solve_job(run, quiz)
    bridge.decide(
        {
            "run_id": run,
            "review_id": bridge.review()["review_id"],
            "action": "answer",
            "choice_index": 2,
            "choice_text": quiz["choices"][1],
            "reason": "Test reviewer chooses a different option",
            "sources": ["https://www.1point3acres.com/bbs/points.html"],
        }
    )
    bridge.complete(
        run,
        {
            "ok": verdict != "incorrect",
            "quiz_outcome": {"verdict": verdict, "evidence": "explicit test page message"},
        },
    )
    rows = records(tmp_path)
    assert all(row["run_id"] == run and row["recorded_at"] for row in rows)
    assert rows[0]["data"]["simulation"] is False
    received = next(row["data"]["quiz"] for row in rows if row["event"] == "quiz_received")
    assert received["question"] == quiz["question"]
    assert [(c["index"], c["text"]) for c in received["choices"]] == list(
        enumerate(quiz["choices"], 1)
    )
    decision = next(row["data"] for row in rows if row["event"] == "codex_decision")
    assert decision["quiz"] == quiz
    assert decision["local_answer"]["choice_index"] == 1
    assert decision["local_answer"]["choice_text"] == quiz["choices"][0]
    assert decision["choice_index"] == 2 and decision["choice_text"] == quiz["choices"][1]
    assert decision["simulation"] is False
    assert rows[-1]["data"]["website_verdict"] == "chrome_reported_" + verdict
    assert not (tmp_path / "knowledge").exists()


@pytest.mark.parametrize(
    "change",
    [
        {"choice_index": 0},
        {"choice_index": 3},
        {"choice_index": True},
        {"choice_index": "2"},
        {"choice_text": "选项一"},
        {"review_id": "stale"},
        {"run_id": "stale"},
        {"action": "override"},
        {"reason": " "},
        {"reason": "x" * 2001},
        {"sources": ["x"] * 11},
        {"sources": ["x" * 2049]},
        {"code": "anything"},
        {"action": "skip"},
    ],
)
def test_invalid_decisions_never_authorize_submission(bridge, change):
    decision = ready(bridge)
    with pytest.raises(ValueError):
        bridge.decide({**decision, **change})
    assert "answer" not in bridge._solve_jobs[decision["run_id"]]


def test_skip_completes_without_waiting_for_chrome(bridge, tmp_path):
    decision = ready(bridge)
    decision.update(action="skip", choice_index=None, choice_text=None)
    bridge.decide(decision)
    assert not bridge.status()["running"]
    assert bridge.status()["result"]["skipped"]
    assert not any(r["event"] == "submission_authorized" for r in records(tmp_path))


def test_model_failure_still_allows_codex_to_review(bridge, monkeypatch):
    monkeypatch.setattr(
        bridge, "solve", lambda *_: (_ for _ in ()).throw(RuntimeError("LM offline"))
    )
    decision = ready(bridge)
    assert bridge.review()["local_answer"] is None
    assert "LM offline" in bridge.review()["local_error"]
    assert bridge.decide(decision)["accepted"]


def test_low_confidence_and_missing_evidence_remain_advisory(bridge, monkeypatch):
    monkeypatch.setattr(
        bridge,
        "solve",
        lambda *_: {
            "choice_index": 1,
            "choice_text": "选项一",
            "confidence": 0.4,
            "evidence_sufficient": False,
            "reason": "unknown",
        },
    )
    decision = ready(bridge)
    assert bridge.review()["status"] == "awaiting_review"
    assert bridge.decide(decision)["accepted"]


def test_permit_is_one_use_and_requires_exact_live_snapshot(bridge, tmp_path):
    decision = ready(bridge)
    payload = {"run_id": decision["run_id"], "review_id": decision["review_id"], "quiz": QUIZ}
    with pytest.raises(ValueError):
        bridge.submission_permit(payload)
    bridge.decide(decision)
    for changed in [{**QUIZ, "question": "different"}, {**QUIZ, "choices": QUIZ["choices"][::-1]}]:
        with pytest.raises(ValueError, match="changed"):
            bridge.submission_permit({**payload, "quiz": changed})
    assert bridge.submission_permit(payload) == {"authorized": True}
    with pytest.raises(ValueError, match="already consumed"):
        bridge.submission_permit(payload)
    assert records(tmp_path)[-1]["event"] == "submission_authorized"


def test_selection_preparation_checks_approval_without_consuming_permit(bridge, tmp_path):
    decision = ready(bridge)
    payload = {"run_id": decision["run_id"], "review_id": decision["review_id"], "quiz": QUIZ}
    with pytest.raises(ValueError, match="approved"):
        bridge.prepare_selection(payload)
    bridge.decide(decision)
    for _ in range(2):
        assert bridge.prepare_selection(payload) == {
            "authorized": True,
            "submission_permit_consumed": False,
        }
    assert not any(row["event"] == "submission_authorized" for row in records(tmp_path))
    with pytest.raises(ValueError, match="changed"):
        bridge.prepare_selection({**payload, "quiz": {**QUIZ, "choices": ["new", "options"]}})
    bridge.submission_permit(payload)
    with pytest.raises(ValueError, match="already consumed"):
        bridge.prepare_selection(payload)


@pytest.mark.parametrize("ended", [False, True])
def test_selection_preparation_rejects_expired_or_ended_run(bridge, ended):
    decision = ready(bridge)
    bridge.decide(decision)
    if ended:
        bridge.complete(decision["run_id"], {"ok": False})
    else:
        bridge._solve_jobs[decision["run_id"]]["deadline"] = time.monotonic() - 1
    with pytest.raises(ValueError):
        bridge.prepare_selection(
            {"run_id": decision["run_id"], "review_id": decision["review_id"], "quiz": QUIZ}
        )


def test_http_preparation_uses_existing_auth_and_does_not_consume_permit(bridge, monkeypatch):
    decision = ready(bridge)
    bridge.decide(decision)
    payload = {"run_id": decision["run_id"], "review_id": decision["review_id"], "quiz": QUIZ}
    with api(bridge, monkeypatch) as client:
        assert (
            client.post(
                "/review/prepare", json=payload, headers={"X-WCA-Token": "wrong"}
            ).status_code
            == 403
        )
        assert client.post("/review/prepare", json=payload).json() == {
            "authorized": True,
            "submission_permit_consumed": False,
        }
        assert client.post("/review/permit", json=payload).json() == {"authorized": True}
        assert client.post("/review/prepare", json=payload).status_code == 400


def test_simulation_approval_can_never_issue_a_live_permit(bridge, tmp_path):
    decision = ready(bridge, simulation=True)
    bridge.decide(decision)
    assert not bridge.status()["running"]
    assert bridge.status()["result"]["website_actions"] == 0
    with pytest.raises(ValueError):
        bridge.submission_permit(
            {"run_id": decision["run_id"], "review_id": decision["review_id"], "quiz": QUIZ}
        )
    assert records(tmp_path)[-1]["data"]["website_verdict"] == "not_submitted"


@pytest.mark.parametrize("stage", ["before_decision", "before_permit", "after_completion"])
def test_expired_or_finished_runs_cannot_submit(bridge, stage):
    decision = ready(bridge)
    if stage != "before_decision":
        bridge.decide(decision)
    if stage == "after_completion":
        bridge.complete(decision["run_id"], {"ok": False})
    else:
        bridge._solve_jobs[decision["run_id"]]["deadline"] = time.monotonic() - 1
    with pytest.raises(ValueError):
        if stage == "before_decision":
            bridge.decide(decision)
        else:
            bridge.submission_permit(
                {"run_id": decision["run_id"], "review_id": decision["review_id"], "quiz": QUIZ}
            )


def test_disk_failure_cannot_release_an_answer(bridge, monkeypatch):
    decision = ready(bridge)

    def fail(*_):
        raise OSError("full")

    monkeypatch.setattr(bridge.journal, "append", fail)
    with pytest.raises(RuntimeError, match="persistence"):
        bridge.decide(decision)
    assert "answer" not in bridge._solve_jobs[decision["run_id"]]
    assert bridge.review()["audit_ok"] is False


def test_audit_unavailable_prevents_dispatch(bridge, monkeypatch):
    monkeypatch.setattr(bridge.journal, "append", lambda *_: (_ for _ in ()).throw(OSError()))
    with pytest.raises(RuntimeError, match="not dispatched"):
        bridge.start_run()
    assert not bridge.status()["running"]


def test_concurrent_conflicting_decisions_only_accept_one(bridge):
    decision = ready(bridge)
    second = {**decision, "choice_index": 1, "choice_text": "选项一"}

    def submit(item):
        try:
            return bridge.decide(item)["accepted"]
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, [decision, second])) == [False, True]


@contextmanager
def api(bridge, monkeypatch):
    monkeypatch.setattr(module, "BRIDGE_PORT", 0)
    server = BridgeServer(bridge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_port}",
            headers={"X-WCA-Token": BRIDGE_TOKEN},
            trust_env=False,
        ) as client:
            yield client
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_http_review_requires_separate_local_authorization(bridge, monkeypatch):
    decision = ready(bridge)
    with api(bridge, monkeypatch) as client:
        assert client.get("/review").json()["quiz"] == QUIZ
        assert client.post("/review/decision", json=decision).status_code == 403
        assert (
            client.post(
                "/review/decision", json=decision, headers={"X-WCA-Review-Token": "wrong"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/review/decision",
                json=decision,
                headers={"X-WCA-Review-Token": bridge.review_token},
            ).status_code
            == 200
        )


@pytest.mark.parametrize("hour", [0, 8, 9, 18, 23])
def test_attempt_does_not_block_another_state_check_after_restart(tmp_path, monkeypatch, hour):
    from web_content_agent import onepoint3acres_app as app_module
    from web_content_agent.onepoint3acres_app import OnePointThreeAcresApp

    fixed = datetime(2026, 9, 2, hour, tzinfo=datetime.now().astimezone().tzinfo)

    class Clock:
        @staticmethod
        def now():
            return fixed

    monkeypatch.setattr(app_module, "datetime", Clock)

    def app():
        instance = object.__new__(OnePointThreeAcresApp)
        instance.running = instance.run_request_pending = instance.close_request_pending = False
        instance._request_lock = threading.Lock()
        instance.events = queue.Queue()
        instance.daily_review_ledger = DailyScheduleLedger(tmp_path / "daily.json", hour=0)
        instance.bridge = type("Bridge", (), {"journal": QuizJournal(tmp_path)})()
        return instance

    first = app()
    assert first._queue_background_run(daily=True)
    assert not first._queue_background_run(daily=True)
    assert app()._queue_background_run(daily=True)
    assert first.events.qsize() == 1
    first.bridge.journal.append("done", "run_started", {"simulation": False})
    first.bridge.journal.append(
        "done",
        "run_finished",
        {
            "checkin_verified": True,
            "quiz_outcome": {"verdict": "correct", "evidence": "explicit site result"},
        },
    )
    assert not app()._queue_background_run(daily=True)


def test_manual_real_attempt_is_recorded_but_not_treated_as_completion(tmp_path):
    from web_content_agent.onepoint3acres_app import OnePointThreeAcresApp

    app = object.__new__(OnePointThreeAcresApp)
    app._request_lock = threading.Lock()
    app.daily_review_ledger = DailyScheduleLedger(tmp_path / "daily.json", strict=True)

    class Bridge:
        journal = QuizJournal(tmp_path)

        def start_run(self):
            persisted = DailyScheduleLedger(tmp_path / "daily.json", strict=True)
            assert persisted.last_scheduled_date == datetime.now().astimezone().date().isoformat()
            raise RuntimeError("fixture: stop before browser dispatch")

    app.bridge = Bridge()
    with pytest.raises(RuntimeError, match="fixture"):
        app._execute(scheduled=False)
    app.running = app.run_request_pending = app.close_request_pending = False
    app.events = queue.Queue()
    assert app._queue_background_run(daily=True)


def test_simulation_queue_does_not_consume_daily_catch_up(tmp_path):
    from web_content_agent.onepoint3acres_app import OnePointThreeAcresApp

    app = object.__new__(OnePointThreeAcresApp)
    app._request_lock = threading.Lock()
    app.daily_review_ledger = DailyScheduleLedger(tmp_path / "daily.json", strict=True)
    app.running = app.run_request_pending = app.close_request_pending = False
    app.events = queue.Queue()
    assert app._queue_simulation(QUIZ)
    assert app.daily_review_ledger.last_scheduled_date is None
    assert not (tmp_path / "daily.json").exists()


def test_close_and_run_cannot_be_queued_together(tmp_path):
    from web_content_agent.onepoint3acres_app import OnePointThreeAcresApp

    app = object.__new__(OnePointThreeAcresApp)
    app.running = app.run_request_pending = app.close_request_pending = False
    app._request_lock = threading.Lock()
    app.events = queue.Queue()
    assert app._queue_close()
    assert not app._queue_background_run()
    assert not app._queue_simulation(QUIZ)


def test_public_review_is_a_copy(bridge):
    decision = ready(bridge)
    bridge.review()["quiz"]["choices"][0] = "injected"
    assert bridge.review()["quiz"] == QUIZ
    assert bridge.decide(decision)["accepted"]


def test_solver_advisory_preserves_abstention_without_weakening_local_only():
    from web_content_agent.onepoint3acres.models import DailyQuiz, QuizChoice
    from web_content_agent.onepoint3acres.solver import InsufficientQuizEvidence, QuizSolver

    class Client:
        def structured(self, *_args, **_kwargs):
            return QuizAnswer(
                choice_index=1,
                choice_text="A",
                confidence=0,
                evidence_sufficient=False,
                reason="missing evidence",
            )

    quiz = DailyQuiz(
        question="Q",
        choices=[QuizChoice(index=1, text="A"), QuizChoice(index=2, text="B")],
        source_url="test",
    )
    solver = QuizSolver(Client())
    with pytest.raises(InsufficientQuizEvidence):
        solver.solve(quiz)
    assert not solver.solve(quiz, advisory=True).evidence_sufficient


def test_corrupt_daily_ledger_does_not_silently_reset(tmp_path):
    path = tmp_path / "daily.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        DailyScheduleLedger(path, strict=True)
