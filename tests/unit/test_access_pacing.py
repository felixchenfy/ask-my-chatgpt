from datetime import UTC, datetime
from pathlib import Path

import pytest

from web_content_agent.access_pacing import AccessPacer, DailyPageLimitReached
from web_content_agent.config import BrowserConfig


@pytest.mark.asyncio
async def test_adaptive_pacing_is_bounded_persistent_and_daily_limited(tmp_path: Path) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    now = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    config = BrowserConfig(
        min_navigation_delay_seconds=38,
        max_navigation_delay_seconds=318,
        daily_page_limit=2,
        pacing_mode="adaptive_polite",
        access_pattern_seed=7,
    )
    ledger = tmp_path / "access-policy.json"
    pacer = AccessPacer(config, ledger, now=lambda: now, sleep=fake_sleep)

    first = await pacer.wait_before_navigation("https://fixture.test/one")
    second = await pacer.wait_before_navigation(
        "https://fixture.test/two", previous_page_characters=12000
    )

    assert first.slept_seconds == 0
    assert 38 <= first.target_interval_seconds <= 318
    assert 38 <= second.target_interval_seconds <= 318
    assert sleeps == [pytest.approx(second.target_interval_seconds, abs=0.001)]
    assert second.pages_today == 2

    resumed = AccessPacer(config, ledger, now=lambda: now, sleep=fake_sleep)
    with pytest.raises(DailyPageLimitReached, match="Daily page limit 2 reached"):
        await resumed.wait_before_navigation("https://fixture.test/three")


@pytest.mark.asyncio
async def test_uniform_pacing_also_respects_configured_bounds(tmp_path: Path) -> None:
    config = BrowserConfig(
        min_navigation_delay_seconds=38,
        max_navigation_delay_seconds=318,
        pacing_mode="uniform",
        access_pattern_seed=11,
    )
    pacer = AccessPacer(config, tmp_path / "access-policy.json")
    interval, long_pause = pacer._adaptive_interval(0, 0)
    assert 38 <= interval <= 318
    assert long_pause is False
