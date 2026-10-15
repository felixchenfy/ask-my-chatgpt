"""Offline-inference experiment: no browser, bridge run, or website submission."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

from web_content_agent.lmstudio import LMStudioClient
from web_content_agent.onepoint3acres.model_selection import (
    installed_models,
    model_lease,
    quiz_model,
)
from web_content_agent.onepoint3acres.models import DailyQuiz
from web_content_agent.onepoint3acres.solver import PROMPT_VERSION, QuizSolver
from web_content_agent.paths import project_root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", help="Exact installed key; default: all models")
    parser.add_argument("--repeats", type=int, default=1, choices=range(1, 6))
    parser.add_argument(
        "--condition", choices=["both", "question_only", "official_evidence"], default="both"
    )
    args = parser.parse_args()
    root = project_root()
    fixture = json.loads(
        (root / "tests/fixtures/onepoint3acres/points-quiz-model-comparison.json").read_text(
            encoding="utf-8"
        )
    )
    inventory = installed_models()
    keys = args.model or [item["key"] for item in inventory]
    if not set(keys) <= {item["key"] for item in inventory}:
        parser.error("Every model must be an exact installed model key")
    report = {
        "inventory": inventory,
        "started_at": datetime.now().astimezone().isoformat(),
        "fixture": fixture,
        "settings": {
            "prompt_version": PROMPT_VERSION,
            "automatic_local_knowledge": False,
            "temperature": 0.15,
            "reasoning_effort_requested": "high",
            "max_tokens_per_call": 8192,
            "passes": 2,
            "threshold": 0.90,
            "repeats": args.repeats,
        },
        "results": [],
    }
    folder = root / ".local/experiments" / datetime.now().strftime("quiz-models-%Y-%m-%d-%H%M%S")
    folder.mkdir(parents=True)
    path = folder / "results.json"

    def save() -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    print(f"Report: {path}", flush=True)
    save()
    with model_lease():
        for key in keys:
            print(f"MODEL {key}", flush=True)
            try:
                with quiz_model(key, lambda s: print(s, flush=True)) as config:
                    for condition in ["question_only", "official_evidence"]:
                        if args.condition not in ("both", condition):
                            continue
                        quiz = DailyQuiz.model_validate(fixture["quiz"])
                        if condition == "official_evidence":
                            quiz.supplementary = fixture["evidence"]
                        for repeat in range(1, args.repeats + 1):
                            print(f"START {condition} repeat={repeat}", flush=True)
                            start = time.monotonic()
                            entry = {"model": key, "condition": condition, "repeat": repeat}
                            entry["loaded_model"] = {
                                "identifier": config.identifier,
                                "model_key": config.model_key,
                                "quant": config.quant,
                                "context_length": config.context_length,
                            }
                            try:
                                # No audit_path: never persist hidden model reasoning.
                                answer = QuizSolver(LMStudioClient(config)).solve(quiz)
                                entry.update(
                                    answer=answer.model_dump(),
                                    correct=answer.choice_index == fixture["expected_choice_index"],
                                    would_submit=answer.confidence >= 0.90,
                                )
                            except Exception as exc:
                                entry["error"] = f"{type(exc).__name__}: {exc}"
                            entry["seconds"] = round(time.monotonic() - start, 2)
                            report["results"].append(entry)
                            save()
                            print(json.dumps(entry, ensure_ascii=False), flush=True)
            except Exception as exc:
                report["results"].append({"model": key, "runtime_error": str(exc)})
                save()
                # Do not proceed after uncertain restoration or an active runtime.
                raise
    report["finished_at"] = datetime.now().astimezone().isoformat()
    save()


if __name__ == "__main__":
    main()
