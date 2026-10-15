"""Small, dated source notes; retrieval supplies facts, never a quiz answer key."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from web_content_agent.paths import default_workspace_root

from .models import DailyQuiz


class KnowledgeNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str = Field(min_length=1, max_length=100)
    keywords: list[str] = Field(min_length=1, max_length=20)
    source_url: str = Field(max_length=2048)
    verified_on: date
    review_after: date
    facts: list[str] = Field(min_length=1, max_length=12)


def knowledge_for(
    quiz: DailyQuiz,
    *,
    workspace: Path | None = None,
    today: date | None = None,
    output: Callable[[str], None] = lambda _: None,
) -> list[dict[str, str]]:
    root = (workspace or default_workspace_root()).resolve()
    folder = root / "knowledge/onepoint3acres"
    now = today or date.today()
    query = (quiz.question + " " + " ".join(c.text for c in quiz.choices)).casefold()
    notes = []
    for path in sorted(folder.glob("*.json"))[:20]:
        try:
            if not path.resolve().is_relative_to(root) or path.stat().st_size > 32_768:
                raise ValueError("知识文件越界或过大")
            note = KnowledgeNote.model_validate_json(path.read_text(encoding="utf-8"))
            url = urlsplit(note.source_url)
            if url.scheme != "https" or url.hostname != "www.1point3acres.com":
                raise ValueError("知识来源必须是一亩三分地官方 HTTPS 页面")
            if any(not k.strip() or len(k) > 40 for k in note.keywords):
                raise ValueError("知识关键词无效")
            if any(not f.strip() or len(f) > 400 for f in note.facts):
                raise ValueError("知识事实为空或过长")
            if not any(k.casefold() in query for k in note.keywords):
                continue
            if (
                not note.verified_on
                <= now
                <= min(note.review_after, note.verified_on + timedelta(days=30))
            ):
                output(f"[6/8] 本地知识需重新核验，未使用：{path.name}")
                continue
            notes.append(
                {
                    "topic": note.topic,
                    "source_url": note.source_url,
                    "verified_on": note.verified_on.isoformat(),
                    "review_after": note.review_after.isoformat(),
                    "facts": "\n".join(note.facts),
                }
            )
            output(
                f"[6/8] 已读取本地知识：{note.topic}；核验 {note.verified_on}；{note.source_url}"
            )
            if len(notes) == 4:
                break
        except (OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            output(f"[6/8] 本地知识未使用：{path.name}（{type(exc).__name__}）")
    if not notes:
        output("[6/8] 无匹配且有效的本地知识；未知事实仍须保留为未知，不能靠猜测提交。")
    return notes
