"""Replay an own real quiz through the selected model, with zero website actions."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime

from web_content_agent.onepoint3acres.models import DailyQuiz
from web_content_agent.onepoint3acres.runtime import create_solver
from web_content_agent.paths import default_workspace_root, project_root


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    date = datetime.strptime(args.date, "%Y-%m-%d").strftime("%Y-%m-%d")
    journal = default_workspace_root() / "runs/onepoint3acres" / f"{date}.jsonl"
    records = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    records = [r for r in records if r["run_id"] == args.run_id]
    if not any(
        r["event"] == "run_started" and r["data"].get("simulation") is False for r in records
    ):
        raise ValueError(
            "Replay requires this app's own real run, not an imported bank or simulation"
        )
    context = next(r["data"] for r in records if r["event"] == "decision_context")
    quiz = DailyQuiz.model_validate(context["quiz"])
    started = time.monotonic()
    report = {
        "source_date": date,
        "source_run": args.run_id,
        "simulation": True,
        "website_actions": 0,
        "started_at": datetime.now().astimezone().isoformat(),
    }
    try:
        answer = create_solver(output=lambda msg: print(msg, flush=True), advisory=True).solve(quiz)
        report["answer"] = answer.model_dump()
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    target = (
        project_root() / ".local/experiments" / f"quiz-replay-{datetime.now():%Y-%m-%d-%H%M%S}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    print(f"Report: {target}", flush=True)


if __name__ == "__main__":
    main()
