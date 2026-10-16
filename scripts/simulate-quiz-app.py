"""Replay a saved quiz through the running App API; never perform website actions."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

import httpx

from web_content_agent.onepoint3acres.chrome_bridge import bridge_token
from web_content_agent.paths import project_root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", choices=["saved", "none"], default="saved")
    args = parser.parse_args()
    root = project_root()
    fixture = json.loads(
        (root / "tests/fixtures/onepoint3acres/points-quiz-model-comparison.json").read_text(
            encoding="utf-8"
        )
    )
    payload = {
        "question": fixture["quiz"]["question"],
        "choices": [choice["text"] for choice in fixture["quiz"]["choices"]],
        "web_evidence": [
            {
                "title": "积分说明（已保存的人工摘要，不是本次 Google 搜索）",
                "url": "https://www.1point3acres.com/bbs/points.html",
                "snippet": fixture["evidence"],
            }
        ],
    }
    if args.evidence == "none":
        payload["web_evidence"] = []
    start = time.monotonic()
    with httpx.Client(
        base_url="http://127.0.0.1:18731", headers={"X-WCA-Token": bridge_token()}, timeout=10
    ) as client:
        before = client.get("/status?log_limit=80")
        before.raise_for_status()
        if before.json()["running"]:
            raise SystemExit("App is busy; not starting a simulation")
        response = client.post("/simulate", json=payload)
        response.raise_for_status()
        seen = set(before.json()["logs"])
        while time.monotonic() - start < 1200:
            response = client.get("/status?log_limit=80")
            response.raise_for_status()
            status = response.json()
            if status["run_id"] != before.json()["run_id"]:
                if not status.get("simulation"):
                    raise SystemExit("A different real run started; no further action taken")
                for line in status["logs"]:
                    if line not in seen:
                        print(line, flush=True)
                        seen.add(line)
                if status.get("quiz_solve_status") == "awaiting_review":
                    print(
                        "Simulation awaits explicit Codex review. "
                        "Use codex-daily-review.py review/decide. "
                        "No website action has been authorized.",
                        flush=True,
                    )
                    return
                if not status["running"] and status.get("result"):
                    break
            time.sleep(2)
        else:
            raise SystemExit("Simulation timed out; inspect /status before trying again")
    result = status["result"]
    stamp = datetime.now().astimezone()
    report = {
        "verified_at": stamp.isoformat(),
        "seconds": round(time.monotonic() - start, 2),
        "result": result,
        "matches_reference": result.get("answer", {}).get("choice_index")
        == fixture["expected_choice_index"],
        "evidence": (
            "Saved official-rule summary plus app knowledge"
            if args.evidence == "saved"
            else "No supplied web evidence; only the app's production knowledge lookup"
        ),
        "live_google_search": False,
        "website_grading": False,
    }
    target = root / f".local/experiments/app-simulation-{stamp:%Y-%m-%d-%H%M%S}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if not result.get("ok") or not report["matches_reference"] or result["website_actions"] != 0:
        raise SystemExit("Simulation failed; no website answer was submitted")


if __name__ == "__main__":
    main()
