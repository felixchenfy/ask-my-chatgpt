from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SiteCandidate(BaseModel):
    title: str
    url: str
    prominence: int
    context: str = ""


class RunStatus(BaseModel):
    run_id: str
    status: Literal["starting", "running", "success", "failed", "interrupted", "unknown"]
    stage: str
    completed: int = 0
    total: int = 0
    failed: int = 0
    current_item: str | None = None
    last_checkpoint: str | None = None
    elapsed_seconds: float | None = None
    can_resume: bool = False
    source_config: str | None = None


class ProcessRecord(BaseModel):
    run_id: str
    pid: int
    mode: Literal["run", "resume"]
    source_config: str
    started_at: str
    stdout_log: str
    stderr_log: str


class ToolResult(BaseModel):
    status: str
    message: str | None = None
    data: dict = Field(default_factory=dict)
