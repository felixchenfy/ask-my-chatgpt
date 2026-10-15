from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .checkpoint import atomic_write_json


def make_run_id(name: str) -> str:
    now = datetime.now().astimezone()
    timestamp = now.strftime("%Y-%m-%dT%H%M%S%z")
    return f"{timestamp}-{name}"


class RunManifest:
    def __init__(self, path: Path, run_id: str, source_path: Path) -> None:
        self.path = path
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.data: dict[str, Any] = {
                "schema_version": 1,
                "run_id": run_id,
                "status": "running",
                "started_at": datetime.now().astimezone().isoformat(),
                "finished_at": None,
                "source_config": str(source_path),
                "articles": [],
                "image_counts": {"candidate": 0, "keep": 0, "drop": 0, "review": 0},
                "failures": [],
            }
            self.save()

    def save(self) -> None:
        atomic_write_json(self.path, self.data)

    def finish(
        self, articles: list[dict], image_counts: dict[str, int], elapsed_seconds: float
    ) -> None:
        self.data.update(
            {
                "status": "success",
                "finished_at": datetime.now().astimezone().isoformat(),
                "elapsed_seconds": round(elapsed_seconds, 3),
                "articles": articles,
                "image_counts": image_counts,
            }
        )
        self.save()

    def fail(self, error_type: str, message: str) -> None:
        self.data["status"] = "failed"
        self.data["finished_at"] = datetime.now().astimezone().isoformat()
        self.data.setdefault("failures", []).append({"type": error_type, "message": message})
        self.save()
