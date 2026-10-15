from __future__ import annotations

import base64
import json
import mimetypes
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import httpx
import lmstudio
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .article_selection import ArticleSelection, ImageDecision, safe_image_decision
from .config import ModelConfig
from .context_budget import ContextUsage, enforce_context_budget
from .logging_setup import EventLogger, redact

T = TypeVar("T", bound=BaseModel)


class LMStudioError(RuntimeError):
    pass


class ModelServerUnavailable(LMStudioError):
    pass


class ModelOutputInvalid(LMStudioError):
    pass


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        stripped = stripped.rsplit("```", 1)[0].strip()
    try:
        result = json.loads(stripped)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        result = json.loads(stripped[start : end + 1])
        if isinstance(result, dict):
            return result
    raise ModelOutputInvalid("Model response did not contain one valid JSON object")


class LMStudioClient:
    def __init__(
        self,
        config: ModelConfig,
        audit_path: Path | None = None,
        event_logger: EventLogger | None = None,
    ) -> None:
        self.config = config
        self.audit_path = audit_path
        self.events = event_logger or EventLogger()
        self.last_request: dict[str, Any] | None = None
        self.last_response: str | None = None
        self.last_response_metadata: dict[str, Any] | None = None
        self.last_context_usage: ContextUsage | None = None
        self._last_model_activity: float | None = None

    @staticmethod
    def _lms_path() -> str:
        discovered = shutil.which("lms")
        if discovered:
            return discovered
        fallback = Path.home() / ".lmstudio" / "bin" / "lms.exe"
        if fallback.is_file():
            return str(fallback)
        raise ModelServerUnavailable("LM Studio CLI was not found on PATH or in ~/.lmstudio/bin")

    def _ensure_model_loaded(self) -> None:
        """Wake the pinned model after its idle TTL without changing its load parameters."""
        now = time.monotonic()
        # An inventory check does not refresh LM Studio's inference-based TTL.
        # Always inspect the real runtime state: a cached "loaded" result can go
        # stale seconds later when slow browser pacing crosses the TTL boundary.
        lms_path = self._lms_path()
        try:
            inventory = subprocess.run(
                [lms_path, "ps", "--json"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
            loaded = json.loads(inventory.stdout or "[]")
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as exc:
            raise ModelServerUnavailable(f"Could not inspect LM Studio model state: {exc}") from exc

        if any(item.get("identifier") == self.config.identifier for item in loaded):
            self._last_model_activity = now
            return
        if loaded:
            identifiers = [str(item.get("identifier", "unknown")) for item in loaded]
            raise ModelServerUnavailable(
                "The pinned model is unloaded, but other LM Studio models are active: "
                f"{identifiers}. Refusing to unload them from a background Worker."
            )
        if self.config.identifier != "local-agent-vlm":
            raise ModelServerUnavailable(
                "Automatic wake is only configured for local-agent-vlm, not "
                f"{self.config.identifier!r}"
            )

        loader = Path(__file__).resolve().parents[2] / "scripts" / "load-model.ps1"
        if not loader.is_file():
            raise ModelServerUnavailable(f"Pinned model loader is missing: {loader}")
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(loader),
                    "-IdleTtlSeconds",
                    str(self.config.idle_ttl_seconds),
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(180, int(self.config.timeout_seconds)),
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise ModelServerUnavailable(f"Could not wake local-agent-vlm: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).replace("\n", " ").strip()[-1000:]
            raise ModelServerUnavailable(f"Could not wake local-agent-vlm: {detail}")
        self._last_model_activity = time.monotonic()

    @staticmethod
    def _budget_history(messages: list[dict[str, Any]]) -> tuple[list[dict[str, str]], int]:
        history: list[dict[str, str]] = []
        image_count = 0
        for message in messages:
            content = message.get("content", "")
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "text":
                        parts.append(str(item.get("text", "")))
                    elif item.get("type") == "image_url":
                        image_count += 1
                        parts.append("[IMAGE]")
                content = "\n".join(parts)
            history.append({"role": str(message.get("role", "user")), "content": str(content)})
        return history, image_count

    def _ensure_context_budget(self, payload: dict[str, Any]) -> ContextUsage:
        messages, image_count = self._budget_history(payload.get("messages", []))
        try:
            # A process-global SDK websocket can become stale when LM Studio
            # switches/restarts runtimes. Scope this tokenizer connection to
            # one request; do not keep a dead connection across daily runs.
            with lmstudio.Client() as tokenizer:
                model = tokenizer.llm.model(self.config.identifier)
                rendered = model.apply_prompt_template({"messages": messages})
                prompt_tokens = model.count_tokens(rendered)
                runtime_context = model.get_context_length()
        except Exception:
            # UTF-8 bytes are a deliberately conservative fallback for BPE
            # tokenizers. It can reject early, but cannot silently undercount
            # ordinary text when the exact local tokenizer is unavailable.
            serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
            prompt_tokens = len(serialized.encode("utf-8"))
            runtime_context = self.config.context_length
        usage = enforce_context_budget(
            prompt_tokens=prompt_tokens,
            output_tokens=int(payload.get("max_tokens", 0)),
            context_length=min(self.config.context_length, runtime_context),
            image_count=image_count,
            vision_tokens_per_image=self.config.vision_tokens_per_image,
            safety_margin_tokens=self.config.context_safety_margin_tokens,
        )
        self.last_context_usage = usage
        return usage

    def _audit(self, record: dict[str, Any]) -> None:
        if not self.audit_path:
            return
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(redact(record), ensure_ascii=False) + "\n")

    def models(self, timeout: float = 10) -> list[dict[str, Any]]:
        try:
            response = httpx.get(f"{self.config.base_url}/models", timeout=timeout)
            response.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            raise ModelServerUnavailable(
                f"LM Studio unavailable at {self.config.base_url}: {type(exc).__name__}: {exc}"
            ) from exc
        return response.json().get("data", [])

    def native_models(self, timeout: float = 10) -> list[dict[str, Any]]:
        """Return LM Studio's native model inventory, including load configuration."""
        api_root = self.config.base_url.removesuffix("/v1")
        try:
            response = httpx.get(f"{api_root}/api/v1/models", timeout=timeout)
            response.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            raise ModelServerUnavailable(
                f"LM Studio native API unavailable at {api_root}: {type(exc).__name__}: {exc}"
            ) from exc
        return response.json().get("models", [])

    def runtime_models(self, timeout: float = 10) -> list[dict[str, Any]]:
        """Return runtime details such as concrete quant and loaded context length."""
        api_root = self.config.base_url.removesuffix("/v1")
        try:
            response = httpx.get(f"{api_root}/api/v0/models", timeout=timeout)
            response.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            raise ModelServerUnavailable(
                f"LM Studio runtime inventory unavailable at {api_root}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return response.json().get("data", [])

    def loaded_model_details(self) -> dict[str, Any]:
        """Find the concrete loaded runtime behind the stable identifier."""
        matches = [
            item
            for item in self.runtime_models()
            if item.get("id") == self.config.identifier and item.get("state") == "loaded"
        ]
        if len(matches) != 1:
            raise ModelServerUnavailable(
                f"Expected one loaded runtime '{self.config.identifier}', found {len(matches)}"
            )
        return matches[0]

    def actual_model_identifier(self) -> str:
        self._ensure_model_loaded()
        models = self.models()
        identifiers = [str(item.get("id")) for item in models if item.get("id")]
        if self.config.identifier in identifiers:
            return self.config.identifier
        if len(identifiers) == 1:
            return identifiers[0]
        raise ModelServerUnavailable(
            f"Expected loaded model '{self.config.identifier}', "
            f"available identifiers: {identifiers}"
        )

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )
    def _post(self, payload: dict[str, Any], purpose: str) -> str:
        # Request non-reasoning mode when the concrete runtime supports it. Some
        # Qwen3.5 GGUF load paths still emit hidden reasoning; it is never copied
        # into the audit record because only final message content is retained.
        payload = {"reasoning_effort": "none", **payload}
        self._ensure_model_loaded()
        context_usage = self._ensure_context_budget(payload)
        started = time.perf_counter()
        self.last_request = payload
        request_for_audit = {
            **payload,
            "messages": [
                {
                    **message,
                    "content": "[MULTIMODAL CONTENT REDACTED FROM AUDIT]"
                    if isinstance(message.get("content"), list)
                    else message.get("content"),
                }
                for message in payload.get("messages", [])
            ],
        }
        try:
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                response = client.post(f"{self.config.base_url}/chat/completions", json=payload)
                response.raise_for_status()
        except httpx.TimeoutException:
            elapsed = time.perf_counter() - started
            self._audit({"purpose": purpose, "status": "timeout", "elapsed_seconds": elapsed})
            raise
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.replace("\n", " ").strip()[:1000]
            raise ModelServerUnavailable(
                f"LM Studio request failed with HTTP {exc.response.status_code}: {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelServerUnavailable(f"LM Studio request failed: {exc}") from exc
        elapsed = time.perf_counter() - started
        body = response.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            # Never put the whole envelope (which may contain hidden reasoning) in an error.
            raise ModelOutputInvalid("Unexpected LM Studio response envelope") from exc
        content = content if isinstance(content, str) else ""
        self.last_response = content
        self.last_response_metadata = {
            "finish_reason": body["choices"][0].get("finish_reason"),
            "output_characters": len(content),
            "usage": body.get("usage"),
        }
        self._last_model_activity = time.monotonic()
        self._audit(
            {
                "purpose": purpose,
                "status": "success",
                "elapsed_seconds": round(elapsed, 3),
                "model": body.get("model"),
                "usage": body.get("usage"),
                "request": request_for_audit,
                "context_usage": context_usage.model_dump(),
                "response": content,
                "response_metadata": self.last_response_metadata,
            }
        )
        self.events.emit("llm_call", purpose=purpose, elapsed_seconds=round(elapsed, 3))
        return str(content)

    def structured(
        self,
        schema: type[T],
        messages: list[dict[str, Any]],
        purpose: str,
        max_tokens: int = 2000,
        *,
        temperature: float = 0.1,
        reasoning_effort: str = "none",
        validation_retries: int | None = None,
        validate_result: Callable[[T], None] | None = None,
        strict_json: bool = False,
        response_schema: dict[str, Any] | None = None,
    ) -> T:
        if reasoning_effort not in {"none", "low", "medium", "high"}:
            raise ValueError(f"Unsupported reasoning_effort: {reasoning_effort}")
        effective_schema = response_schema or schema.model_json_schema()
        schema_json = json.dumps(effective_schema, ensure_ascii=False)
        json_instruction = {
            "role": "system",
            "content": (
                "Return only one JSON object matching this JSON Schema exactly. "
                f"Do not use Markdown fences. Schema: {schema_json}"
            ),
        }
        base_messages = [json_instruction, *messages]
        payload = {
            "model": self.config.identifier,
            "messages": base_messages,
            "temperature": temperature,
            "reasoning_effort": reasoning_effort,
            # Qwen3.5 Q6_K on Bionic 1.0.6 may spend several hundred hidden
            # tokens before the final JSON even when non-reasoning is requested.
            "max_tokens": max(max_tokens, 1024),
            # Bionic 1.0.6's strict grammar returns empty final content for this
            # concrete Qwen3.5 Q6_K runtime. Text generation plus local Pydantic
            # validation retains strict structure without trusting the envelope.
            "response_format": {"type": "text"},
        }
        if strict_json:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": effective_schema,
                },
            }
        errors: list[str] = []
        current_messages = list(base_messages)
        retry_count = self.config.max_retries if validation_retries is None else validation_retries
        for attempt in range(retry_count + 1):
            text = self._post({**payload, "messages": current_messages}, purpose)
            try:
                result = schema.model_validate(extract_json_object(text))
                if validate_result is not None:
                    validate_result(result)
                return result
            except (ModelOutputInvalid, ValidationError, ValueError, json.JSONDecodeError) as exc:
                detail = str(exc)
                if not text.strip():
                    metadata = self.last_response_metadata or {}
                    detail = (
                        "Model returned empty final content "
                        f"(finish_reason={metadata.get('finish_reason', 'unknown')}); " + detail
                    )
                errors.append(detail)
                if attempt >= retry_count:
                    break
                current_messages = [
                    *base_messages,
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": (
                            "Your JSON failed validation. Return only one corrected JSON object. "
                            f"Validation error: {exc}"
                        ),
                    },
                ]
        raise ModelOutputInvalid("; ".join(errors))

    def select_articles(
        self, homepage_url: str, dom_summary: str, count: int = 3
    ) -> ArticleSelection:
        system = (
            f"You are a local web navigation model. Select exactly {count} normal editorial news "
            "articles from the supplied homepage candidates. Exclude ads, sponsored content, "
            "newsletters, navigation/category/author pages, video-only pages, live tickers, and "
            "duplicates. URLs must be copied exactly from the candidate list. "
            f"Ranks are 1..{count}."
        )
        result = self.structured(
            ArticleSelection,
            [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": f"Homepage: {homepage_url}\n\nCandidate DOM summary:\n{dom_summary}",
                },
            ],
            purpose="homepage_article_selection",
            max_tokens=4096,
        )
        if len(result.articles) != count:
            raise ModelOutputInvalid(f"Expected {count} selected items, got {len(result.articles)}")
        return result

    def classify_image(
        self,
        image_path: Path,
        *,
        article_title: str,
        alt: str,
        caption: str,
        nearby_text: str,
        dom_region: str,
    ) -> ImageDecision:
        mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        prompt = (
            "Classify whether this image belongs in a cleaned editorial article. Allowed category: "
            "lead_photo, article_figure, chart, diagram, screenshot, decorative, logo, icon, "
            "avatar, advertisement, tracking, unknown. Allowed decision: keep, drop, review. "
            "Low confidence "
            "or uncertainty must use review. Return JSON only.\n"
            f"Article title: {article_title}\nAlt: {alt}\nCaption: {caption}\n"
            f"Nearby text: {nearby_text[:1000]}\nDOM region: {dom_region[:300]}"
        )
        try:
            decision = self.structured(
                ImageDecision,
                [
                    {
                        "role": "system",
                        "content": "You are a conservative editorial image reviewer.",
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{encoded}"},
                            },
                        ],
                    },
                ],
                purpose="image_cleanup_classification",
                max_tokens=600,
            )
            return safe_image_decision(decision.model_dump())
        except Exception as exc:
            return safe_image_decision({}, error=f"{type(exc).__name__}: {exc}")

    def text_smoke(self) -> str:
        return self._post(
            {
                "model": self.config.identifier,
                "messages": [{"role": "user", "content": "Reply with exactly: LOCAL_OK"}],
                "temperature": 0,
                "max_tokens": 1024,
            },
            purpose="smoke_text",
        )
