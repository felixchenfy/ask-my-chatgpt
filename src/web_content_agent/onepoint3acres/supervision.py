"""Codex review is a separate, fail-closed gate, not another model API call."""

from __future__ import annotations

import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from web_content_agent.paths import project_root

from .chrome_bridge import ChromeBridge

INTERNAL_SCHEDULE_ENABLED = False
REVIEW_TIMEOUT_SECONDS = 900


def review_token_path() -> Path:
    return project_root() / ".local/state/onepoint3acres-review-token"


def load_review_token(path: Path | None = None) -> str:
    path = path or review_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(secrets.token_urlsafe(32))
    except FileExistsError:
        pass
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise ValueError("Invalid local review token; refusing to enable submissions")
    return token


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    run_id: str
    review_id: str
    action: Literal["answer", "skip"]
    choice_index: StrictInt | None = None
    choice_text: str | None = Field(default=None, max_length=4000)
    reason: str = Field(min_length=1, max_length=2000)
    sources: list[str] = Field(default_factory=list, max_length=10)


class SupervisedChromeBridge(ChromeBridge):
    review_required = True

    def __init__(self, output, journal, *, token_path: Path | None = None) -> None:
        super().__init__(output, journal)
        self.review_token = load_review_token(token_path)
        self._audit_failed: set[str] = set()
        self._contexts: dict[str, dict] = {}

    def _record(self, run_id: str, event: str, data: dict[str, Any]) -> None:
        data = {**data, "mode": "codex_review"}
        if event == "decision_context":
            self._contexts[run_id] = data
        try:
            if self.journal is None:
                raise ValueError("Review mode requires a durable journal")
            self.journal.append(run_id, event, data)
        except (OSError, ValueError):
            self._audit_failed.add(run_id)
            self.output("审计记录保存失败；本次运行禁止批准或提交答案。")

    def _require_record(self, run_id: str, event: str, data: dict) -> None:
        self._record(run_id, event, data)
        if run_id in self._audit_failed:
            raise RuntimeError("Audit persistence failed; submission is forbidden")

    def start_run(self, *, simulation: bool = False) -> str:
        run_id = super().start_run(simulation=simulation)
        if run_id in self._audit_failed:
            self.complete(run_id, {"ok": False, "error": "Audit persistence failed"})
            raise RuntimeError("Audit persistence failed; run not dispatched")
        return run_id

    def _run_solve_job(self, run_id: str, payload: dict[str, Any]) -> None:
        try:
            candidate = self.solve(run_id, payload)
            error = None
        except Exception as exc:
            candidate = None
            error = f"{type(exc).__name__}: {exc}"
        with self._lock:
            job = self._solve_jobs.get(run_id)
            if job is None or not self._command or self._command["run_id"] != run_id:
                return  # A timed-out/replaced run must never become approvable again.
            # An invalid page extraction cannot be rescued by approving fabricated choices.
            choices = payload.get("choices")
            if (
                not isinstance(payload.get("question"), str)
                or not payload["question"].strip()
                or not isinstance(choices, list)
                or not 2 <= len(choices) <= 8
                or any(not isinstance(c, str) or not c.strip() for c in choices)
            ):
                job.update(status="error", error="Invalid quiz snapshot")
                return
            now = datetime.now(UTC)
            job.update(
                status="awaiting_review",
                review_id=secrets.token_urlsafe(24),
                quiz={"question": payload["question"], "choices": list(choices)},
                local_answer=candidate,
                local_error=error,
                context=self._contexts.get(run_id),
                expires_at=(now + timedelta(seconds=REVIEW_TIMEOUT_SECONDS)).isoformat(),
                deadline=time.monotonic() + REVIEW_TIMEOUT_SECONDS,
            )
            self._record(
                run_id,
                "review_requested",
                {
                    "review_id": job["review_id"],
                    "quiz": job["quiz"],
                    "local_answer": candidate,
                    "local_error": error,
                    "simulation": self._simulation,
                    "expires_at": job["expires_at"],
                },
            )
        self.output(
            "[6/8] 等待 Codex 审核（不是模型卡住）：本地答案仅供参考；未批准不会选择或提交。"
        )

    def poll_solve(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = super().poll_solve(run_id, payload)
        if response["status"] == "awaiting_review":
            with self._lock:
                job = self._solve_jobs[run_id]
                if time.monotonic() >= job["deadline"]:
                    job.update(status="error", error="Codex review expired; not submitted")
                    return {"status": "error", "error": job["error"]}
            return {"status": "pending", "waiting_for": "codex_review"}
        return response

    def review(self) -> dict[str, Any]:
        with self._lock:
            run_id = self._last_run_id
            job = self._solve_jobs.get(run_id or "", {})
            active = bool(self._command and self._command["run_id"] == run_id)
            expired = time.monotonic() >= job.get("deadline", float("inf"))
            return json.loads(
                json.dumps(
                    {
                        "run_id": run_id,
                        "active": active,
                        "simulation": self._simulation,
                        "mode": "codex_review",
                        "audit_ok": run_id not in self._audit_failed,
                        "expired": expired,
                        **{
                            key: job[key]
                            for key in (
                                "status",
                                "review_id",
                                "quiz",
                                "local_answer",
                                "local_error",
                                "context",
                                "decision",
                                "expires_at",
                                "permit_used",
                            )
                            if key in job
                        },
                    },
                    ensure_ascii=False,
                )
            )

    def _review_job(self, run_id: str, review_id: str) -> dict:
        if not self._command or self._command["run_id"] != run_id:
            raise ValueError("Unknown or expired run")
        job = self._solve_jobs.get(run_id, {})
        if not review_id or job.get("review_id") != review_id:
            raise ValueError("Review does not match the frozen quiz")
        if time.monotonic() >= job["deadline"]:
            raise ValueError("Review expired; do not submit")
        if run_id in self._audit_failed:
            raise RuntimeError("Audit persistence failed; submission is forbidden")
        return job

    def decide(self, payload: dict) -> dict:
        decision = ReviewDecision.model_validate(payload)
        data = decision.model_dump()
        if any(not s.strip() or len(s) > 2048 for s in decision.sources):
            raise ValueError("Invalid decision sources")
        with self._lock:
            job = self._review_job(decision.run_id, decision.review_id)
            if job.get("decision"):
                if job["decision"] == data:
                    return {"accepted": True, "duplicate": True, "action": decision.action}
                raise ValueError("This review already has a final decision")
            if job["status"] != "awaiting_review":
                raise ValueError("Review is not ready")
            if decision.action == "answer":
                index = decision.choice_index
                choices = job["quiz"]["choices"]
                if index is None or not 1 <= index <= len(choices):
                    raise ValueError("choice_index outside frozen choices")
                if decision.choice_text != choices[index - 1]:
                    raise ValueError("choice_text does not match frozen choice_index")
            elif decision.choice_index is not None or decision.choice_text is not None:
                raise ValueError("A skip must not contain an answer")
            self._require_record(
                decision.run_id,
                "codex_decision",
                {
                    **data,
                    "actor": "codex",
                    "simulation": self._simulation,
                    "local_answer": job.get("local_answer"),
                    "quiz": job["quiz"],
                },
            )
            job["decision"] = data
            if decision.action == "answer":
                job.update(
                    status="complete",
                    answer={
                        "choice_index": decision.choice_index,
                        "choice_text": decision.choice_text,
                        "reason": decision.reason,
                        "review_id": decision.review_id,
                        "decided_by": "codex",
                    },
                )
            else:
                job.update(status="error", error=f"Codex skipped: {decision.reason}")
        label = "跳过，不提交" if decision.action == "skip" else "答案，等待 Chrome 下一次轮询执行"
        if self._simulation:
            label = "模拟答案；不会调用 Chrome"
        self.output(f"[7/8] Codex 已决定{label}：{decision.reason}")
        if decision.action == "skip" or self._simulation:
            self.complete(
                decision.run_id,
                {
                    "ok": True,
                    "skipped": decision.action == "skip",
                    "simulation": self._simulation,
                    "website_actions": 0 if self._simulation else None,
                    "codex_decision": data,
                    "quiz_submission": "not_submitted",
                },
            )
            self.output("[8/8] 本次未提交网站答案；模拟或弃答已结束。")
        return {"accepted": True, "action": decision.action}

    def _approved_live_job(self, payload: dict) -> dict:
        """Caller holds the lock; selection and submission share the same checks."""
        run_id = str(payload.get("run_id", ""))
        job = self._review_job(run_id, str(payload.get("review_id", "")))
        if self._simulation or job.get("status") != "complete" or not job.get("decision"):
            raise ValueError("No live Codex-approved answer")
        if job.get("permit_used"):
            raise ValueError("Submission permit already consumed; inspect outcome, never retry")
        if payload.get("quiz") != job["quiz"]:
            raise ValueError("The website question/options changed after Codex review")
        return job

    def prepare_selection(self, payload: dict) -> dict:
        """Validate current approval without consuming the final-submit permit."""
        with self._lock:
            self._approved_live_job(payload)
            return {"authorized": True, "submission_permit_consumed": False}

    def submission_permit(self, payload: dict) -> dict:
        """Consume once BEFORE final submit; uncertain delivery never permits retry."""
        with self._lock:
            run_id = str(payload.get("run_id", ""))
            job = self._approved_live_job(payload)
            self._require_record(
                run_id,
                "submission_authorized",
                {
                    "review_id": job["review_id"],
                    "decision": job["decision"],
                    "website_verdict": "unverified",
                    "note": "Permit is not proof of a click or grade",
                },
            )
            job["permit_used"] = True
            return {"authorized": True}

    def simulate(self, payload: dict[str, Any]) -> dict[str, Any]:
        run_id = self.start_run(simulation=True)
        self._claimed_event.set()
        self.output(f"[模拟 1/3] {run_id}：零网页操作，真实本地模型 + Codex 审核接口。")
        self.poll_solve(run_id, payload)
        return self.wait(REVIEW_TIMEOUT_SECONDS)

    def status(self) -> dict[str, Any]:
        return {
            **super().status(),
            "mode": "codex_review",
            "review_required": True,
            "internal_schedule_enabled": INTERNAL_SCHEDULE_ENABLED,
            "daily_progress": self.journal.daily_progress(),
        }
