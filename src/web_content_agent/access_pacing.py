from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .checkpoint import atomic_write_json
from .config import BrowserConfig


class DailyPageLimitReached(RuntimeError):
    """Stop a resumable run before it exceeds the configured daily request cap."""


@dataclass(frozen=True)
class PacingResult:
    target_interval_seconds: float
    slept_seconds: float
    pages_today: int
    daily_page_limit: int
    mode: str
    long_pause: bool
    previous_page_characters: int


class AccessPacer:
    """Persistent, bounded request pacing for polite serial browsing.

    This deliberately controls request frequency; it does not spoof fingerprints or
    attempt to evade access controls. The ledger survives worker restarts so resume
    cannot silently reset the daily cap or the last-request interval.
    """

    def __init__(
        self,
        config: BrowserConfig,
        state_path: Path,
        *,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.config = config
        self.state_path = state_path
        self._now = now or (lambda: datetime.now().astimezone())
        self._sleep = sleep
        self._rng = rng or random.Random(config.access_pattern_seed)

    def _load(self) -> dict:
        if not self.state_path.exists():
            return {"schema_version": 1, "days": {}, "last_attempt_at": None}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": 1, "days": {}, "last_attempt_at": None}
        return value if isinstance(value, dict) else {"schema_version": 1, "days": {}}

    def _adaptive_interval(
        self, previous_page_characters: int, pages_today: int
    ) -> tuple[float, bool]:
        low = self.config.min_navigation_delay_seconds
        high = self.config.max_navigation_delay_seconds
        if high <= low:
            return low, False
        if self.config.pacing_mode == "uniform":
            return self._rng.uniform(low, high), False

        # Most visits are near the lower half. Longer previous content, a growing
        # streak, and occasional breaks shift the interval upward without ever
        # leaving the configured bounds.
        ratio = self._rng.betavariate(1.6, 4.2)
        reading_factor = min(max(previous_page_characters, 0) / 18000, 1.0) * 0.28
        streak_factor = min((pages_today % 12) / 12, 1.0) * 0.12
        long_pause_chance = min(
            0.5,
            self.config.long_pause_probability + min(previous_page_characters / 80000, 0.08),
        )
        long_pause = self._rng.random() < long_pause_chance
        if long_pause:
            ratio = max(ratio, self._rng.uniform(0.68, 1.0))
        else:
            ratio = min(1.0, ratio + reading_factor + streak_factor)
        return low + ((high - low) * ratio), long_pause

    async def wait_before_navigation(
        self, url: str, *, previous_page_characters: int = 0
    ) -> PacingResult:
        state = self._load()
        now = self._now()
        day_key = now.date().isoformat()
        days = state.setdefault("days", {})
        day = days.setdefault(day_key, {"attempts": 0})
        pages_today = int(day.get("attempts", 0))
        if pages_today >= self.config.daily_page_limit:
            raise DailyPageLimitReached(
                f"Daily page limit {self.config.daily_page_limit} reached for {day_key}; "
                "resume after the local calendar date changes"
            )

        interval, long_pause = self._adaptive_interval(previous_page_characters, pages_today)
        slept = 0.0
        last_value = state.get("last_attempt_at")
        if last_value:
            try:
                elapsed = max(0.0, (now - datetime.fromisoformat(str(last_value))).total_seconds())
            except ValueError:
                elapsed = interval
            slept = max(0.0, interval - elapsed)
            if slept:
                await self._sleep(slept)

        attempted_at = self._now()
        day["attempts"] = pages_today + 1
        day["last_url"] = url
        day["last_attempt_at"] = attempted_at.isoformat()
        state["last_attempt_at"] = attempted_at.isoformat()
        # Retain a short audit window rather than growing this tiny ledger forever.
        for old_day in sorted(days)[:-14]:
            days.pop(old_day, None)
        atomic_write_json(self.state_path, state)
        return PacingResult(
            target_interval_seconds=round(interval, 3),
            slept_seconds=round(slept, 3),
            pages_today=pages_today + 1,
            daily_page_limit=self.config.daily_page_limit,
            mode=self.config.pacing_mode,
            long_pause=long_pause,
            previous_page_characters=max(previous_page_characters, 0),
        )

    @staticmethod
    def as_event(result: PacingResult) -> dict:
        return asdict(result)
