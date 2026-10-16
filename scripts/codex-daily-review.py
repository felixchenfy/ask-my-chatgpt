"""Narrow localhost client for the existing-thread daily reviewer. No LLM API calls."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

from web_content_agent.onepoint3acres.chrome_bridge import BRIDGE_PORT, bridge_token
from web_content_agent.onepoint3acres.supervision import review_token_path
from web_content_agent.paths import default_workspace_root, project_root


def request(path: str, payload: dict | None = None) -> dict:
    headers = {"X-WCA-Token": bridge_token()}
    if path == "/review/decision":
        headers["X-WCA-Review-Token"] = review_token_path().read_text(encoding="utf-8").strip()
    with httpx.Client(
        base_url=f"http://127.0.0.1:{BRIDGE_PORT}", headers=headers, timeout=15, trust_env=False
    ) as client:
        response = client.get(path) if payload is None else client.post(path, json=payload)
        result = response.json()
        if response.status_code == 409:
            return {
                **result,
                "conflict": True,
                "note": "Both daily steps completed, busy, or closing; inspect status/journal. "
                "Do not force retry.",
            }
        response.raise_for_status()
        return result


def ensure_app() -> dict:
    try:
        status = request("/status?log_limit=0")
    except httpx.RequestError:
        launcher = project_root() / ".venv/Scripts/wca-onepoint3acres-app.exe"
        if not launcher.is_file():
            raise FileNotFoundError(launcher) from None
        subprocess.Popen(
            [str(launcher)], cwd=project_root(), creationflags=subprocess.CREATE_NO_WINDOW
        )
        for _ in range(40):
            time.sleep(0.5)
            try:
                status = request("/status?log_limit=0")
                break
            except httpx.RequestError:
                continue
        else:
            raise RuntimeError("App did not expose its API; inspect logs before relaunching")
    if status.get("mode") != "codex_review" or status.get("internal_schedule_enabled") is not False:
        raise RuntimeError(
            "App is not in Codex review mode. Do not trigger a run; update/restart it first."
        )
    return status


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["ensure", "run", "recover", "status", "review", "decide", "simulate", "close"],
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="Explicit JSON quiz or decision file; never auto-selects an answer",
    )
    args = parser.parse_args()
    if args.command == "ensure":
        result = ensure_app()
    elif args.command == "run":
        ensure_app()
        result = request("/run-daily", {})
    elif args.command == "recover":
        from web_content_agent.onepoint3acres.journal import QuizJournal
        from web_content_agent.onepoint3acres.model_selection import model_lease
        from web_content_agent.onepoint3acres.recovery import authorize_recovery

        if args.file is None:
            parser.error("recover requires --file with parent_run_id and repair reason")
        payload = json.loads(args.file.read_text(encoding="utf-8-sig"))
        if set(payload) != {"parent_run_id", "reason"}:
            parser.error("recover accepts only parent_run_id and reason")
        with model_lease(project_root() / ".local/state/onepoint3acres-recovery.lock"):
            status = ensure_app()
            if status.get("running"):
                raise RuntimeError("Continue the active run, do not queue a recovery")
            journal = QuizJournal(default_workspace_root())
            authorize_recovery(journal, payload["parent_run_id"], payload["reason"])
            # Same outcome-based gate as ordinary daily runs; preserve the failed
            # parent link and reservation before issuing a new request.
            result = request("/run-daily", {})
            journal.append(payload["parent_run_id"], "recovery_dispatched", result)
    elif args.command in {"decide", "simulate"}:
        if args.file is None:
            parser.error("--file is required")
        payload = json.loads(args.file.read_text(encoding="utf-8-sig"))
        result = request("/review/decision" if args.command == "decide" else "/simulate", payload)
    elif args.command == "close":
        result = request("/close", {})
    else:
        result = request("/review" if args.command == "review" else "/status?log_limit=35")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
