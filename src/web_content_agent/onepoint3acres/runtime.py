from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from web_content_agent.browser import BrowserController
from web_content_agent.config import BrowserConfig, ModelConfig
from web_content_agent.lmstudio import LMStudioClient
from web_content_agent.logging_setup import EventLogger
from web_content_agent.paths import project_root

from .knowledge import knowledge_for
from .model_selection import model_lease, quiz_model, selected_model
from .models import DailyQuiz, QuizAnswer
from .solver import PROMPT_VERSION, QuizSolver


def local_root() -> Path:
    return project_root() / ".local"


def _env(name: str, fallback: str, default: str) -> str:
    return os.getenv(name) or os.getenv(fallback) or default


def create_browser(
    *,
    profile_dir: Path | None = None,
    debug: bool = False,
) -> tuple[BrowserController, Path]:
    root = local_root()
    debug_dir = root / "debug" / "onepoint3acres"
    browser = BrowserController(
        BrowserConfig(
            headless=False,
            user_agent=None,
            locale=None,
            trace_enabled=False,
            min_navigation_delay_seconds=1,
            max_navigation_delay_seconds=2,
        ),
        profile_dir=(profile_dir or root / "browser_profiles" / "onepoint3acres").resolve(),
        trace_path=debug_dir / "disabled-trace.zip",
        access_state_path=root / "state" / "onepoint3acres-access.json",
        event_logger=EventLogger(debug_dir / "events.jsonl" if debug else None),
    )
    return browser, debug_dir


def _default_solver() -> QuizSolver:
    model_config = ModelConfig(
        base_url=_env("LM_STUDIO_BASE_URL", "WCA_LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1"),
        identifier=_env("LM_STUDIO_MODEL", "WCA_MODEL_IDENTIFIER", "local-agent-vlm"),
        timeout_seconds=float(os.getenv("WCA_LLM_TIMEOUT_SECONDS", "900")),
        max_retries=1,
    )
    return QuizSolver(LMStudioClient(model_config))


class AppQuizSolver:
    def __init__(
        self,
        output: Callable[[str], None],
        record: Callable[[dict], None] = lambda _: None,
        *,
        advisory: bool = False,
    ) -> None:
        self.key = selected_model()
        self.output = output
        self.record = record
        self.advisory = advisory

    def solve(self, quiz: DailyQuiz) -> QuizAnswer:
        policy = (
            "本地结果仅供 Codex 审核；依据不足会如实标记，不能自动提交。"
            if self.advisory
            else "依据不足则跳过，置信度不能覆盖此检查。"
        )
        self.output(f"[6/8] 答题规则版本：{PROMPT_VERSION}；{policy}")
        # Production and simulations retrieve the same facts. Do not accept an
        # answer key or caller-supplied local notes in place of this lookup.
        quiz = quiz.model_copy(
            update={
                "local_knowledge": knowledge_for(quiz, output=self.output),
            }
        )
        self.record(
            {
                "quiz": quiz.model_dump(),
                "model_key": self.key or "local-agent-vlm",
                "prompt_version": PROMPT_VERSION,
            }
        )
        with model_lease():
            if self.key is None:
                solver = _default_solver()
                self.output(
                    f"[6/8] 使用 WebContentAgent 默认模型：{solver.client.config.identifier}"
                )
                return solver.solve(quiz, advisory=True) if self.advisory else solver.solve(quiz)
            with quiz_model(self.key, self.output) as config:
                self.output("[6/8] 答题模型已就绪，开始两轮推理……")
                audit_path = (
                    local_root()
                    / "debug/onepoint3acres"
                    / (f"quiz-model-{datetime.now():%Y-%m-%d-%H%M%S-%f}.jsonl")
                )
                self.output(
                    f"[6/8] 模型诊断记录：{audit_path.name}（仅最终输出，不保存内部推理）。"
                )
                solver = QuizSolver(LMStudioClient(config, audit_path=audit_path))
                return solver.solve(quiz, advisory=True) if self.advisory else solver.solve(quiz)


def create_solver(
    output: Callable[[str], None] = lambda _: None,
    record: Callable[[dict], None] = lambda _: None,
    *,
    advisory: bool = False,
) -> AppQuizSolver:
    return AppQuizSolver(output, record, advisory=advisory)
