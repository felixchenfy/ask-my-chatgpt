from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class ModelConfig(BaseModel):
    base_url: str = "http://127.0.0.1:1234/v1"
    identifier: str = "local-agent-vlm"
    model_key: str = (
        "DavidAU/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-"
        "MAX-MTP-GGUF/Qwen3.5-9B-The-Defiant-Fable-Uncnr-Heretic-NEO-MAX-Q6_K.gguf"
    )
    quant: str = "Q6_K"
    context_length: int = 32768
    idle_ttl_seconds: int = Field(default=300, ge=60, le=86400)
    context_safety_margin_tokens: int = Field(default=2048, ge=256)
    vision_tokens_per_image: int = Field(default=4096, ge=256)
    timeout_seconds: float = 120.0
    max_retries: int = 2

    @field_validator("base_url")
    @classmethod
    def localhost_only(cls, value: str) -> str:
        host = urlparse(value).hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError(
                "LM Studio base_url must be localhost; cloud model endpoints are forbidden"
            )
        return value.rstrip("/")


class BrowserConfig(BaseModel):
    headless: bool = True
    navigation_timeout_ms: int = 45000
    settle_ms: int = 1500
    min_navigation_delay_seconds: float = Field(default=2.0, ge=0, le=1800)
    max_navigation_delay_seconds: float = Field(default=5.0, ge=0, le=1800)
    image_request_delay_seconds: float = Field(default=0.0, ge=0, le=60)
    daily_page_limit: int = Field(default=500, ge=1, le=10000)
    pacing_mode: Literal["uniform", "adaptive_polite"] = "adaptive_polite"
    long_pause_probability: float = Field(default=0.12, ge=0, le=0.5)
    access_pattern_seed: int | None = None
    user_agent: str | None = "WebContentAgent/0.2 (polite local public-content research)"
    locale: str | None = "en-US"
    trace_enabled: bool = True

    @model_validator(mode="after")
    def valid_delay_range(self) -> BrowserConfig:
        if self.max_navigation_delay_seconds < self.min_navigation_delay_seconds:
            raise ValueError("max_navigation_delay_seconds must be >= minimum")
        return self


class SourceConfig(BaseModel):
    schema_version: int = 1
    name: str
    site_name: str
    homepage_url: str
    discovery_url: str | None = None
    allowed_domains: list[str]
    collection_dir: Path | None = None
    strategy: Literal["auto", "news", "course"] = "auto"
    max_items: int | None = Field(default=None, ge=1)
    max_articles: int | None = Field(default=None, ge=1, exclude=True)
    instructions: str = Field(default="", max_length=4000)
    require_course_hierarchy: bool = False
    model: ModelConfig = Field(default_factory=ModelConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    source_path: Path | None = Field(default=None, exclude=True)

    @field_validator("homepage_url", "discovery_url")
    @classmethod
    def http_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("homepage_url/discovery_url must be an absolute HTTP(S) URL")
        return value

    @model_validator(mode="after")
    def homepage_is_allowed(self) -> SourceConfig:
        for label, url in (("homepage", self.homepage_url), ("discovery", self.discovery_url)):
            if not url:
                continue
            host = (urlparse(url).hostname or "").lower()
            if not any(
                host == domain or host.endswith(f".{domain}") for domain in self.allowed_domains
            ):
                raise ValueError(f"{label} host is not in allowed_domains")
        return self

    @model_validator(mode="after")
    def resolve_legacy_limit(self) -> SourceConfig:
        if self.max_items is None:
            self.max_items = self.max_articles
        if self.max_items is None and self.resolved_strategy() == "news":
            self.max_items = 3
        return self

    def resolved_strategy(self) -> Literal["news", "course"]:
        if self.strategy != "auto":
            return self.strategy
        signal = f"{self.homepage_url} {self.name} {self.instructions}".lower()
        course_hints = ("/course", "lesson", "chapter", "curriculum", "课程", "章节", "课时")
        return "course" if any(hint in signal for hint in course_hints) else "news"

    def resolved_collection_dir(self) -> Path:
        if self.collection_dir:
            return self.collection_dir.resolve()
        if not self.source_path:
            raise ValueError("source_path is required to infer collection_dir")
        return self.source_path.parent.resolve()


def load_source_config(path: str | Path) -> SourceConfig:
    source_path = Path(path).expanduser().resolve()
    data = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Source configuration must be a mapping: {source_path}")
    if os.getenv("WCA_MODEL_IDENTIFIER"):
        data.setdefault("model", {})["identifier"] = os.environ["WCA_MODEL_IDENTIFIER"]
    if os.getenv("WCA_LMSTUDIO_BASE_URL"):
        data.setdefault("model", {})["base_url"] = os.environ["WCA_LMSTUDIO_BASE_URL"]
    config = SourceConfig.model_validate(data)
    config.source_path = source_path
    return config
