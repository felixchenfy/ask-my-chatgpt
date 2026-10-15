from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path


class DailyScheduleLedger:
    def __init__(self, path: Path, hour: int = 9, *, strict: bool = False) -> None:
        self.path = path
        self.hour = hour
        self.strict = strict
        self.last_scheduled_date: str | None = None
        self.last_started_at: str | None = None
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            if self.strict:
                raise
            return
        if self.strict and (
            not isinstance(data, dict)
            or not isinstance(data.get("last_scheduled_date"), str)
            or not isinstance(data.get("last_started_at"), str)
        ):
            raise ValueError("Invalid daily-run ledger; inspect before retrying")
        self.last_scheduled_date = data.get("last_scheduled_date")
        self.last_started_at = data.get("last_started_at")

    def is_due(self, now: datetime) -> bool:
        return now.hour == self.hour and self.last_scheduled_date != now.date().isoformat()

    def mark_started(self, now: datetime) -> None:
        self.last_scheduled_date = now.date().isoformat()
        self.last_started_at = now.astimezone().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "last_scheduled_date": self.last_scheduled_date,
                    "last_started_at": self.last_started_at,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def next_run(self, now: datetime) -> datetime:
        today = now.replace(hour=self.hour, minute=0, second=0, microsecond=0)
        if now < today and self.last_scheduled_date != now.date().isoformat():
            return today
        return today + timedelta(days=1)
