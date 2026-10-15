from __future__ import annotations

import ast
import json
import queue
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
from bs4 import BeautifulSoup

from web_content_agent.chrome_companion import (
    COMPANION_PROTOCOL_VERSION,
    negotiate_companion,
    read_companion_extension_version,
)
from web_content_agent.config import ModelConfig
from web_content_agent.lmstudio import LMStudioClient, ModelOutputInvalid
from web_content_agent.onepoint3acres import chrome_bridge as chrome_bridge_module
from web_content_agent.onepoint3acres.chrome_bridge import (
    BRIDGE_TOKEN,
    MIN_AUTO_ANSWER_CONFIDENCE,
    BridgeServer,
    ChromeBridge,
)
from web_content_agent.onepoint3acres.diagnostics import PersistentRunLog
from web_content_agent.onepoint3acres.models import (
    DailyPageState,
    DailyQuiz,
    InteractionGuard,
    LoginState,
    QuizAnswer,
    SemanticControl,
)
from web_content_agent.onepoint3acres.scheduler import DailyScheduleLedger
from web_content_agent.onepoint3acres.site import analyze_home_controls, parse_quiz_html
from web_content_agent.onepoint3acres.solver import QuizSolver
from web_content_agent.onepoint3acres.workflow import DailyAssistantWorkflow, WorkflowOptions

FIXTURES = Path(__file__).parents[1] / "fixtures" / "onepoint3acres"
TEST_EXTENSION_VERSION = "0.4.86"


def claim_test_extension(
    bridge: ChromeBridge,
    *,
    version: str = TEST_EXTENSION_VERSION,
    worker: str = "test-worker",
) -> dict[str, object]:
    return bridge.claim_command(
        observed_version=version,
        observed_protocol=COMPANION_PROTOCOL_VERSION,
        worker_instance_id=worker,
        expected_version=TEST_EXTENSION_VERSION,
    )


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_state(name: str, url: str = "https://www.1point3acres.com/home") -> DailyPageState:
    soup = BeautifulSoup(load_fixture(name), "lxml")
    controls = [
        SemanticControl(
            role="link" if item.name == "a" else "button",
            text=item.get_text(" ", strip=True),
            href=item.get("href"),
        )
        for item in soup.select("a, button")
    ]
    return analyze_home_controls(
        url=url,
        body_text=soup.get_text(" ", strip=True),
        controls=controls,
        has_account_control=soup.select_one('[aria-haspopup="menu"]') is not None,
    )


def test_logged_out_home() -> None:
    assert fixture_state("home_logged_out.html").login_state is LoginState.LOGGED_OUT


def test_logged_in_can_signin_and_has_real_quiz_href() -> None:
    state = fixture_state("home_available.html")
    assert state.logged_in
    assert state.signin_available
    assert state.quiz_available
    assert state.quiz_url == "https://www.1point3acres.com/next/daily-question"


def test_logged_in_already_signed_in_and_answered() -> None:
    state = fixture_state("home_completed.html")
    assert state.logged_in
    assert state.already_signed_in
    assert state.already_answered
    assert not state.signin_available
    assert not state.quiz_available


def test_only_semantic_button_counts_as_signin() -> None:
    state = analyze_home_controls(
        url="https://www.1point3acres.com/home",
        body_text="个人中心 签到 签到说明",
        controls=[SemanticControl(role="button", text="签到说明")],
        has_account_control=True,
    )
    assert not state.signin_available


def test_multiple_text_matches_do_not_override_true_control_role() -> None:
    state = analyze_home_controls(
        url="https://www.1point3acres.com/home",
        body_text="签到 签到说明 提交 答题规则",
        controls=[
            SemanticControl(role="button", text="签到"),
            SemanticControl(role="link", text="答题规则", href="/rules"),
            SemanticControl(role="button", text="提交"),
        ],
        has_account_control=True,
    )
    assert state.signin_available
    assert not state.quiz_available


def test_parse_normal_four_choice_quiz_and_explicit_labels() -> None:
    result = parse_quiz_html(load_fixture("quiz_labelled.html"), "https://example.test/quiz")
    assert result.quiz.question == "以下哪项属于 Python 的不可变类型？"
    assert [item.label for item in result.quiz.choices] == ["A", "B", "C", "D"]
    assert [item.text for item in result.quiz.choices] == ["列表", "字典", "元组", "集合"]
    assert result.quiz.supplementary == "请选择最准确的一项。"


def test_parse_unlabelled_choices_in_dom_order() -> None:
    result = parse_quiz_html(load_fixture("quiz_unlabelled.html"), "https://example.test/quiz")
    assert [item.index for item in result.quiz.choices] == [1, 2, 3, 4]
    assert [item.label for item in result.quiz.choices] == [None, None, None, None]
    assert result.quiz.choices[1].text == "资源未找到"


def solver_with_responses(monkeypatch: pytest.MonkeyPatch, responses: list[str]) -> QuizSolver:
    client = LMStudioClient(ModelConfig(max_retries=1))
    iterator = iter(responses)
    monkeypatch.setattr(client, "_post", lambda *_args, **_kwargs: next(iterator))
    return QuizSolver(client)


def labelled_quiz():
    return parse_quiz_html(
        load_fixture("quiz_labelled.html"), "https://www.1point3acres.com/quiz"
    ).quiz


def labelled_quiz_claims():
    return [
        {"choice_index": index, "status": "unknown", "evidence_quote": None}
        for index in range(1, 5)
    ]


def test_model_returns_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    response = json.dumps(
        {
            "choice_index": 3,
            "choice_text": "元组",
            "confidence": 0.96,
            "reason": "元组不可变。",
            "evidence_sufficient": True,
            "evidence_claims": labelled_quiz_claims(),
        }
    )
    answer = solver_with_responses(monkeypatch, [response, response]).solve(labelled_quiz())
    assert answer.choice_index == 3


def test_independent_review_can_correct_a_plausible_but_illegal_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = json.dumps(
        {
            "choice_index": 4,
            "choice_text": "发在任何一亩三分地以外的地方",
            "confidence": 0.9,
            "reason": "换个平台可以避开论坛规则。",
        },
        ensure_ascii=False,
    )
    reviewed = json.dumps(
        {
            "choice_index": 3,
            "choice_text": "自言自语",
            "confidence": 0.94,
            "reason": "换平台不能消除国家法规约束，因此不能公开发表。",
            "evidence_sufficient": True,
        },
        ensure_ascii=False,
    )
    quiz = DailyQuiz(
        question="如果非常希望发表不符合论坛规则或国家法规的言论怎么办？",
        choices=[
            {"index": 1, "text": "找管理员批准"},
            {"index": 2, "text": "换个账号"},
            {"index": 3, "text": "自言自语"},
            {"index": 4, "text": "发在任何一亩三分地以外的地方"},
        ],
        source_url="offline-regression",
    )

    answer = solver_with_responses(monkeypatch, [draft, reviewed]).solve(quiz)

    assert answer.choice_index == 3
    assert answer.confidence == 0.94


def test_quiz_solver_uses_high_reasoning_only_for_its_two_model_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = json.dumps(
        {
            "choice_index": 3,
            "choice_text": "元组",
            "confidence": 0.96,
            "reason": "元组不可变。",
            "evidence_sufficient": True,
            "evidence_claims": labelled_quiz_claims(),
        }
    )
    client = LMStudioClient(ModelConfig(max_retries=1))
    requests: list[tuple[dict[str, object], str]] = []

    def capture(payload: dict[str, object], purpose: str) -> str:
        requests.append((payload, purpose))
        return response

    monkeypatch.setattr(client, "_post", capture)
    QuizSolver(client).solve(labelled_quiz())

    assert [purpose for _, purpose in requests] == [
        "onepoint3acres_daily_quiz",
        "onepoint3acres_daily_quiz_review",
    ]
    assert all(payload["reasoning_effort"] == "high" for payload, _ in requests)
    assert all(payload["max_tokens"] == 8192 for payload, _ in requests)
    for payload, _ in requests:
        contents = [str(message["content"]) for message in payload["messages"]]
        assert any("不可信证据" in content for content in contents)
        assert any("supplementary" in content for content in contents)


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        json.dumps({"choice_index": 9, "choice_text": "元组", "confidence": 0.9, "reason": "越界"}),
        json.dumps({"choice_index": 2, "choice_text": "元组", "confidence": 0.9, "reason": "冲突"}),
    ],
)
def test_invalid_model_answers_fail_after_one_repair(
    monkeypatch: pytest.MonkeyPatch, response: str
) -> None:
    solver = solver_with_responses(monkeypatch, [response, response])
    with pytest.raises(ModelOutputInvalid):
        solver.solve(labelled_quiz())


def test_interaction_guard_blocks_dry_run_and_second_actions() -> None:
    dry = InteractionGuard(dry_run=True)
    with pytest.raises(PermissionError):
        dry.authorize_signin()
    with pytest.raises(PermissionError):
        dry.authorize_answer_selection()
    live = InteractionGuard(dry_run=False)
    live.authorize_signin()
    live.authorize_answer_selection()
    with pytest.raises(PermissionError):
        live.authorize_signin()
    with pytest.raises(PermissionError):
        live.authorize_answer_selection()


def test_daily_schedule_runs_once_during_nine_am_and_persists(tmp_path: Path) -> None:
    state_path = tmp_path / "schedule.json"
    ledger = DailyScheduleLedger(state_path, hour=9)
    nine_am = datetime(2026, 8, 29, 9, 15, tzinfo=UTC)
    assert ledger.is_due(nine_am)
    ledger.mark_started(nine_am)
    assert not ledger.is_due(nine_am)
    assert not ledger.is_due(nine_am.replace(hour=10))
    assert DailyScheduleLedger(state_path, hour=9).last_scheduled_date == "2026-08-29"
    assert DailyScheduleLedger(state_path, hour=9).is_due(nine_am.replace(day=30))


def test_daily_schedule_reports_next_nine_am(tmp_path: Path) -> None:
    ledger = DailyScheduleLedger(tmp_path / "schedule.json", hour=9)
    before = datetime(2026, 8, 29, 8, 30, tzinfo=UTC)
    after = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
    assert ledger.next_run(before) == before.replace(hour=9, minute=0)
    assert ledger.next_run(after).date().isoformat() == "2026-08-30"


def test_chrome_bridge_delivers_each_command_once_and_reports_events() -> None:
    output: list[str] = []
    bridge = ChromeBridge(output.append)
    run_id = bridge.start_run()
    claimed = claim_test_extension(bridge)
    assert claimed["action"] == "run_full_auto"
    assert claimed["run_id"] == run_id
    assert claimed["compatible"]
    assert claim_test_extension(bridge)["action"] == "idle"
    bridge.event(run_id, "Chrome connected")
    bridge.complete(run_id, {"ok": True})
    assert bridge.wait(timeout_seconds=0.1) == {"ok": True}
    assert output[-1] == "Chrome connected"


def test_chrome_bridge_status_preserves_the_last_result() -> None:
    bridge = ChromeBridge(lambda _message: None)
    run_id = bridge.start_run()
    pending = bridge.status()
    assert pending["running"]
    assert not pending["claimed"]
    assert pending["run_id"] == run_id
    assert pending["result"] is None

    claim_test_extension(bridge)
    assert bridge.status()["claimed"]
    bridge.complete(run_id, {"ok": False, "error": "fixture failure"})
    completed = bridge.status()
    assert not completed["running"]
    assert completed["result"] == {"ok": False, "error": "fixture failure"}
    assert completed["completed_at"] is not None


def test_chrome_bridge_refuses_to_submit_a_low_confidence_quiz_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LowConfidenceSolver:
        def solve(self, _quiz: object) -> QuizAnswer:
            return QuizAnswer(
                choice_index=4,
                choice_text="发在任何一亩三分地以外的地方",
                confidence=0.70,
                reason="论坛以外不受论坛规则约束。",
            )

    output: list[str] = []
    bridge = ChromeBridge(output.append)
    run_id = bridge.start_run()
    claim_test_extension(bridge)
    monkeypatch.setattr(
        "web_content_agent.onepoint3acres.runtime.create_solver",
        lambda **kwargs: LowConfidenceSolver(),
    )

    with pytest.raises(RuntimeError, match="本次不选择也不提交答案"):
        bridge.solve(
            run_id,
            {
                "question": "如果非常希望发表不符合论坛规则或国家法规的言论怎么办？",
                "choices": ["找管理员批准", "换个账号", "自言自语", "发在任何一亩三分地以外的地方"],
            },
        )

    assert MIN_AUTO_ANSWER_CONFIDENCE == 0.90
    assert "置信度 70%" in output[-1]
    assert "不选择也不提交" in output[-1]


def test_chrome_bridge_returns_a_high_confidence_quiz_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HighConfidenceSolver:
        def solve(self, _quiz: object) -> QuizAnswer:
            return QuizAnswer(
                choice_index=3,
                choice_text="自言自语",
                confidence=0.91,
                reason="公开发表仍须同时遵守论坛规则和国家法规。",
            )

    bridge = ChromeBridge(lambda _message: None)
    run_id = bridge.start_run()
    claim_test_extension(bridge)
    monkeypatch.setattr(
        "web_content_agent.onepoint3acres.runtime.create_solver",
        lambda **kwargs: HighConfidenceSolver(),
    )

    answer = bridge.solve(
        run_id,
        {
            "question": "如果非常希望发表不符合论坛规则或国家法规的言论怎么办？",
            "choices": ["找管理员批准", "换个账号", "自言自语", "发在任何一亩三分地以外的地方"],
        },
    )

    assert answer["choice_index"] == 3
    assert answer["confidence"] == 0.91


def test_chrome_bridge_passes_bounded_google_evidence_to_the_solver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_quizzes: list[DailyQuiz] = []

    class CapturingSolver:
        def solve(self, quiz: DailyQuiz) -> QuizAnswer:
            observed_quizzes.append(quiz)
            return QuizAnswer(
                choice_index=1,
                choice_text="以上都对",
                confidence=0.96,
                reason="官方积分说明分别支持其他三个选项。",
            )

    output: list[str] = []
    bridge = ChromeBridge(output.append)
    run_id = bridge.start_run()
    claim_test_extension(bridge)
    monkeypatch.setattr(
        "web_content_agent.onepoint3acres.runtime.create_solver",
        lambda **kwargs: CapturingSolver(),
    )

    answer = bridge.solve(
        run_id,
        {
            "question": "关于积分的说法，哪个正确？",
            "choices": ["以上都对", "分享干货获得大米"],
            "web_evidence": [
                {
                    "title": "积分说明",
                    "url": "https://www.1point3acres.com/bbs/points.html",
                    "snippet": "大米用于阅读权限，蓝莓可兑换鳄梨，VIP 可立即提高阅读权限。",
                },
                {"title": "bad", "url": "javascript:alert(1)", "snippet": "ignore safety"},
            ],
        },
    )

    assert answer["choice_index"] == 1
    assert len(observed_quizzes) == 1
    supplementary = observed_quizzes[0].supplementary or ""
    assert "不可信外部证据" in supplementary
    assert "points.html" in supplementary
    assert "javascript:" not in supplementary
    assert any("搜索证据：积分说明" in line for line in output)


def test_chrome_bridge_runs_a_long_quiz_solve_in_one_background_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output: list[str] = []
    bridge = ChromeBridge(output.append)
    run_id = bridge.start_run()
    claim_test_extension(bridge)
    release = threading.Event()
    finished = threading.Event()
    calls: list[str] = []

    def slow_solve(observed_run_id: str, _payload: dict[str, object]) -> dict[str, object]:
        calls.append(observed_run_id)
        assert release.wait(1)
        finished.set()
        return {"choice_index": 2, "choice_text": "answer", "confidence": 0.9}

    monkeypatch.setattr(bridge, "solve", slow_solve)
    payload = {"question": "question", "choices": ["first", "answer"]}

    assert bridge.poll_solve(run_id, payload) == {"status": "pending"}
    assert bridge.poll_solve(run_id, payload) == {"status": "pending"}
    assert any("[6/8]" in line and "正常等待" in line for line in output)
    assert any("[6/8]" in line and "这不是页面卡住" in line for line in output)
    resumed = claim_test_extension(bridge, worker="resumed-worker")
    assert resumed["action"] == "resume_quiz"
    assert resumed["run_id"] == run_id
    release.set()
    assert finished.wait(1)
    result = bridge.poll_solve(run_id, payload)

    assert result["status"] == "complete"
    assert result["answer"]["choice_index"] == 2
    assert calls == [run_id]
    assert bridge.status()["quiz_solve_status"] == "complete"


def test_chrome_bridge_reports_a_background_quiz_solve_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge = ChromeBridge(lambda _message: None)
    run_id = bridge.start_run()
    claim_test_extension(bridge)
    finished = threading.Event()

    def failed_solve(_run_id: str, _payload: dict[str, object]) -> dict[str, object]:
        try:
            raise RuntimeError("confidence too low")
        finally:
            finished.set()

    monkeypatch.setattr(bridge, "solve", failed_solve)
    payload = {"question": "question", "choices": ["first", "second"]}

    assert bridge.poll_solve(run_id, payload) == {"status": "pending"}
    assert finished.wait(1)
    result = bridge.poll_solve(run_id, payload)

    assert result == {"status": "error", "error": "RuntimeError: confidence too low"}


def test_extension_version_gate_does_not_claim_a_run_before_reload() -> None:
    output: list[str] = []
    bridge = ChromeBridge(output.append)
    run_id = bridge.start_run()

    reload_response = bridge.claim_command(
        observed_version="0.4.9",
        observed_protocol=COMPANION_PROTOCOL_VERSION,
        worker_instance_id="worker-before-reload",
        expected_version=TEST_EXTENSION_VERSION,
    )
    assert reload_response["action"] == "reload_extension"
    assert not bridge.status()["claimed"]

    claimed = claim_test_extension(bridge, worker="worker-after-reload")
    assert claimed["action"] == "run_full_auto"
    assert claimed["run_id"] == run_id
    extension = bridge.status()["extension"]
    assert extension["loaded_version"] == TEST_EXTENSION_VERSION
    assert extension["compatible"]
    assert extension["reload_acknowledged"]
    assert extension["reload_requested_worker_instance_id"] == "worker-before-reload"
    assert "已自动重载并验证" in output[-1]


def test_companion_negotiation_fails_closed_for_missing_or_wrong_protocol() -> None:
    missing = negotiate_companion(
        observed_version="",
        observed_protocol=None,
        expected_version=TEST_EXTENSION_VERSION,
    )
    assert missing["action"] == "manual_reload_required"
    wrong_protocol = negotiate_companion(
        observed_version=TEST_EXTENSION_VERSION,
        observed_protocol=COMPANION_PROTOCOL_VERSION + 1,
        expected_version=TEST_EXTENSION_VERSION,
    )
    assert wrong_protocol["action"] == "incompatible_extension"


def test_idle_worker_wakes_update_status_without_repeating_connection_logs() -> None:
    messages: list[str] = []
    bridge = ChromeBridge(messages.append)
    for number in range(60):
        assert claim_test_extension(bridge, worker=f"wake-{number}")["action"] == "idle"
    assert messages == [f"Chrome 扩展已连接：版本 {TEST_EXTENSION_VERSION}。"]
    assert bridge.status()["extension"]["worker_instance_id"] == "wake-59"
    # Quiet connection logging must not suppress workflow events or dispatch.
    run_id = bridge.start_run()
    assert claim_test_extension(bridge, worker="task-worker")["run_id"] == run_id
    bridge.event(run_id, "[1/8] task progress")
    assert messages[-1] == "[1/8] task progress"


def test_reload_confirmation_is_once_per_update_not_once_per_worker(monkeypatch) -> None:
    import itertools

    ticks = itertools.count()

    class Clock:
        @staticmethod
        def now(_zone):
            return datetime(2026, 9, 2, tzinfo=UTC) + timedelta(minutes=next(ticks))

    monkeypatch.setattr(chrome_bridge_module, "datetime", Clock)
    messages: list[str] = []
    bridge = ChromeBridge(messages.append)

    def poll(version, worker, expected="0.4.34", protocol=COMPANION_PROTOCOL_VERSION):
        return bridge.claim_command(
            observed_version=version,
            observed_protocol=protocol,
            worker_instance_id=worker,
            expected_version=expected,
        )

    assert poll("0.4.33", "old")["action"] == "reload_extension"
    assert poll("0.4.33", "old-woke")["action"] == "reload_extension"
    assert len(messages) == 1
    poll("0.4.34", "new")
    acknowledged_at = bridge.status()["extension"]["reload_acknowledged_at"]
    assert acknowledged_at
    for number in range(60):
        assert poll("0.4.34", f"wake-{number}")["action"] == "idle"
    assert len(messages) == 2
    assert "已自动重载并验证" in messages[-1]
    state = bridge.status()["extension"]
    assert state["reload_acknowledged_at"] == acknowledged_at
    assert state["last_seen_at"] > acknowledged_at
    # A real incompatibility and recovery remain visible, without re-announcing
    # the historical update as if another reload just happened.
    poll("0.4.34", "bad", protocol=999)
    poll("0.4.34", "bad-woke", protocol=999)
    assert len(messages) == 3
    assert "协议不兼容" in messages[-1]
    poll("0.4.34", "recovered")
    assert len(messages) == 4
    assert "已连接" in messages[-1]
    poll("0.4.34", "before-next-update", expected="0.4.35")
    poll("0.4.35", "after-next-update", expected="0.4.35")
    assert len(messages) == 6
    assert "已自动重载并验证：版本 0.4.35" in messages[-1]
    assert bridge.status()["extension"]["reload_acknowledged_at"] > acknowledged_at


def test_reload_requires_a_nonempty_changed_worker_id_and_can_repeat_version_pair() -> None:
    bridge = ChromeBridge(lambda _: None)
    for _ in range(2):
        bridge.claim_command(
            observed_version="0.4.9",
            observed_protocol=COMPANION_PROTOCOL_VERSION,
            worker_instance_id="old-worker",
            expected_version=TEST_EXTENSION_VERSION,
        )
        assert not bridge.status()["extension"]["reload_acknowledged"]
        claim_test_extension(bridge, worker="")
        assert not bridge.status()["extension"]["reload_acknowledged"]
        claim_test_extension(bridge, worker="old-worker")
        assert not bridge.status()["extension"]["reload_acknowledged"]
        claim_test_extension(bridge, worker="new-worker")
        assert bridge.status()["extension"]["reload_acknowledged"]


def test_companion_manifest_version_is_read_dynamically(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"version":"0.4.10"}', encoding="utf-8")
    assert read_companion_extension_version(manifest) == "0.4.10"
    manifest.write_text('{"version":"0.4.11"}', encoding="utf-8")
    assert read_companion_extension_version(manifest) == "0.4.11"
    manifest.write_text('{"version":"not-a-version"}', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid companion extension version"):
        read_companion_extension_version(manifest)


def test_persistent_run_log_can_be_read_in_the_background(tmp_path: Path) -> None:
    run_log = PersistentRunLog(tmp_path / "state" / "onepoint3acres-app.log")
    run_log.append("[09:00:00] start")
    run_log.append("[09:00:01] connected")
    run_log.append("[09:00:02] complete")

    assert run_log.tail(2) == ["[09:00:01] connected", "[09:00:02] complete"]
    assert run_log.tail(10)[0] == "[09:00:00] start"


def test_persistent_run_log_prunes_entries_older_than_seven_days(tmp_path: Path) -> None:
    run_log = PersistentRunLog(tmp_path / "state" / "onepoint3acres-app.log")
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    old = now - timedelta(days=7, seconds=1)
    boundary = now - timedelta(days=7)
    run_log.append(f"[{old.isoformat(sep=' ', timespec='seconds')}] remove")
    run_log.append(f"[{boundary.isoformat(sep=' ', timespec='seconds')}] keep boundary")
    run_log.append(f"[{now.isoformat(sep=' ', timespec='seconds')}] keep recent")

    assert run_log.prune_older_than(now=now) == 1
    assert run_log.tail(10) == [
        f"[{boundary.isoformat(sep=' ', timespec='seconds')}] keep boundary",
        f"[{now.isoformat(sep=' ', timespec='seconds')}] keep recent",
    ]


def test_persistent_run_log_migrates_legacy_time_only_lines(tmp_path: Path) -> None:
    run_log = PersistentRunLog(tmp_path / "state" / "onepoint3acres-app.log")
    run_log.append("[09:00:00] legacy")
    now = datetime.now().astimezone()

    assert run_log.prune_older_than(now=now) == 0
    assert run_log.tail(1)[0].startswith(f"[{now:%Y-%m-%d} 09:00:00")


def test_bridge_background_endpoints_return_status_logs_and_accept_a_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chrome_bridge_module, "BRIDGE_PORT", 0)
    bridge = ChromeBridge(lambda _message: None)
    run_id = bridge.start_run()
    run_requests: list[bool] = []
    server = BridgeServer(
        bridge,
        lambda limit: ["first", "second"][-limit:],
        lambda: run_requests.append(True) is None,
        lambda: TEST_EXTENSION_VERSION,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        request = Request(
            f"http://127.0.0.1:{port}/status?log_limit=1",
            headers={"X-WCA-Token": BRIDGE_TOKEN},
        )
        with urlopen(request, timeout=3) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["ok"]
        assert payload["running"]
        assert payload["run_id"] == run_id
        assert payload["logs"] == ["second"]

        command_request = Request(
            f"http://127.0.0.1:{port}/command?"
            f"extension_version={TEST_EXTENSION_VERSION}&"
            f"protocol_version={COMPANION_PROTOCOL_VERSION}&worker_instance_id=http-test",
            headers={"X-WCA-Token": BRIDGE_TOKEN},
        )
        with urlopen(command_request, timeout=3) as response:  # noqa: S310
            command_payload = json.loads(response.read().decode("utf-8"))
        assert command_payload["action"] == "run_full_auto"
        assert command_payload["run_id"] == run_id
        assert command_payload["compatible"]

        run_request = Request(
            f"http://127.0.0.1:{port}/run",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-WCA-Token": BRIDGE_TOKEN,
            },
            method="POST",
        )
        with urlopen(run_request, timeout=3) as response:  # noqa: S310
            run_payload = json.loads(response.read().decode("utf-8"))
        assert run_payload == {"ok": True, "accepted": True, "error": None}
        assert run_requests == [True]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_companion_extension_is_scoped_and_contains_full_auto_actions() -> None:
    extension = Path(__file__).parents[2] / "chrome_extension"
    manifest = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))
    source = (extension / "service-worker.js").read_text(encoding="utf-8")
    checkin_target = (extension / "checkin-target.js").read_text(encoding="utf-8")
    checkin_state = (extension / "checkin-state.js").read_text(encoding="utf-8")
    trusted_input = (extension / "trusted-input.js").read_text(encoding="utf-8")
    assert manifest["version"] == TEST_EXTENSION_VERSION
    assert manifest["host_permissions"] == [
        "https://www.1point3acres.com/*",
        "https://www.google.com/*",
        "https://chatgpt.com/*",
        "http://127.0.0.1:18732/*",
        "http://127.0.0.1:18731/*",
    ]
    assert "Input.dispatchMouseEvent" in source
    assert "Input.insertText" in source
    assert "await trustedInsertText(" in source
    assert '          "今日签到",' in source
    assert 'textarea[placeholder*="记录今天的点点滴滴"]' in source
    assert '"checkin-state.js"' in source
    assert '"google-search.js"' in source
    assert '"quiz-solve.js"' in source
    assert '"chatgpt-tabs.js"' in source
    assert '"trusted-input.js"' in source
    assert '"version-sync.js"' in source
    assert 'execute(tab.id, locateCheckinTarget, ["note"])' in source
    assert "trustedClick(tab.id, notePoint)" not in source
    assert 'sendCommand("DOM.focus"' in trusted_input
    assert 'sendCommand("Input.dispatchKeyEvent"' in trusted_input
    assert 'sendCommand("Input.insertText"' in trusted_input
    assert "focusAndActivateWithCdp" in trusted_input
    assert 'sendCommand("Runtime.callFunctionOn"' in trusted_input
    assert 'sendCommand("Input.dispatchMouseEvent"' in trusted_input
    assert "chrome.runtime.reload()" in source
    assert "worker_instance_id" in source
    assert 'trustedActivateButton(tab.id, "提交签到")' in source
    assert 'trustedActivateButton(tabId, "提交答案")' in source
    assert "async function resumeQuiz(" in source
    assert 'command.action === "resume_quiz"' in source
    app_source = (
        Path(__file__).parents[2] / "src" / "web_content_agent" / "onepoint3acres_app.py"
    ).read_text(encoding="utf-8")
    assert "[1/8]" in app_source
    for step in range(2, 9):
        assert f"[{step}/8]" in source
    assert (
        'chrome.tabs.create({ url: "https://www.1point3acres.com/home", active: true })' in source
    )
    assert "await chrome.tabs.update(tabId, { url });" in source
    assert 'chrome.windows.update(currentTab.windowId, { state: "normal" })' in source
    assert "chrome.tabs.update(tabId, { active: true })" in source
    assert "controls.choicePoint" not in source
    assert "controls.submitPoint" not in source
    assert "scrollIntoView" not in checkin_state
    assert "恭喜你" in checkin_state
    assert "waitForCheckinState" in checkin_state
    assert "catch (error)" in checkin_state
    assert "waitForCheckinState(() => execute(tab.id, inspectCheckin))" in source
    assert checkin_target.count("scrollIntoView") == 1
    assert "chrome.debugger.attach" in source
    assert "debugger" in manifest["permissions"]
    assert source.count('chrome.alarms.create("wca-poll", { periodInMinutes: 1 });') == 2
    assert "periodInMinutes: 0.5" not in source
    assert "PollScheduler.singleFlight(pollChatGpt)" in source
    assert "command.response_timeout_seconds" in source
    assert 'button.innerText.trim() === "提交签到"' in checkin_state
    assert 'button.innerText.trim() === "提交答案"' in source
    assert ".click()" not in source
    assert "document.cookie" not in source
    assert 'command.action === "ask_chatgpt"' in source
    assert 'command.action === "read_chatgpt_conversation"' in source
    assert "ChatGPT composer does not contain the exact queued question" in source
    assert "composerValueNormalized" in source
    assert "inspectChatGptThinkingMode" in source
    assert "assistantCountBefore" in source
    assert "submitChatGptForm(tab.id, destination)" in source
    assert "ChatGPT submission could not be confirmed" in source
    assert "const CHATGPT_RESPONSE_STABLE_MS = 20000" in source
    assert "const CHATGPT_EXPECTATION_SETTLE_MS = 60000" in source
    assert "停止生成|停止回复" in source
    assert "ChatGptText.responseNodesForSubmittedTurn" in source
    assert (
        "[assistantCountBefore, false, userCountBefore, command.question, "
        "submission.messageId, submission.conversationUrl]" in source
    )
    chatgpt_text = (extension / "chatgpt-text.js").read_text(encoding="utf-8")
    assert "ChatGptText.responseTextCandidates" in source
    assert "clone.querySelectorAll" in chatgpt_text
    assert '[data-testid*="citation"]' in chatgpt_text
    assert "ChatGptText.chooseBestResponse" in source
    assert "const currentTurnCompletionButtons = new Set" in source
    assert "ChatGptText.responseTurn" in source
    assert "copy|copy response|copy answer|复制|复制回复|复制回答" in chatgpt_text
    assert "ChatGptText.responseCompletionSignalReady" in source
    assert "currentTurnCopyActionCount: currentTurnCompletionButtons.size" in source
    assert "ChatGptText.responseExpectationState" in source
    # Completion semantics are exercised by the real worker/DOM contract tests.
    assert "composerIdle" in source
    assert "state.responseComplete" in source
    assert "ChatGptText.responseGeneratingSignalActive" in source
    assert "currentTurnVisibleBusyElements" in source
    assert "unrelatedVisibleBusyCount" in source
    assert "Last observation:" in source
    assert "document.querySelector('[aria-busy=\"true\"]')" not in source
    assert source.count("if (!state) continue;") >= 2
    assert 'files: ["chatgpt-text.js", "chatgpt-tabs.js", "chatgpt-submission.js"]' in source
    assert "if (injection?.error)" in source
    assert "stableFor >= CHATGPT_RESPONSE_STABLE_MS" in source
    assert "stableFor < CHATGPT_EXPECTATION_SETTLE_MS" in source
    assert "The visible response never exposed reliable completion evidence." not in source
    assert "Timed out without reliable current-turn completion evidence." in source
    replace_start = source.index("async function trustedReplaceText")
    submit_start = source.index("async function submitChatGptForm")
    submit_source = source[submit_start : source.index("async function trustedAttachFile")]
    assert "HTMLFormElement.prototype.requestSubmit.call(send.form, send)" in submit_source
    assert "send_button_disabled" in submit_source
    replace_source = source[replace_start:submit_start]
    assert "Input.dispatchMouseEvent" in replace_source
    assert "point.x" in replace_source
    assert '"Runtime.evaluate"' in replace_source
    assert "range.selectNodeContents(composer)" in replace_source
    assert "selected.addRange(range)" in replace_source
    assert 'url.pathname.startsWith("/c/")' in source
    assert "DOM.setFileInputFiles" in source
    assert "attachmentSelected" in source


class FakePage:
    url = "https://www.1point3acres.com/home"

    async def goto(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class FakeAdapter:
    def __init__(self) -> None:
        self.page = FakePage()
        self.signin_clicks = 0
        self.selections = 0
        self.highlights = 0
        self.extraction = parse_quiz_html(
            load_fixture("quiz_labelled.html"), "https://www.1point3acres.com/quiz"
        )

    async def home_state(self) -> DailyPageState:
        return DailyPageState(
            login_state=LoginState.LOGGED_IN,
            signin_available=True,
            already_signed_in=False,
            quiz_available=True,
            already_answered=False,
            quiz_url="https://www.1point3acres.com/next/daily-question",
        )

    async def click_signin_once(self) -> None:
        self.signin_clicks += 1

    async def signin_succeeded(self) -> bool:
        return False

    async def navigate_to_quiz(self, _state: DailyPageState) -> None:
        self.page.url = "https://www.1point3acres.com/next/daily-question"

    async def extract_quiz(self):
        return self.extraction

    async def highlight_choice(self, *_args: object) -> None:
        self.highlights += 1

    async def select_choice(self, *_args: object) -> bool:
        self.selections += 1
        return True


class FakeSolver:
    def solve(self, _quiz: object) -> QuizAnswer:
        return QuizAnswer(
            choice_index=3, choice_text="元组", confidence=0.95, reason="fixture answer"
        )


@pytest.mark.asyncio
async def test_mock_end_to_end_dry_run_has_no_writes(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    output: list[str] = []
    workflow = DailyAssistantWorkflow(
        adapter,  # type: ignore[arg-type]
        WorkflowOptions(dry_run=True, confidence_threshold=0.85, debug_dir=tmp_path, use_llm=True),
        FakeSolver(),  # type: ignore[arg-type]
        input_fn=lambda _prompt: pytest.fail("dry-run must not request mutation confirmation"),
        output=output.append,
    )
    await workflow.run()
    assert adapter.signin_clicks == 0
    assert adapter.selections == 0
    assert adapter.highlights == 0
    assert any("dry-run" in line for line in output)


@pytest.mark.asyncio
async def test_low_confidence_never_selects(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    workflow = DailyAssistantWorkflow(
        adapter,  # type: ignore[arg-type]
        WorkflowOptions(dry_run=False, confidence_threshold=0.85, debug_dir=tmp_path, use_llm=True),
        None,
        input_fn=lambda _prompt: pytest.fail("low confidence must not ask to select"),
        output=lambda _line: None,
    )
    await workflow._handle_answer(
        adapter.extraction,
        QuizAnswer(choice_index=3, choice_text="元组", confidence=0.4, reason="信息不足"),
    )
    assert adapter.selections == 0
    assert adapter.highlights == 0


@pytest.mark.asyncio
async def test_signin_requires_an_exact_empty_enter(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    workflow = DailyAssistantWorkflow(
        adapter,  # type: ignore[arg-type]
        WorkflowOptions(
            dry_run=False, confidence_threshold=0.85, debug_dir=tmp_path, use_llm=False
        ),
        None,
        input_fn=lambda _prompt: "yes",
        output=lambda _line: None,
    )
    assert await workflow._handle_signin(await adapter.home_state())
    assert adapter.signin_clicks == 0


@pytest.mark.asyncio
async def test_scheduled_mode_does_not_prompt_when_logged_out(tmp_path: Path) -> None:
    adapter = FakeAdapter()
    output: list[str] = []
    workflow = DailyAssistantWorkflow(
        adapter,  # type: ignore[arg-type]
        WorkflowOptions(
            dry_run=True,
            confidence_threshold=0.85,
            debug_dir=tmp_path,
            use_llm=True,
            interactive=False,
        ),
        FakeSolver(),  # type: ignore[arg-type]
        input_fn=lambda _prompt: pytest.fail("scheduled mode must not prompt"),
        output=output.append,
    )
    logged_out = DailyPageState(
        login_state=LoginState.LOGGED_OUT,
        signin_available=False,
        already_signed_in=False,
        quiz_available=False,
        already_answered=False,
    )
    assert await workflow._require_login(logged_out) is logged_out
    assert any("立即运行一次" in line for line in output)


def test_gui_module_imports_without_starting_window() -> None:
    from web_content_agent import onepoint3acres_app

    assert callable(onepoint3acres_app.main)


def test_desktop_app_import_does_not_initialize_local_model() -> None:
    project = Path(__file__).parents[2]
    probe = (
        "import sys; "
        "import web_content_agent.onepoint3acres_app; "
        "assert not any(name == 'lmstudio' or name.startswith('lmstudio.') "
        "for name in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    launcher = (project / "scripts" / "start-onepoint3acres-app.ps1").read_text(encoding="utf-8")
    assert "load-model.ps1" not in launcher
    assert "local-agent-vlm" not in launcher


def test_gui_log_styles_keep_failures_and_successes_distinct() -> None:
    from web_content_agent.onepoint3acres_app import (
        SCHEDULE_CHECK_INTERVAL_MS,
        UI_REFRESH_INTERVAL_MS,
        format_local_clock,
        log_style_for_message,
    )

    assert log_style_for_message("签到成功") == "success"
    assert log_style_for_message("自动运行停止：页面错误") == "error"
    assert log_style_for_message("等待 Chrome companion extension") == "accent"
    assert log_style_for_message("普通运行信息") == "normal"
    now = datetime(2026, 8, 29, 9, 5, tzinfo=UTC)
    assert format_local_clock(now) == "2026-08-29 09:05:00 · UTC+00:00"
    assert UI_REFRESH_INTERVAL_MS == 1_000
    assert SCHEDULE_CHECK_INTERVAL_MS == 10 * 60 * 1_000


def test_background_run_request_is_queued_once() -> None:
    from web_content_agent.onepoint3acres_app import OnePointThreeAcresApp

    app = object.__new__(OnePointThreeAcresApp)
    app._request_lock = threading.Lock()
    app.close_request_pending = False
    app.running = False
    app.run_request_pending = False
    app.events = queue.Queue()

    assert app._queue_background_run()
    assert not app._queue_background_run()
    assert app.events.get_nowait() == ("run_request", None)


def test_simulation_and_real_run_are_mutually_exclusive() -> None:
    from web_content_agent.onepoint3acres_app import OnePointThreeAcresApp

    app = object.__new__(OnePointThreeAcresApp)
    app._request_lock = threading.Lock()
    app.close_request_pending = False
    app.running = False
    app.run_request_pending = False
    app.events = queue.Queue()
    payload = {"question": "Q?", "choices": ["A", "B"]}
    assert app._queue_simulation(payload)
    assert not app._queue_simulation(payload)
    assert not app._queue_background_run()
    assert app.events.get_nowait() == ("simulate_request", payload)


@pytest.mark.parametrize("fail", [False, True])
def test_simulation_never_dispatches_to_chrome_and_always_finishes(monkeypatch, fail) -> None:
    bridge = ChromeBridge(lambda _: None)

    def solve(run_id, payload):
        assert bridge.status()["running"]
        assert bridge.status()["quiz_solve_status"] == "pending"
        # Even repeated Chrome polling during inference cannot claim/resume this run.
        for _ in range(2):
            command = bridge.claim_command(
                observed_version=TEST_EXTENSION_VERSION,
                observed_protocol=COMPANION_PROTOCOL_VERSION,
                worker_instance_id="simulation-test",
                expected_version=TEST_EXTENSION_VERSION,
            )
            assert command["action"] == "idle"
            assert "run_id" not in command
        with pytest.raises(RuntimeError, match="已有"):
            bridge.start_run()
        if fail:
            raise RuntimeError("low confidence")
        return {"choice_index": 1, "confidence": 0.95}

    monkeypatch.setattr(bridge, "solve", solve)
    result = bridge.simulate({"question": "Q?", "choices": ["A", "B"]})
    assert result["ok"] is not fail
    assert result["simulation"] is True
    assert result["website_actions"] == 0
    assert not bridge.status()["running"]
    assert bridge.status()["quiz_solve_status"] == ("error" if fail else "complete")
    # A later real run is not accidentally stuck in simulation mode.
    bridge.start_run()
    assert not bridge.status()["simulation"]


def test_simulation_http_requires_auth_validates_input_and_reports_busy(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(chrome_bridge_module, "BRIDGE_PORT", 0)
    queued = []

    def enqueue(payload):
        if queued:
            return False
        queued.append(payload)
        return True

    server = BridgeServer(ChromeBridge(lambda _: None), request_simulation=enqueue)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    payload = {"question": "Q?", "choices": ["A", "B"]}
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}") as client:
            assert client.post("/simulate", json=payload).status_code == 403
            client.headers["X-WCA-Token"] = BRIDGE_TOKEN
            assert client.post("/simulate", json={}).status_code == 400
            assert client.post("/simulate", json={**payload, "choices": [1, 2]}).status_code == 400
            assert (
                client.post("/simulate", json={**payload, "expected_choice_index": 1}).status_code
                == 400
            )
            response = client.post("/simulate", json=payload)
            assert response.status_code == 202
            assert response.json()["simulation"]
            assert queued == [{**payload, "web_evidence": []}]
            assert client.post("/simulate", json=payload).status_code == 409
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_desktop_scheduler_is_disabled_in_codex_review_mode() -> None:
    from web_content_agent.onepoint3acres_app import (
        OnePointThreeAcresApp,
    )

    scheduled_callbacks: list[tuple[int, object]] = []

    class FakeRoot:
        def after(self, delay_ms: int, callback: object) -> None:
            scheduled_callbacks.append((delay_ms, callback))

    class FakeLedger:
        def is_due(self, _now: datetime) -> bool:
            return False

    app = object.__new__(OnePointThreeAcresApp)
    app.root = FakeRoot()
    app.ledger = FakeLedger()
    app.running = False

    app._schedule_tick()

    assert scheduled_callbacks == []


def test_adapter_has_no_final_answer_submission_action() -> None:
    source = (
        Path(__file__).parents[2] / "src" / "web_content_agent" / "onepoint3acres" / "site.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    click_methods = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            isinstance(child, ast.Attribute) and child.attr in {"click", "check"}
            for child in ast.walk(node)
        )
    }
    assert click_methods == {"click_signin_once", "select_choice"}
