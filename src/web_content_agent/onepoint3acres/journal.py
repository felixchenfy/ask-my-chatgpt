"""Private append-only run evidence. Never automatically promote records to knowledge."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


class QuizJournal:
    def __init__(self, workspace: Path) -> None:
        self.root = workspace.resolve()
        self._lock = threading.Lock()

    def today_records(self) -> list[dict[str, Any]]:
        day = datetime.now().astimezone().date().isoformat()
        path = self.root / "runs/onepoint3acres" / f"{day}.jsonl"
        with self._lock:
            if not path.resolve().is_relative_to(self.root):
                raise ValueError("Audit path outside workspace")
            if not path.exists():
                return []
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def daily_progress(self) -> dict[str, Any]:
        """Attempts, approvals and simulations are not evidence of completion."""
        records = self.today_records()
        starts = [
            r
            for r in records
            if r["event"] == "run_started" and r["data"].get("simulation") is False
        ]
        real_ids = {r["run_id"] for r in starts}
        outcomes = [
            r["data"]
            for r in records
            if r["run_id"] in real_ids
            and r["event"] in {"run_finished", "checkin_verified"}
            and not r["data"].get("simulation")
        ]
        checkin = any(r.get("checkin_verified") is True for r in outcomes)
        quiz = any(
            isinstance(r.get("quiz_outcome"), dict)
            and r["quiz_outcome"].get("verdict")
            in {"correct", "incorrect", "already_completed_unknown"}
            and bool(r["quiz_outcome"].get("evidence"))
            for r in outcomes
        )
        return {
            "local_date": datetime.now().astimezone().date().isoformat(),
            "attempted": bool(starts),
            "checkin_completed": checkin,
            "quiz_completed": quiz,
            "completed": checkin and quiz,
            "latest_run_id": starts[-1]["run_id"] if starts else None,
        }

    def append(self, run_id: str, event: str, data: dict[str, Any]) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
            raise ValueError("Invalid audit run id")
        now = datetime.now().astimezone()
        folder = self.root / "runs/onepoint3acres"
        path = folder / f"{now:%Y-%m-%d}.jsonl"
        record = {"recorded_at": now.isoformat(), "run_id": run_id, "event": event, "data": data}
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            # Resolve and create under the same lock: on Windows concurrent creation
            # of an ancestor can otherwise race path resolution for another writer.
            if not path.resolve().is_relative_to(self.root):
                raise ValueError("Audit path outside workspace")
            folder.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")
                stream.flush()
                os.fsync(stream.fileno())
