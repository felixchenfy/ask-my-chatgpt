import json

import pytest

from web_content_agent.config import ModelConfig
from web_content_agent.lmstudio import LMStudioClient, ModelOutputInvalid
from web_content_agent.onepoint3acres.models import DailyQuiz, QuizAnswer
from web_content_agent.onepoint3acres.solver import QuizSolver


@pytest.mark.parametrize("gemma", [False, True])
def test_native_json_is_scoped_to_gemma_quiz_without_reducing_reasoning(monkeypatch, gemma):
    config = ModelConfig(model_key="google/gemma-4-12b-qat") if gemma else ModelConfig()
    client = LMStudioClient(config)
    calls = []
    answer = dict(
        choice_index=2,
        choice_text="这些都是",
        confidence=0.9,
        reason="各项符合规则",
        evidence_sufficient=True,
        evidence_claims=[],
    )

    def post(payload, purpose):
        calls.append(payload)
        return json.dumps(answer, ensure_ascii=False)

    monkeypatch.setattr(client, "_post", post)
    quiz = DailyQuiz(
        question="题目",
        choices=[{"index": 1, "text": "选项"}, {"index": 2, "text": "这些都是"}],
        source_url="fixture",
    )
    assert QuizSolver(client).solve(quiz).choice_index == 2
    assert len(calls) == 2
    for payload in calls:
        assert payload["reasoning_effort"] == "high"
        assert payload["response_format"]["type"] == ("json_schema" if gemma else "text")
        if gemma:
            schema = payload["response_format"]["json_schema"]["schema"]
            assert schema["properties"]["choice_index"]["maximum"] == 2
            assert schema["properties"]["choice_text"]["enum"] == ["选项", "这些都是"]
            assert (
                schema["$defs"]["QuizEvidenceClaim"]["properties"]["choice_index"]["maximum"] == 2
            )
            assert schema["properties"]["evidence_claims"]["maxItems"] == 2
        system_prompt = "\n".join(
            message["content"] for message in payload["messages"] if message["role"] == "system"
        )
        assert "这些都包括/全都包括/上述原因均包括" in system_prompt
        assert "不能再以某一项‘更直接’或‘更精准’为由单选它" in system_prompt
        assert "不得声称某条证据‘明确指出’一个事实" in system_prompt
        assert "题目选项、常识或自己的推断冒充成搜索摘要" in system_prompt
        assert "关键动作、渠道和前提上相符" in system_prompt
        assert "证据只说通过邮件找回" in system_prompt
        assert "不得输出 original_knowledge_quote" in system_prompt
        assert "evidence_claims 要逐项填写" in system_prompt
        assert "恰好覆盖每个选项一次" in system_prompt
        assert "不能固定选择第一项或任意占位" in system_prompt
    calls.clear()
    client.structured(QuizAnswer, [], "other_task")
    assert calls[0]["response_format"] == {"type": "text"}
    assert calls[0]["reasoning_effort"] == "none"


def test_empty_final_content_has_actionable_metadata_not_a_fabricated_answer(monkeypatch):
    client = LMStudioClient(ModelConfig(max_retries=0))
    client.last_response_metadata = {"finish_reason": "length", "output_characters": 0}
    monkeypatch.setattr(client, "_post", lambda *_: "")
    with pytest.raises(ModelOutputInvalid, match="empty final content.*finish_reason=length"):
        client.structured(QuizAnswer, [], "fixture", validation_retries=0)


def test_invalid_evidence_quote_downgrades_a_claimed_sufficient_answer():
    quiz = DailyQuiz(
        question="规则题",
        choices=[{"index": 1, "text": "A"}, {"index": 2, "text": "B"}],
        source_url="fixture",
        supplementary="只支持 B",
    )
    unsupported = QuizAnswer(
        choice_index=1,
        choice_text="A",
        confidence=0.8,
        reason="依据不足，仅作占位",
        evidence_sufficient=False,
        evidence_claims=[
            {"choice_index": 1, "status": "unknown", "evidence_quote": "并不存在的引用"},
            {"choice_index": 2, "status": "unknown", "evidence_quote": None},
        ],
    )
    QuizSolver.validate_answer(unsupported, quiz)
    assert unsupported.evidence_claims[0].status == "unknown"
    assert unsupported.evidence_claims[0].evidence_quote is None
    supported = QuizAnswer(
        choice_index=1,
        choice_text="A",
        confidence=0.9,
        reason="声称依据充分",
        evidence_sufficient=True,
        evidence_claims=[
            {"choice_index": 1, "status": "supported", "evidence_quote": "并不存在的引用"},
            {"choice_index": 2, "status": "unknown", "evidence_quote": None},
        ],
    )
    QuizSolver.validate_answer(supported, quiz)
    assert not supported.evidence_sufficient
    assert supported.confidence == 0
    assert "不可自动提交" in supported.reason
    assert supported.evidence_claims[0].status == "unknown"
    assert supported.evidence_claims[0].evidence_quote is None


@pytest.mark.parametrize(
    "claims",
    [
        [{"choice_index": 1, "status": "unknown", "evidence_quote": None}],
        [
            {"choice_index": 1, "status": "supported", "evidence_quote": "一条外部资料"},
            {"choice_index": 1, "status": "unknown", "evidence_quote": None},
        ],
        [
            {"choice_index": 1, "status": "unknown", "evidence_quote": None},
            {"choice_index": 3, "status": "unknown", "evidence_quote": None},
        ],
    ],
)
def test_supplied_evidence_repairs_claim_coverage_but_disables_local_submission(claims):
    quiz = DailyQuiz(
        question="规则题",
        choices=[{"index": 1, "text": "A"}, {"index": 2, "text": "B"}],
        source_url="fixture",
        supplementary="一条外部资料",
    )
    answer = QuizAnswer(
        choice_index=1,
        choice_text="A",
        confidence=0.8,
        reason="依据不足",
        evidence_sufficient=False,
        evidence_claims=claims,
    )

    QuizSolver.validate_answer(answer, quiz)

    assert [claim.choice_index for claim in answer.evidence_claims] == [1, 2]
    assert answer.evidence_sufficient is False
    assert answer.confidence == 0
    assert "不可自动提交" in answer.reason


def test_choice_validation_accepts_only_harmless_quote_glyph_changes_and_restores_exact_text():
    quiz = DailyQuiz(
        question="如何操作？",
        choices=[
            {"index": 1, "text": "忽略"},
            {"index": 2, "text": "点楼层内”举报“"},
        ],
        source_url="fixture",
    )
    answer = QuizAnswer(
        choice_index=2,
        choice_text="点楼层内“举报”",
        confidence=0.5,
        reason="引号字形被模型规范化。",
    )
    QuizSolver.validate_answer(answer, quiz)
    assert answer.choice_text == "点楼层内”举报“"

    changed = answer.model_copy(update={"choice_text": "点楼层内举报并私信"})
    with pytest.raises(ValueError, match="choice_index 与 choice_text 不对应"):
        QuizSolver.validate_answer(changed, quiz)


def test_generation_artifact_in_reason_is_replaced_and_cannot_be_sufficient():
    quiz = DailyQuiz(
        question="题目",
        choices=[{"index": 1, "text": "A"}, {"index": 2, "text": "B"}],
        source_url="fixture",
    )
    answer = QuizAnswer(
        choice_index=1,
        choice_text="A",
        confidence=0.9,
        reason="支持 A original_knowledge_quote_quote_quote_quote_quote_quote_quote_quote",
        evidence_sufficient=True,
    )

    QuizSolver.validate_answer(answer, quiz)

    assert answer.evidence_sufficient is False
    assert answer.confidence == 0
    assert answer.reason == "本地模型输出含模板残留或重复填充文字；仅作占位，不可自动提交。"


def test_long_repeated_digit_artifact_in_reason_is_replaced():
    quiz = DailyQuiz(
        question="题目",
        choices=[{"index": 1, "text": "A"}, {"index": 2, "text": "B"}],
        source_url="fixture",
    )
    answer = QuizAnswer(
        choice_index=1,
        choice_text="A",
        confidence=0.9,
        reason="支持 A，但随后出现坏尾巴 20" + "40" * 30,
        evidence_sufficient=True,
    )

    QuizSolver.validate_answer(answer, quiz)

    assert answer.evidence_sufficient is False
    assert answer.confidence == 0
    assert answer.reason == "本地模型输出含模板残留或重复填充文字；仅作占位，不可自动提交。"


def test_repeated_word_artifact_in_reason_is_replaced():
    quiz = DailyQuiz(
        question="题目",
        choices=[{"index": 1, "text": "A"}, {"index": 2, "text": "B"}],
        source_url="fixture",
    )
    answer = QuizAnswer(
        choice_index=1,
        choice_text="A",
        confidence=0.9,
        reason="Option B involves " + ", ".join(["net-bullying"] * 8),
        evidence_sufficient=True,
    )

    QuizSolver.validate_answer(answer, quiz)

    assert answer.evidence_sufficient is False
    assert answer.confidence == 0
    assert answer.reason == "本地模型输出含模板残留或重复填充文字；仅作占位，不可自动提交。"


def test_contiguous_chinese_phrase_artifact_in_reason_is_replaced():
    quiz = DailyQuiz(
        question="题目",
        choices=[{"index": 1, "text": "A"}, {"index": 2, "text": "B"}],
        source_url="fixture",
    )
    answer = QuizAnswer(
        choice_index=1,
        choice_text="A",
        confidence=0.8,
        reason="依据不足：" + "补充证据中" * 20,
        evidence_sufficient=False,
    )

    QuizSolver.validate_answer(answer, quiz)

    assert answer.evidence_sufficient is False
    assert answer.confidence == 0
    assert answer.reason == "本地模型输出含模板残留或重复填充文字；仅作占位，不可自动提交。"


def test_long_newline_artifact_in_reason_is_replaced():
    quiz = DailyQuiz(
        question="题目",
        choices=[{"index": 1, "text": "A"}, {"index": 2, "text": "B"}],
        source_url="fixture",
    )
    answer = QuizAnswer(
        choice_index=1,
        choice_text="A",
        confidence=0.8,
        reason="依据不足" + "\n" * 40 + "0.8",
        evidence_sufficient=False,
    )

    QuizSolver.validate_answer(answer, quiz)

    assert answer.evidence_sufficient is False
    assert answer.confidence == 0
    assert answer.reason == "本地模型输出含模板残留或重复填充文字；仅作占位，不可自动提交。"


def test_mutating_repeated_phrase_artifact_in_reason_is_replaced():
    quiz = DailyQuiz(
        question="题目",
        choices=[{"index": 1, "text": "A"}, {"index": 2, "text": "B"}],
        source_url="fixture",
    )
    repeated = "".join(
        f"本地知识中提到大米用于阅读权限、站内搜索、附件下载等，第{index}段略有变化。"
        for index in range(8)
    )
    answer = QuizAnswer(
        choice_index=1,
        choice_text="A",
        confidence=0.8,
        reason=repeated,
        evidence_sufficient=False,
    )

    QuizSolver.validate_answer(answer, quiz)

    assert answer.evidence_sufficient is False
    assert answer.confidence == 0
    assert answer.reason == "本地模型输出含模板残留或重复填充文字；仅作占位，不可自动提交。"
