"""Explicit Codex recovery after a proven pre-submission failure, never blind retries."""

from __future__ import annotations

from datetime import datetime

from .journal import QuizJournal


def authorize_recovery(journal: QuizJournal, parent_run_id: str, reason: str) -> None:
    """Caller holds the recovery lock across this durable reservation and dispatch.

    A consumed permit is not completion. A fresh run rechecks both website pages
    and obtains a new review if the quiz is still explicitly unanswered.
    """
    if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 2000:
        raise ValueError("Recovery needs a concrete repair/evidence reason (10-2000 characters)")
    day = datetime.now().astimezone().date().isoformat()
    records = journal.today_records()
    starts = [
        r for r in records if r["event"] == "run_started" and r["data"].get("simulation") is False
    ]
    if not starts or starts[-1]["run_id"] != parent_run_id:
        raise ValueError("Recovery requires the latest real run from today")
    real_ids = {r["run_id"] for r in starts}
    real_records = [r for r in records if r["run_id"] in real_ids]
    if journal.daily_progress()["completed"]:
        raise ValueError("Today's check-in AND quiz are already verified complete")
    parent = [r for r in real_records if r["run_id"] == parent_run_id]
    if not any(r["event"] == "run_finished" for r in parent):
        raise ValueError("No explicit finished run; inspect or continue the active run")
    reservations = [r for r in records if r["event"] == "recovery_requested"]
    if any(r["run_id"] == parent_run_id for r in reservations):
        raise ValueError("Recovery already reserved for this run; do not repeat uncertain dispatch")
    journal.append(
        parent_run_id,
        "recovery_requested",
        {
            "actor": "codex",
            "reason": reason.strip(),
            "local_date": day,
            "submission_permit_seen": any(r["event"] == "submission_authorized" for r in parent),
            "attempt": len(reservations) + 1,
            "note": "Fresh run rechecks website state; old permits/audits remain unchanged",
        },
    )
