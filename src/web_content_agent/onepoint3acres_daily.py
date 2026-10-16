"""Extra feature: daily runner for the 1Point3Acres assistant.

Not part of the ChatGPT bridge; see onepoint3acres/README.md.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from web_content_agent.logging_setup import configure_logging
from web_content_agent.onepoint3acres.runtime import create_browser, create_solver
from web_content_agent.onepoint3acres.site import OnePointThreeAcresAdapter
from web_content_agent.onepoint3acres.workflow import (
    DailyAssistantWorkflow,
    WorkflowOptions,
    run_with_browser,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="一亩三分地每日签到与答题助手")
    parser.add_argument("--dry-run", action="store_true", help="不签到、不选择答案")
    parser.add_argument("--profile-dir", type=Path, help="Playwright persistent profile 路径")
    parser.add_argument("--confidence-threshold", type=float, default=None)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-llm", action="store_true", help="只提取题目和选项")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    configure_logging(args.debug)
    threshold = args.confidence_threshold
    if threshold is None:
        threshold = float(os.getenv("ONEPOINT3ACRES_CONFIDENCE_THRESHOLD", "0.85"))
    if not 0 <= threshold <= 1:
        raise SystemExit("--confidence-threshold 必须在 0 到 1 之间")

    browser, debug_dir = create_browser(profile_dir=args.profile_dir, debug=args.debug)
    options = WorkflowOptions(
        dry_run=args.dry_run,
        confidence_threshold=threshold,
        debug_dir=debug_dir,
        use_llm=not args.no_llm,
    )
    solver = None if args.no_llm else create_solver()

    def make_workflow(adapter: OnePointThreeAcresAdapter) -> DailyAssistantWorkflow:
        return DailyAssistantWorkflow(adapter, options, solver)

    try:
        asyncio.run(run_with_browser(browser, make_workflow, debug=args.debug))
    except KeyboardInterrupt:
        print("\n已由用户取消。")
        return 130
    except Exception as exc:
        print(f"助手已安全停止：{type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
