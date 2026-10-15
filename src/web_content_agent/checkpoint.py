from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Checkpoint:
    def __init__(self, path: Path, initial: dict[str, Any] | None = None) -> None:
        self.path = path
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.data = initial or {
                "schema_version": 1,
                "current_stage": "initialized",
                "selected_articles": [],
                "articles": {},
                "completed_downloads": [],
                "completed_cleanup": [],
                "retry_counts": {},
                "last_success_at": None,
                "last_error": None,
                "can_resume": True,
            }
            self.save()

    def save(self) -> None:
        atomic_write_json(self.path, self.data)

    def update(self, stage: str, **changes: Any) -> None:
        self.data.update(changes)
        self.data["current_stage"] = stage
        self.data["last_success_at"] = datetime.now().astimezone().isoformat()
        self.save()

    def set_article(self, key: str, status: str, **changes: Any) -> None:
        article = self.data.setdefault("articles", {}).setdefault(key, {})
        article.update(changes)
        article["status"] = status
        article["updated_at"] = datetime.now().astimezone().isoformat()
        self.data["last_success_at"] = article["updated_at"]
        self.save()

    def record_error(self, error_type: str, message: str, can_resume: bool = True) -> None:
        self.data["last_error"] = {
            "type": error_type,
            "message": message,
            "timestamp": datetime.now().astimezone().isoformat(),
        }
        self.data["can_resume"] = can_resume
        self.save()

    def completed(self, key: str) -> bool:
        return self.data.get("articles", {}).get(key, {}).get("status") == "success"
