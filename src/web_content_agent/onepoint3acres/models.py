from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


class LoginState(StrEnum):
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    VERIFICATION_REQUIRED = "verification_required"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SemanticControl:
    role: str
    text: str
    href: str | None = None


@dataclass(frozen=True)
class DailyPageState:
    login_state: LoginState
    signin_available: bool
    already_signed_in: bool
    quiz_available: bool
    already_answered: bool
    signin_locator_description: str | None = None
    quiz_url: str | None = None

    @property
    def logged_in(self) -> bool:
        return self.login_state is LoginState.LOGGED_IN


class QuizChoice(BaseModel):
    index: int = Field(ge=1)
    label: str | None = None
    text: str = Field(min_length=1)


class DailyQuiz(BaseModel):
    question: str = Field(min_length=1)
    choices: list[QuizChoice] = Field(min_length=2)
    source_url: str
    supplementary: str | None = None
    local_knowledge: list[dict[str, str]] = Field(default_factory=list)


class QuizEvidenceClaim(BaseModel):
    choice_index: int = Field(ge=1)
    status: Literal["supported", "contradicted", "unknown"]
    evidence_quote: str | None = Field(default=None, max_length=240)


class QuizAnswer(BaseModel):
    choice_index: int = Field(ge=1)
    choice_text: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)
    evidence_sufficient: bool = Field(
        default=False,
        description="是否有足够依据区分正确答案与合理竞争选项；证据缺失或冲突时为 false",
    )
    evidence_claims: list[QuizEvidenceClaim] = Field(
        default_factory=list,
        max_length=8,
        description="逐项证据状态；引用必须逐字来自提供的外部证据，不能复制题目选项冒充证据",
    )


@dataclass(frozen=True)
class ChoiceTarget:
    kind: str
    dom_index: int


@dataclass(frozen=True)
class QuizExtraction:
    quiz: DailyQuiz
    targets: tuple[ChoiceTarget, ...]


@dataclass
class InteractionGuard:
    dry_run: bool
    signin_clicks: int = 0
    answer_selections: int = 0

    def authorize_signin(self) -> None:
        if self.dry_run:
            raise PermissionError("dry-run 模式禁止签到点击")
        if self.signin_clicks >= 1:
            raise PermissionError("每次运行最多执行一次签到点击")
        self.signin_clicks += 1

    def authorize_answer_selection(self) -> None:
        if self.dry_run:
            raise PermissionError("dry-run 模式禁止选择答案")
        if self.answer_selections >= 1:
            raise PermissionError("每次运行最多执行一次答案选择")
        self.answer_selections += 1
