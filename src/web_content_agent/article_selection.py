from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from pydantic import BaseModel, Field, field_validator

TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}


def canonicalize_url(url: str, base_url: str | None = None) -> str:
    absolute = urljoin(base_url or url, url)
    parts = urlsplit(absolute)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        host = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
    ]
    return urlunsplit((scheme, host, path, urlencode(sorted(query)), ""))


def safe_slug(value: str, max_length: int = 72) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:max_length].rstrip("-") or "article"


def safe_filename(value: str, fallback: str = "file") -> str:
    stem = Path(value).name
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", stem).strip(" .")
    return stem[:120] or fallback


def safe_directory_name(value: str, fallback: str = "item", max_length: int = 80) -> str:
    """Create a readable, Windows-safe course directory component."""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^a-zA-Z0-9]+", "_", normalized).strip("_ .")
    name = re.sub(r"_{2,}", "_", name)
    return name[:max_length].rstrip("_ .") or fallback


class LinkCandidate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    url: str
    prominence: int = 0
    context: str = ""
    section_number: int | None = Field(default=None, ge=1)
    section_title: str | None = Field(default=None, max_length=300)
    lesson_number: int | None = Field(default=None, ge=1)


class SelectedArticle(BaseModel):
    rank: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=300)
    url: str
    reason: str = Field(min_length=8, max_length=500)
    section_number: int | None = Field(default=None, ge=1)
    section_title: str | None = Field(default=None, max_length=300)
    lesson_number: int | None = Field(default=None, ge=1)

    @field_validator("url")
    @classmethod
    def absolute_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("Article URL must be absolute")
        return canonicalize_url(value)


class ArticleSelection(BaseModel):
    articles: list[SelectedArticle]

    @field_validator("articles")
    @classmethod
    def ordered_unique(cls, articles: list[SelectedArticle]) -> list[SelectedArticle]:
        if not articles:
            raise ValueError("At least one content item is required")
        if {item.rank for item in articles} != set(range(1, len(articles) + 1)):
            raise ValueError("Ranks must be contiguous starting at 1")
        urls = [item.url for item in articles]
        if len(set(urls)) != len(urls):
            raise ValueError("Content URLs must be unique")
        return sorted(articles, key=lambda item: item.rank)


class ImageDecision(BaseModel):
    category: Literal[
        "lead_photo",
        "article_figure",
        "chart",
        "diagram",
        "screenshot",
        "decorative",
        "logo",
        "icon",
        "avatar",
        "advertisement",
        "tracking",
        "unknown",
    ]
    decision: Literal["keep", "drop", "review"]
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=3, max_length=500)


def safe_image_decision(raw: object, error: str | None = None) -> ImageDecision:
    try:
        decision = ImageDecision.model_validate(raw)
    except Exception as exc:
        detail = str(error or exc).replace("\n", " ")[:400]
        return ImageDecision(
            category="unknown",
            decision="review",
            confidence=0,
            reason=f"Model output invalid; raw image retained safely: {detail}",
        )
    if decision.category == "unknown" or decision.confidence < 0.65:
        return decision.model_copy(
            update={
                "decision": "review",
                "reason": (
                    f"{decision.reason} Low confidence/unknown defaults to retain for review."
                ),
            }
        )
    return decision
