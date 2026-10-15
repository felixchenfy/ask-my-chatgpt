from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from web_content_agent.browser import BrowserController
from web_content_agent.lmstudio import LMStudioError

from .models import DailyPageState, InteractionGuard, LoginState, QuizAnswer, QuizExtraction
from .site import HOME_URL, OnePointThreeAcresAdapter
from .solver import QuizSolver

logger = logging.getLogger(__name__)
InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]


@dataclass(frozen=True)
class WorkflowOptions:
    dry_run: bool
    confidence_threshold: float
    debug_dir: Path
    use_llm: bool = True
    interactive: bool = True


class DailyAssistantWorkflow:
    def __init__(
        self,
        adapter: OnePointThreeAcresAdapter,
        options: WorkflowOptions,
        solver: QuizSolver | None,
        *,
        input_fn: InputFunction = input,
        output: OutputFunction = print,
    ) -> None:
        self.adapter = adapter
        self.options = options
        self.solver = solver
        self.input = input_fn
        self.output = output
        self.guard = InteractionGuard(dry_run=options.dry_run)

    async def _require_login(self, state: DailyPageState) -> DailyPageState:
        if not self.options.interactive and state.login_state is not LoginState.LOGGED_IN:
            self.output(
                f"自动检查无法确认登录状态：{state.login_state.value}。"
                "请点击“立即运行一次”并在浏览器中手动处理。"
            )
            return state
        if state.login_state is LoginState.VERIFICATION_REQUIRED:
            self.output("页面要求验证码或安全验证。请在浏览器中手动完成，脚本不会尝试绕过。")
            await asyncio.to_thread(self.input, "完成后回到终端按 Enter 重新检查：")
            return await self.adapter.home_state()
        if state.login_state is LoginState.LOGGED_OUT:
            self.output("当前未登录。请在浏览器窗口中手动完成登录；不要在终端输入密码。")
            await asyncio.to_thread(self.input, "登录完成后回到终端按 Enter 重新检查：")
            return await self.adapter.home_state()
        return state

    async def _handle_signin(self, state: DailyPageState) -> bool:
        if state.already_signed_in:
            self.output("今日已经签到，无需操作。")
            return True
        if not state.signin_available:
            self.output("未检测到明确的可签到入口；不会猜测点击。")
            return True
        self.output("检测到“签到”入口。")
        if self.options.dry_run:
            self.output("dry-run：不会点击签到。")
            return True
        answer = await asyncio.to_thread(self.input, "按 Enter 执行一次签到；输入 q 取消：")
        if answer != "":
            self.output("已取消签到。")
            return True
        self.guard.authorize_signin()
        await self.adapter.click_signin_once()
        if await self.adapter.signin_succeeded():
            self.output("签到成功。")
            return True
        else:
            self.output("点击已执行一次，但无法验证签到成功；不会再次点击，请查看浏览器。")
            return False

    def _print_quiz(self, extraction: QuizExtraction) -> None:
        self.output(f"\n题目：\n{extraction.quiz.question}\n\n选项：")
        for choice in extraction.quiz.choices:
            label = f"{choice.label}. " if choice.label else ""
            self.output(f"[{choice.index}] {label}{choice.text}")
        if extraction.quiz.supplementary:
            self.output(f"\n补充说明：{extraction.quiz.supplementary}")

    def _print_answer(self, answer: QuizAnswer) -> None:
        self.output(
            "\n本地模型推荐：\n"
            f"答案：[{answer.choice_index}] {answer.choice_text}\n"
            f"置信度：{answer.confidence:.0%}\n"
            f"理由：{answer.reason}"
        )

    async def _handle_answer(self, extraction: QuizExtraction, answer: QuizAnswer) -> None:
        self._print_answer(answer)
        threshold = self.options.confidence_threshold
        if answer.confidence < threshold:
            self.output(
                f"模型置信度低于 {threshold:.0%}，不会帮你选择选项。\n请在浏览器中手动判断和作答。"
            )
            return
        if self.options.dry_run:
            self.output("dry-run：不会高亮或改变页面表单状态。")
            return
        await self.adapter.highlight_choice(
            extraction, answer.choice_index, answer.confidence, answer.reason
        )
        confirmed = await asyncio.to_thread(
            self.input, "是否帮你选中该选项，但不提交？输入 y 确认，其他输入取消："
        )
        if confirmed.strip().lower() != "y":
            self.output("已取消选择；页面上仅保留非破坏性高亮。")
            return
        self.guard.authorize_answer_selection()
        if not await self.adapter.select_choice(extraction, answer.choice_index):
            raise RuntimeError("页面未确认推荐项处于选中状态")
        self.output("已选中推荐答案，但尚未提交。\n请在浏览器中亲自检查并点击提交。")

    async def run(self) -> None:
        await self.adapter.page.goto(HOME_URL, wait_until="domcontentloaded")
        await self.adapter.page.wait_for_timeout(1200)
        state = await self._require_login(await self.adapter.home_state())
        if not state.logged_in:
            raise RuntimeError(f"无法确认登录状态：{state.login_state.value}")
        if not await self._handle_signin(state):
            return
        state = await self.adapter.home_state()
        if state.already_answered:
            self.output("今日已经答题，无需操作。")
            return
        if not state.quiz_available:
            self.output("未检测到明确的今日答题入口；不会猜测导航或点击。")
            return
        await self.adapter.navigate_to_quiz(state)
        try:
            extraction = await self.adapter.extract_quiz()
        except Exception:
            html_path, screenshot_path = await self.adapter.save_debug_bundle(
                self.options.debug_dir
            )
            self.output(f"题目提取失败，诊断文件：\n{html_path}\n{screenshot_path}")
            raise
        self._print_quiz(extraction)
        if not self.options.use_llm:
            self.output("--no-llm：未调用本地模型。")
            return
        if self.solver is None:
            raise RuntimeError("已启用 LLM，但没有配置本地求解器")
        try:
            answer = await asyncio.to_thread(self.solver.solve, extraction.quiz)
        except LMStudioError:
            logger.exception("Local model failed")
            raise
        await self._handle_answer(extraction, answer)


async def run_with_browser(
    browser: BrowserController,
    workflow_factory: Callable[[OnePointThreeAcresAdapter], DailyAssistantWorkflow],
    *,
    debug: bool,
    wait_for_user: bool = True,
) -> None:
    async with browser:
        adapter = OnePointThreeAcresAdapter(browser.require_page(), debug=debug)
        workflow = workflow_factory(adapter)
        try:
            await workflow.run()
        finally:
            if wait_for_user:
                await asyncio.to_thread(
                    workflow.input,
                    "请在浏览器中检查页面；完成后回到终端按 Enter 关闭助手窗口：",
                )
