"""Verify the app's saved model via its actual solver, without any website action."""

from __future__ import annotations

import json
import time
from datetime import datetime

from web_content_agent.onepoint3acres.models import DailyQuiz
from web_content_agent.onepoint3acres.runtime import create_solver
from web_content_agent.paths import project_root


def main() -> None:
    root = project_root()
    fixture = json.loads(
        (root / "tests/fixtures/onepoint3acres/points-quiz-model-comparison.json").read_text(
            encoding="utf-8"
        )
    )
    quiz = DailyQuiz.model_validate(fixture["quiz"])
    quiz.supplementary = fixture["evidence"]
    solver = create_solver(output=lambda message: print(message, flush=True))
    start = time.monotonic()
    answer = solver.solve(quiz)
    report = {
        "verified_at": datetime.now().astimezone().isoformat(),
        "selected_model": solver.key,
        "answer": answer.model_dump(),
        "matches_reference": answer.choice_index == fixture["expected_choice_index"],
        "seconds_including_model_switching": round(time.monotonic() - start, 2),
        "website_actions": 0,
    }
    target = root / ".local/experiments/selected-quiz-model-verification.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if not report["matches_reference"]:
        raise SystemExit("Selected model did not match the reference answer")


if __name__ == "__main__":
    main()
