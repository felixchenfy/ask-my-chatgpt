from __future__ import annotations

import time
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from .access_pacing import AccessPacer
from .config import BrowserConfig
from .logging_setup import EventLogger


class BrowserController:
    def __init__(
        self,
        config: BrowserConfig,
        profile_dir: Path,
        trace_path: Path,
        *,
        access_state_path: Path | None = None,
        event_logger: EventLogger | None = None,
    ) -> None:
        self.config = config
        self.profile_dir = profile_dir
        self.trace_path = trace_path
        self.playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.trace_started = False
        self.last_status: int | None = None
        self.last_navigation_at: float | None = None
        self.previous_page_characters = 0
        self.events = event_logger
        self.pacer = AccessPacer(
            config,
            access_state_path or trace_path.with_name("access-policy.json"),
        )

    async def __aenter__(self) -> BrowserController:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        self.playwright = await async_playwright().start()
        launch_options: dict[str, object] = {
            "user_data_dir": self.profile_dir,
            "headless": self.config.headless,
            "viewport": {"width": 1440, "height": 1200},
        }
        if self.config.user_agent is not None:
            launch_options["user_agent"] = self.config.user_agent
        if self.config.locale is not None:
            launch_options["locale"] = self.config.locale
        self.context = await self.playwright.chromium.launch_persistent_context(**launch_options)
        self.context.set_default_navigation_timeout(self.config.navigation_timeout_ms)
        if self.config.trace_enabled:
            await self.context.tracing.start(screenshots=True, snapshots=True, sources=False)
            self.trace_started = True
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.context:
            if self.trace_started:
                try:
                    await self.context.tracing.stop(path=self.trace_path)
                except Exception:
                    pass
                self.trace_started = False
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()

    def require_page(self) -> Page:
        if not self.page:
            raise RuntimeError("Browser is not started")
        return self.page

    async def goto(self, url: str) -> Page:
        page = self.require_page()
        pacing = await self.pacer.wait_before_navigation(
            url, previous_page_characters=self.previous_page_characters
        )
        if self.events:
            self.events.emit("navigation_pacing", url=url, **self.pacer.as_event(pacing))
        response = await page.goto(url, wait_until="domcontentloaded")
        self.last_status = response.status if response else None
        await page.wait_for_timeout(self.config.settle_ms)
        self.last_navigation_at = time.monotonic()
        try:
            self.previous_page_characters = len(await page.locator("body").inner_text(timeout=3000))
        except Exception:
            self.previous_page_characters = 0
        if response and response.status >= 400:
            raise RuntimeError(f"Page returned HTTP {response.status}: {url}")
        return page

    async def save_current(
        self, html_path: Path, screenshot_path: Path, full_page: bool = True
    ) -> None:
        page = self.require_page()
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(await page.content(), encoding="utf-8")
        await page.screenshot(path=screenshot_path, full_page=full_page)

    async def save_trace(self) -> None:
        if self.context and self.trace_started:
            await self.context.tracing.stop(path=self.trace_path)
            self.trace_started = False
