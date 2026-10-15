from __future__ import annotations

from dataclasses import asdict, dataclass


class ContextBudgetExceeded(RuntimeError):
    """Raised before inference when a request cannot fit the configured window."""


@dataclass(frozen=True)
class ContextUsage:
    prompt_tokens: int
    output_tokens: int
    vision_reserve_tokens: int
    safety_margin_tokens: int
    context_length: int

    @property
    def total_reserved_tokens(self) -> int:
        return (
            self.prompt_tokens
            + self.output_tokens
            + self.vision_reserve_tokens
            + self.safety_margin_tokens
        )

    @property
    def remaining_tokens(self) -> int:
        return self.context_length - self.total_reserved_tokens

    def model_dump(self) -> dict[str, int]:
        return {**asdict(self), "total_reserved_tokens": self.total_reserved_tokens}


def enforce_context_budget(
    *,
    prompt_tokens: int,
    output_tokens: int,
    context_length: int,
    image_count: int = 0,
    vision_tokens_per_image: int = 4096,
    safety_margin_tokens: int = 2048,
) -> ContextUsage:
    """Validate a conservative full-request budget before calling the model."""
    values = {
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "context_length": context_length,
        "image_count": image_count,
        "vision_tokens_per_image": vision_tokens_per_image,
        "safety_margin_tokens": safety_margin_tokens,
    }
    if any(value < 0 for value in values.values()) or context_length == 0:
        raise ValueError(f"Context budget values must be non-negative: {values}")
    usage = ContextUsage(
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        vision_reserve_tokens=image_count * vision_tokens_per_image,
        safety_margin_tokens=safety_margin_tokens,
        context_length=context_length,
    )
    if usage.remaining_tokens < 0:
        raise ContextBudgetExceeded(
            "Request exceeds context budget before inference: "
            f"reserved={usage.total_reserved_tokens}, context={context_length}, "
            f"over_by={-usage.remaining_tokens}"
        )
    return usage
