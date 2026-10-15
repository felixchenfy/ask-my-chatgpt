from __future__ import annotations

import re
import threading
from collections import deque
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

DATED_LINE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2})?)\]\s?(.*)$")
LEGACY_TIME_LINE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s?(.*)$")
LOG_RETENTION_DAYS = 7


class PersistentRunLog:
    """Append-only UTF-8 log that can be inspected without taking over the GUI."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        clean_line = line.rstrip("\r\n")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(f"{clean_line}\n")

    def tail(self, limit: int = 200) -> list[str]:
        bounded_limit = max(1, min(int(limit), 1000))
        with self._lock:
            if not self.path.is_file():
                return []
            with self.path.open(encoding="utf-8") as stream:
                return [line.rstrip("\r\n") for line in deque(stream, maxlen=bounded_limit)]

    def prune_older_than(
        self, days: int = LOG_RETENTION_DAYS, *, now: datetime | None = None
    ) -> int:
        if days < LOG_RETENTION_DAYS:
            raise ValueError("log retention must be at least seven days")
        current = now or datetime.now().astimezone()
        if current.tzinfo is None:
            current = current.astimezone()
        cutoff = current.astimezone(UTC) - timedelta(days=days)

        with self._lock:
            if not self.path.is_file():
                return 0
            file_time = datetime.fromtimestamp(self.path.stat().st_mtime, tz=current.tzinfo)
            original = self.path.read_text(encoding="utf-8").splitlines()
            kept: list[str] = []
            removed = 0
            for line in original:
                timestamp, normalized = self._normalize_line(line, file_time)
                if timestamp < cutoff:
                    removed += 1
                else:
                    kept.append(normalized)

            if removed or kept != original:
                temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
                payload = "".join(f"{line}\n" for line in kept)
                temporary.write_text(payload, encoding="utf-8", newline="\n")
                temporary.replace(self.path)
            return removed

    @staticmethod
    def _normalize_line(line: str, fallback: datetime) -> tuple[datetime, str]:
        dated = DATED_LINE.match(line)
        if dated:
            try:
                timestamp = datetime.fromisoformat(dated.group(1))
            except ValueError:
                return fallback, f"[{fallback.isoformat(sep=' ', timespec='seconds')}] {line}"
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=fallback.tzinfo)
            return timestamp, line

        legacy = LEGACY_TIME_LINE.match(line)
        if legacy:
            try:
                clock = time.fromisoformat(legacy.group(1))
            except ValueError:
                return fallback, f"[{fallback.isoformat(sep=' ', timespec='seconds')}] {line}"
            timestamp = datetime.combine(fallback.date(), clock, tzinfo=fallback.tzinfo)
            normalized = f"[{timestamp.isoformat(sep=' ', timespec='seconds')}] {legacy.group(2)}"
            return timestamp, normalized

        normalized = f"[{fallback.isoformat(sep=' ', timespec='seconds')}] {line}"
        return fallback, normalized
