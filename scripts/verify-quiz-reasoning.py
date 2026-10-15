"""Offline Gemma evaluation of aggregate answers using production prompt/knowledge."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

from web_content_agent.lmstudio import LMStudioClient
from web_content_agent.onepoint3acres.chrome_bridge import MIN_AUTO_ANSWER_CONFIDENCE
from web_content_agent.onepoint3acres.knowledge import knowledge_for
from web_content_agent.onepoint3acres.model_selection import model_lease, quiz_model, selected_model
from web_content_agent.onepoint3acres.models import DailyQuiz
from web_content_agent.onepoint3acres.solver import (
    PROMPT_VERSION,
    InsufficientQuizEvidence,
    QuizSolver,
)
from web_content_agent.paths import project_root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", help="Run only this named fixture case")
    args = parser.parse_args()
    root = project_root()
    fixtures = root / "tests/fixtures/onepoint3acres"
    original = json.loads(
        (fixtures / "points-quiz-model-comparison.json").read_text(encoding="utf-8")
    )
    cases = json.loads((fixtures / "aggregate-answer-cases.json").read_text(encoding="utf-8"))[
        "cases"
    ]
    if args.case:
        if set(args.case) - {case["name"] for case in cases}:
            parser.error("Unknown fixture case")
        cases = [case for case in cases if case["name"] in args.case]
    key = selected_model()
    if key != "google/gemma-4-12b-qat":
        raise SystemExit("This evaluation requires the app's selected Gemma 4 12B QAT")
    stamp = datetime.now().astimezone()
    path = root / f".local/experiments/quiz-reasoning-{stamp:%Y-%m-%d-%H%M%S}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "started_at": stamp.isoformat(),
        "model": key,
        "prompt_version": PROMPT_VERSION,
        "website_actions": 0,
        "limitations": (
            "Selected offline cases, one sample each; not live grading or an accuracy estimate. "
            "Partial search is synthetic, not a replay of the lost live snippet."
        ),
        "results": [],
    }

    def save():
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    output = lambda text: print(text, flush=True)  # noqa: E731
    print(f"Report: {path}", flush=True)
    with model_lease():
        with quiz_model(key, output) as config:
            for case in cases:
                if case.get("original_points_quiz"):
                    quiz = DailyQuiz.model_validate(original["quiz"])
                    if case.get("rotate_choices"):
                        quiz.choices = quiz.choices[1:] + quiz.choices[:1]
                        for index, choice in enumerate(quiz.choices, start=1):
                            choice.index = index
                else:
                    quiz = DailyQuiz(
                        question=case["question"],
                        choices=[{"index": i, "text": s} for i, s in enumerate(case["choices"], 1)],
                        source_url="offline-regression",
                    )
                quiz.supplementary = case.get("supplementary")
                quiz.local_knowledge = knowledge_for(quiz, output=output)
                entry = {"case": case["name"], "quiz": quiz.model_dump(), "passed": False}
                print(f"START {case['name']}", flush=True)
                start = time.monotonic()
                try:
                    answer = QuizSolver(LMStudioClient(config)).solve(quiz)
                    entry["answer"] = answer.model_dump()
                    entry["would_submit"] = answer.confidence >= MIN_AUTO_ANSWER_CONFIDENCE
                    entry["passed"] = (
                        not entry["would_submit"]
                        if case.get("expect_abstention")
                        else answer.choice_index == case["expected_choice_index"]
                        and entry["would_submit"]
                    )
                except InsufficientQuizEvidence as exc:
                    entry.update(
                        error=str(exc),
                        would_submit=False,
                        passed=bool(case.get("expect_abstention")),
                    )
                except Exception as exc:
                    entry["error"] = f"{type(exc).__name__}: {exc}"
                entry["seconds"] = round(time.monotonic() - start, 2)
                report["results"].append(entry)
                save()
                print(
                    json.dumps({k: v for k, v in entry.items() if k != "quiz"}, ensure_ascii=False),
                    flush=True,
                )
    report["finished_at"] = datetime.now().astimezone().isoformat()
    save()
    passed = sum(entry["passed"] for entry in report["results"])
    print(f"Passed: {passed}/{len(cases)}; website actions: 0", flush=True)
    if passed != len(cases):
        raise SystemExit("Some offline cases failed; preserve the report and inspect")


if __name__ == "__main__":
    main()
