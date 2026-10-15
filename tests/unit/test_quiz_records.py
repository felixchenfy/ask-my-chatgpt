from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from web_content_agent.onepoint3acres.chrome_bridge import ChromeBridge
from web_content_agent.onepoint3acres.diagnostics import LOG_RETENTION_DAYS, PersistentRunLog
from web_content_agent.onepoint3acres.journal import QuizJournal
from web_content_agent.onepoint3acres.models import DailyQuiz, QuizAnswer


@pytest.fixture
def quiz():
    return DailyQuiz(
        question="关于积分的说法，哪个正确？",
        choices=[
            {"index": 1, "text": "以上都对"},
            {"index": 2, "text": "分享内容可获大米"},
        ],
        source_url="offline",
    )


@pytest.mark.parametrize("days", [-1, 0, 1, 3, 6])
def test_log_cannot_prune_below_seven_days(tmp_path, days):
    with pytest.raises(ValueError):
        PersistentRunLog(tmp_path / "run.log").prune_older_than(days)


@pytest.mark.parametrize(
    "offset_seconds,retained",
    [(-1, False), (0, True), (1, True), (86400, True), (259200, True), (604800, True)],
)
def test_log_seven_day_boundary(tmp_path, offset_seconds, retained):
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    stamp = now - timedelta(days=7) + timedelta(seconds=offset_seconds)
    log = PersistentRunLog(tmp_path / "run.log")
    log.append(f"[{stamp.isoformat(sep=' ', timespec='seconds')}] evidence")
    assert log.prune_older_than(now=now) == int(not retained)
    assert bool(log.tail()) is retained
    assert LOG_RETENTION_DAYS == 7


@pytest.mark.parametrize(
    "line", ["[2026-99-99 09:00:00] invalid", "[99:90:00] old format", "a continuation", ""]
)
def test_malformed_log_does_not_break_startup(tmp_path, line):
    log = PersistentRunLog(tmp_path / "run.log")
    log.append(line)
    assert log.prune_older_than() == 0
    assert len(log.tail()) == 1


def test_retention_honors_timestamp_timezone(tmp_path):
    log = PersistentRunLog(tmp_path / "run.log")
    log.append("[2026-09-03 05:00:00-07:00] exact boundary")
    log.append("[2026-09-03 04:59:59-07:00] too old")
    assert log.prune_older_than(now=datetime(2026, 9, 10, 12, tzinfo=UTC)) == 1
    assert "exact boundary" in log.tail()[0]


def test_missing_log_and_longer_retention(tmp_path):
    log = PersistentRunLog(tmp_path / "run.log")
    assert log.prune_older_than(30) == 0
    assert not log.path.exists()


def test_journal_threads_have_whole_lines_and_no_auto_promotion(tmp_path):
    journal = QuizJournal(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: journal.append("run-a", "test", {"n": n}), range(40)))
    lines = (
        next((tmp_path / "runs/onepoint3acres").glob("*.jsonl"))
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert sorted(json.loads(line)["data"]["n"] for line in lines) == list(range(40))
    assert not (tmp_path / "knowledge").exists()


@pytest.mark.parametrize("run_id", ["../escape", "x/y", "", "a" * 81])
def test_journal_rejects_invalid_run_id(tmp_path, run_id):
    with pytest.raises(ValueError):
        QuizJournal(tmp_path).append(run_id, "test", {})


@pytest.mark.parametrize("fail", [False, True])
def test_simulation_records_evidence_answer_or_failure_never_a_verified_grade(
    tmp_path, monkeypatch, fail
):
    class Solver:
        def solve(self, quiz):
            assert quiz.supplementary and "saved snippet" in quiz.supplementary
            if fail:
                raise RuntimeError("uncertain")
            return QuizAnswer(
                choice_index=1,
                choice_text="A",
                confidence=0.95,
                evidence_sufficient=True,
                reason="test",
            )

    monkeypatch.setattr(
        "web_content_agent.onepoint3acres.runtime.create_solver", lambda **_: Solver()
    )
    bridge = ChromeBridge(lambda _: None, journal=QuizJournal(tmp_path))
    result = bridge.simulate(
        {
            "question": "Q",
            "choices": ["A", "B"],
            "web_evidence": [
                {
                    "title": "T",
                    "url": "https://www.1point3acres.com/bbs/rules.html",
                    "snippet": "saved snippet",
                }
            ],
        }
    )
    assert result["ok"] is not fail and not bridge.status()["running"]
    rows = [
        json.loads(line)
        for line in next((tmp_path / "runs/onepoint3acres").glob("*.jsonl"))
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert rows[0]["event"] == "run_started"
    assert rows[1]["data"]["web_evidence"][0]["snippet"] == "saved snippet"
    assert rows[2]["event"] == ("solve_failed" if fail else "model_answer")
    assert rows[-1]["data"]["website_verdict"] == "not_submitted"


def test_completed_legacy_run_never_becomes_trusted_knowledge(tmp_path):
    bridge = ChromeBridge(lambda _: None, journal=QuizJournal(tmp_path))
    run = bridge.start_run()
    bridge.complete(run, {"ok": True})
    rows = [
        json.loads(line)
        for line in next((tmp_path / "runs/onepoint3acres").glob("*.jsonl"))
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert rows[-1]["data"]["website_verdict"] == "unverified"
    bridge.complete(run, {"ok": False})  # A stale completion must not create new evidence.
    assert (
        len(
            next((tmp_path / "runs/onepoint3acres").glob("*.jsonl"))
            .read_text(encoding="utf-8")
            .splitlines()
        )
        == 2
    )


def test_journal_io_failure_is_visible_and_does_not_leave_run_busy(monkeypatch):
    class Broken:
        def append(self, *_):
            raise OSError("disk full")

    logs = []
    bridge = ChromeBridge(logs.append, journal=Broken())
    run = bridge.start_run()
    bridge.complete(run, {"ok": False})
    assert not bridge.status()["running"]
    assert any("审计记录保存失败" in line for line in logs)


@pytest.mark.parametrize("verdict", ["correct", "incorrect", "already_completed_unknown"])
def test_explicit_chrome_outcome_is_preserved_but_not_promoted(tmp_path, verdict):
    bridge = ChromeBridge(lambda _: None, journal=QuizJournal(tmp_path))
    run = bridge.start_run()
    bridge.complete(
        run,
        {
            "ok": verdict != "incorrect",
            "quiz_outcome": {
                "verdict": verdict,
                "evidence": "page message",
                "arbitrary_extra": "ignored",
            },
        },
    )
    path = next((tmp_path / "runs/onepoint3acres").glob("*.jsonl"))
    last = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])["data"]
    assert last["website_verdict"] == "chrome_reported_" + verdict
    assert "arbitrary_extra" not in last["quiz_outcome"]
    assert not (tmp_path / "knowledge").exists()


@pytest.mark.parametrize("selected", [False, True])
def test_actual_runtime_ignores_legacy_bank_and_records_complete_quiz(
    monkeypatch, tmp_path, quiz, selected
):
    from contextlib import nullcontext

    from web_content_agent.onepoint3acres import runtime

    legacy_path = tmp_path / "knowledge/onepoint3acres/history/community-questions.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(
        json.dumps({"records": [{"question": quiz.question, "answer": "LEGACY_BANK"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("WCA_WORKSPACE", str(tmp_path))
    records = []
    monkeypatch.setattr(runtime, "selected_model", lambda: "selected" if selected else None)
    monkeypatch.setattr(runtime, "model_lease", nullcontext)
    monkeypatch.setattr(runtime, "knowledge_for", lambda *_, **__: [{"facts": "official"}])
    monkeypatch.setattr(runtime, "quiz_model", lambda *_: nullcontext("fake-config"))
    monkeypatch.setattr(runtime, "LMStudioClient", lambda _, **kwargs: None)

    class Solver:
        client = type("Client", (), {"config": type("Config", (), {"identifier": "test"})()})()

        def solve(self, enriched):
            assert records[0]["quiz"] == enriched.model_dump()
            assert enriched.local_knowledge == [{"facts": "official"}]
            assert records[0]["quiz"]["choices"] == quiz.model_dump()["choices"]
            assert "historical_answers" not in records[0]["quiz"]
            assert "LEGACY_" not in json.dumps(records)
            return QuizAnswer(
                choice_index=1,
                choice_text="以上都对",
                confidence=0.95,
                reason="test",
                evidence_sufficient=True,
            )

    monkeypatch.setattr(runtime, "_default_solver", Solver)
    monkeypatch.setattr(runtime, "QuizSolver", lambda _: Solver())
    # Old serialized quizzes may still contain this field. Parsing drops it.
    quiz = DailyQuiz.model_validate(
        {**quiz.model_dump(), "historical_answers": [{"historical_answer": "LEGACY_INPUT"}]}
    )
    assert "historical_answers" not in quiz.model_dump()
    runtime.create_solver(record=records.append).solve(quiz)
    assert records[0]["prompt_version"] == runtime.PROMPT_VERSION
    assert not hasattr(runtime, "history_for")
    assert legacy_path.exists()  # Runtime does not read, rewrite, or reimport old data.


def test_solver_does_not_send_legacy_answers_in_either_prompt(quiz):
    from web_content_agent.onepoint3acres.solver import QuizSolver

    calls = []

    class Client:
        def structured(self, schema, messages, **kwargs):
            payload = json.loads(messages[1]["content"])
            calls.append(payload)
            assert "historical_answers" not in payload
            assert "LEGACY_INPUT" not in json.dumps(payload)
            assert payload["choices"] == [
                {"choice_index": c.index, "choice_text": c.text} for c in quiz.choices
            ]
            return QuizAnswer(
                choice_index=1,
                choice_text=quiz.choices[0].text,
                confidence=0.95,
                reason="fixture",
                evidence_sufficient=True,
            )

    # Even a caller bypassing schema validation cannot add the old field to model requests.
    legacy_quiz = quiz.model_copy(
        update={"historical_answers": [{"historical_answer": "LEGACY_INPUT"}]}
    )
    QuizSolver(Client()).solve(legacy_quiz)
    assert len(calls) == 2
