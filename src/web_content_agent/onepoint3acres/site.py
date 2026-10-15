from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag
from playwright.async_api import Locator, Page

from .models import (
    ChoiceTarget,
    DailyPageState,
    DailyQuiz,
    LoginState,
    QuizChoice,
    QuizExtraction,
    SemanticControl,
    normalize_text,
)

HOME_URL = "https://www.1point3acres.com/home"
QUIZ_FALLBACK_URL = "https://www.1point3acres.com/next/daily-question"
ALLOWED_HOSTS = {"1point3acres.com", "www.1point3acres.com"}
VERIFICATION_MARKERS = ("验证码", "安全验证", "人机验证", "captcha", "security check", "challenge")
LOGGED_OUT_MARKERS = ("请先登录", "尚未登录", "登录后")
ACCOUNT_MARKERS = ("个人中心", "用户中心", "我的账户", "退出登录", "登出")

logger = logging.getLogger(__name__)


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = normalize_text(text).lower()
    return any(marker.lower() in lowered for marker in markers)


def analyze_home_controls(
    *,
    url: str,
    body_text: str,
    controls: list[SemanticControl],
    has_account_control: bool = False,
) -> DailyPageState:
    normalized = [
        SemanticControl(item.role, normalize_text(item.text), item.href) for item in controls
    ]
    texts = [item.text for item in normalized]
    lower_url = url.lower()

    if _has_any(body_text, VERIFICATION_MARKERS) or any(
        marker in lower_url for marker in ("captcha", "verify", "challenge")
    ):
        login_state = LoginState.VERIFICATION_REQUIRED
    else:
        login_control = any(text in {"登录", "立即登录", "登入"} for text in texts)
        daily_signal = any("签到" in text or "答题" in text for text in texts)
        account_signal = has_account_control or _has_any(body_text, ACCOUNT_MARKERS)
        explicit_logged_out = _has_any(body_text, LOGGED_OUT_MARKERS) or any(
            part in lower_url for part in ("/login", "/signin")
        )
        if explicit_logged_out or (login_control and not (daily_signal or account_signal)):
            login_state = LoginState.LOGGED_OUT
        elif daily_signal or account_signal:
            login_state = LoginState.LOGGED_IN
        else:
            login_state = LoginState.UNKNOWN

    already_signed_in = any("已签到" in text or "签到成功" in text for text in texts) or _has_any(
        body_text, ("今日已签到", "签到成功")
    )
    signin = next((item for item in normalized if item.text == "签到"), None)
    already_answered = any("已答题" in text or "今日已答" in text for text in texts) or _has_any(
        body_text, ("今日已答题", "已经完成今日答题", "明日再来")
    )
    quiz_controls = [
        item
        for item in normalized
        if "答题" in item.text and "说明" not in item.text and "规则" not in item.text
    ]
    quiz_url = next((urljoin(url, item.href) for item in quiz_controls if item.href), None)
    if quiz_url and (urlparse(quiz_url).hostname or "").lower() not in ALLOWED_HOSTS:
        quiz_url = None

    return DailyPageState(
        login_state=login_state,
        signin_available=signin is not None and not already_signed_in,
        already_signed_in=already_signed_in,
        quiz_available=bool(quiz_controls) and not already_answered,
        already_answered=already_answered,
        signin_locator_description=(f"{signin.role}: {signin.text}" if signin else None),
        quiz_url=quiz_url,
    )


def _is_hidden(tag: Tag) -> bool:
    style = str(tag.get("style", "")).replace(" ", "").lower()
    return tag.has_attr("hidden") or tag.get("aria-hidden") == "true" or "display:none" in style


def _choice_text(control: Tag, container: Tag) -> tuple[str | None, str]:
    label_text = normalize_text(str(control.get("aria-label", "")))
    control_id = str(control.get("id", ""))
    label: Tag | None = None
    if control_id:
        candidate = container.find("label", attrs={"for": control_id})
        label = candidate if isinstance(candidate, Tag) else None
    if label is None:
        candidate = control.find_parent("label")
        label = candidate if isinstance(candidate, Tag) else None
    if label is not None:
        label_text = normalize_text(label.get_text(" ", strip=True))
    if not label_text and control.get("role") == "radio":
        label_text = normalize_text(control.get_text(" ", strip=True))
    if not label_text:
        label_text = normalize_text(str(control.get("value", "")))
    prefix = None
    if (
        len(label_text) >= 2
        and label_text[0].upper() in "ABCDEFGH"
        and label_text[1] in ".、:：)） "
    ):
        prefix = label_text[0].upper()
        label_text = normalize_text(label_text[2:])
    return prefix, label_text


def parse_quiz_html(html: str, source_url: str) -> QuizExtraction:
    soup = BeautifulSoup(html, "lxml")
    all_controls = [
        item
        for item in soup.select('input[type="radio"], [role="radio"]')
        if isinstance(item, Tag) and not _is_hidden(item)
    ]
    selected: tuple[Tag, list[Tag]] | None = None
    for selector in ("fieldset", '[role="radiogroup"]', "form", "main", "article"):
        candidates: list[tuple[Tag, list[Tag]]] = []
        for container in soup.select(selector):
            if not isinstance(container, Tag):
                continue
            choices = [
                item for item in all_controls if item is container or container in item.parents
            ]
            if 2 <= len(choices) <= 8:
                candidates.append((container, choices))
        if len(candidates) > 1:
            raise ValueError("页面存在多个可能的单选题区域，无法安全确定每日题目")
        if candidates:
            selected = candidates[0]
            break
    if selected is None:
        raise ValueError("未找到包含 2 到 8 个可见单选项的明确题目区域")

    container, controls = selected
    choices: list[QuizChoice] = []
    targets: list[ChoiceTarget] = []
    for index, control in enumerate(controls, start=1):
        label, text = _choice_text(control, container)
        if not text:
            raise ValueError(f"第 {index} 个选项没有可验证文本")
        choices.append(QuizChoice(index=index, label=label, text=text))
        targets.append(
            ChoiceTarget(
                kind="input" if control.name == "input" else "role",
                dom_index=all_controls.index(control),
            )
        )

    question = ""
    for selector in ("legend", "[data-question]", "h1", "h2", "h3", ".question"):
        candidate = container.select_one(selector)
        if isinstance(candidate, Tag):
            question = normalize_text(candidate.get_text(" ", strip=True))
            if question:
                break
    if not question:
        previous = container.find_previous(["h1", "h2", "h3"])
        if isinstance(previous, Tag):
            question = normalize_text(previous.get_text(" ", strip=True))
    if not question:
        raise ValueError("题目区域存在选项，但无法可靠确定题干")

    supplementary_parts: list[str] = []
    for candidate in container.select(".description, .hint, [data-description]"):
        text = normalize_text(candidate.get_text(" ", strip=True))
        if text and text != question and all(text != choice.text for choice in choices):
            supplementary_parts.append(text)
    supplementary = normalize_text(" ".join(dict.fromkeys(supplementary_parts))) or None
    quiz = DailyQuiz(
        question=question,
        choices=choices,
        source_url=source_url,
        supplementary=supplementary,
    )
    return QuizExtraction(quiz=quiz, targets=tuple(targets))


@dataclass
class OnePointThreeAcresAdapter:
    page: Page
    debug: bool = False
    _signin_locator: Locator | None = None

    async def _semantic_controls(self) -> list[SemanticControl]:
        result: list[SemanticControl] = []
        for role in ("button", "link"):
            locator = self.page.get_by_role(role)  # type: ignore[arg-type]
            for index in range(await locator.count()):
                item = locator.nth(index)
                try:
                    if not await item.is_visible():
                        continue
                    text = normalize_text(await item.inner_text(timeout=1000))
                    if not text:
                        text = normalize_text(await item.get_attribute("aria-label"))
                    href = await item.get_attribute("href") if role == "link" else None
                    result.append(SemanticControl(role=role, text=text, href=href))
                except Exception:
                    continue
        return result

    async def home_state(self) -> DailyPageState:
        controls = await self._semantic_controls()
        body_text = await self.page.locator("body").inner_text(timeout=5000)
        account = self.page.locator(
            '[aria-haspopup="menu"], [aria-label*="用户"], [aria-label*="头像"], '
            '[aria-label*="account" i], [aria-label*="profile" i]'
        )
        has_account_control = await account.count() > 0
        state = analyze_home_controls(
            url=self.page.url,
            body_text=body_text[:50000],
            controls=controls,
            has_account_control=has_account_control,
        )
        self._signin_locator = None
        if state.signin_available:
            for role in ("button", "link"):
                candidate = self.page.get_by_role(role, name="签到", exact=True)  # type: ignore[arg-type]
                if await candidate.count() and await candidate.first.is_visible():
                    self._signin_locator = candidate.first
                    break
        if self.debug:
            logger.debug("home controls=%s state=%s", controls, state)
        return state

    async def click_signin_once(self) -> None:
        if self._signin_locator is None:
            raise RuntimeError("没有经过状态检测的明确签到控件")
        await self._signin_locator.click()
        await self.page.wait_for_timeout(1200)

    async def signin_succeeded(self) -> bool:
        state = await self.home_state()
        if state.already_signed_in:
            return True
        body_text = await self.page.locator("body").inner_text(timeout=3000)
        return _has_any(body_text, ("签到成功", "成功签到"))

    async def navigate_to_quiz(self, state: DailyPageState) -> None:
        target = state.quiz_url
        if target is None and state.quiz_available and not state.already_answered:
            target = QUIZ_FALLBACK_URL
        if target is None:
            raise RuntimeError("首页没有可验证的答题入口或未完成状态")
        host = (urlparse(target).hostname or "").lower()
        if host not in ALLOWED_HOSTS:
            raise RuntimeError("答题入口离开一亩三分地域名，已停止导航")
        await self.page.goto(target, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(1200)

    async def extract_quiz(self) -> QuizExtraction:
        body_text = await self.page.locator("body").inner_text(timeout=5000)
        if _has_any(body_text, VERIFICATION_MARKERS):
            raise RuntimeError("答题页要求人工完成安全验证")
        if _has_any(body_text, ("今日已答题", "已经完成今日答题", "明日再来")):
            raise RuntimeError("今日已经答题")
        return parse_quiz_html(await self.page.content(), self.page.url)

    def _target_locator(self, target: ChoiceTarget) -> Locator:
        return self.page.locator('input[type="radio"], [role="radio"]').nth(target.dom_index)

    async def _revalidate(self, extraction: QuizExtraction, choice_index: int) -> Locator:
        current = parse_quiz_html(await self.page.content(), self.page.url)
        if (
            current.quiz.model_dump() != extraction.quiz.model_dump()
            or current.targets != extraction.targets
        ):
            raise RuntimeError("页面题目结构已经变化，拒绝继续操作")
        if not 1 <= choice_index <= len(extraction.targets):
            raise RuntimeError("推荐选项越界")
        locator = self._target_locator(extraction.targets[choice_index - 1])
        if not await locator.is_visible():
            raise RuntimeError("推荐选项当前不可见")
        return locator

    async def highlight_choice(
        self, extraction: QuizExtraction, choice_index: int, confidence: float, reason: str
    ) -> None:
        locator = await self._revalidate(extraction, choice_index)
        await locator.evaluate(
            """element => {
                const target = element.closest('label') || element;
                target.dataset.wcaDailyHighlight = 'true';
                target.style.outline = '3px solid #f59e0b';
                target.style.outlineOffset = '4px';
                target.style.backgroundColor = 'rgba(245, 158, 11, 0.12)';
            }"""
        )
        answer = extraction.quiz.choices[choice_index - 1]
        await self.page.evaluate(
            """({answer, confidence, reason}) => {
                document.getElementById('wca-daily-overlay')?.remove();
                const box = document.createElement('aside');
                box.id = 'wca-daily-overlay';
                Object.assign(box.style, {
                    position: 'fixed', left: '16px', bottom: '16px', zIndex: '2147483647',
                    maxWidth: '360px', padding: '12px 14px', borderRadius: '8px',
                    background: '#111827', color: '#fff', boxShadow: '0 4px 18px #0006',
                    fontSize: '14px', lineHeight: '1.45', pointerEvents: 'none'
                });
                box.textContent =
                    `本地模型推荐：${answer}（${confidence}%）\n${reason}\n此工具不会自动提交`;
                document.body.appendChild(box);
            }""",
            {
                "answer": f"[{answer.index}] {answer.text}",
                "confidence": round(confidence * 100),
                "reason": reason,
            },
        )

    async def select_choice(self, extraction: QuizExtraction, choice_index: int) -> bool:
        locator = await self._revalidate(extraction, choice_index)
        if extraction.targets[choice_index - 1].kind == "input":
            await locator.check()
            return await locator.is_checked()
        await locator.click()
        return (await locator.get_attribute("aria-checked")) == "true"

    async def save_debug_bundle(self, directory: Path) -> tuple[Path, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        from datetime import datetime

        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        html_path = directory / f"quiz-{stamp}.html"
        screenshot_path = directory / f"quiz-{stamp}.png"
        html_path.write_text(await self.page.content(), encoding="utf-8")
        await self.page.screenshot(path=screenshot_path, full_page=True)
        return html_path, screenshot_path
