from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from web_content_agent.config import ModelConfig
from web_content_agent.context_budget import ContextBudgetExceeded, enforce_context_budget
from web_content_agent.lmstudio import LMStudioClient


def test_context_budget_allows_exact_boundary() -> None:
    usage = enforce_context_budget(
        prompt_tokens=6000,
        output_tokens=1000,
        safety_margin_tokens=1000,
        context_length=8000,
    )
    assert usage.remaining_tokens == 0
    assert usage.total_reserved_tokens == 8000


def test_context_budget_rejects_one_token_over_before_inference() -> None:
    with pytest.raises(ContextBudgetExceeded, match="over_by=1"):
        enforce_context_budget(
            prompt_tokens=6001,
            output_tokens=1000,
            safety_margin_tokens=1000,
            context_length=8000,
        )


def test_context_budget_reserves_vision_tokens() -> None:
    usage = enforce_context_budget(
        prompt_tokens=1000,
        output_tokens=1000,
        image_count=2,
        vision_tokens_per_image=2000,
        safety_margin_tokens=1000,
        context_length=10000,
    )
    assert usage.vision_reserve_tokens == 4000
    assert usage.remaining_tokens == 3000


def test_client_rejects_oversized_payload_without_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LMStudioClient(ModelConfig(context_length=4096, context_safety_margin_tokens=512))
    monkeypatch.setattr(
        client,
        "_budget_history",
        lambda messages: ([{"role": "user", "content": "large"}], 0),
    )

    class FakeModel:
        def apply_prompt_template(self, history: dict) -> str:
            return "rendered"

        def count_tokens(self, prompt: str) -> int:
            return 3585

        def get_context_length(self) -> int:
            return 4096

    @contextmanager
    def tokenizer():
        yield SimpleNamespace(llm=SimpleNamespace(model=lambda identifier: FakeModel()))

    monkeypatch.setattr("web_content_agent.lmstudio.lmstudio.Client", tokenizer)
    with pytest.raises(ContextBudgetExceeded, match="over_by=1"):
        client._ensure_context_budget(
            {"messages": [{"role": "user", "content": "large"}], "max_tokens": 0}
        )


def test_tokenizer_connection_failure_still_enforces_budget(monkeypatch):
    def disconnected():
        raise RuntimeError("stale websocket")

    monkeypatch.setattr("web_content_agent.lmstudio.lmstudio.Client", disconnected)
    client = LMStudioClient(ModelConfig(context_length=4096, context_safety_margin_tokens=512))
    with pytest.raises(ContextBudgetExceeded):
        client._ensure_context_budget(
            {"messages": [{"role": "user", "content": "中" * 2000}], "max_tokens": 1024}
        )
