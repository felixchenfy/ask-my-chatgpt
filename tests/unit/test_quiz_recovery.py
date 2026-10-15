import pytest

from web_content_agent.onepoint3acres.journal import QuizJournal
from web_content_agent.onepoint3acres.recovery import authorize_recovery


@pytest.fixture
def journal(tmp_path):
    journal = QuizJournal(tmp_path)
    journal.append("failed-run", "run_started", {"simulation": False})
    journal.append("failed-run", "run_finished", {"ok": False, "error": "extraction error"})
    return journal


def test_reserves_once_before_dispatch_and_preserves_failure(journal):
    authorize_recovery(journal, "failed-run", "Fixed extraction; live page shows unanswered quiz")
    with pytest.raises(ValueError, match="already reserved"):
        authorize_recovery(journal, "failed-run", "Do not retry uncertain dispatch")


@pytest.mark.parametrize(
    "event,data",
    [
        (
            "run_finished",
            {
                "ok": True,
                "checkin_verified": True,
                "quiz_outcome": {"verdict": "correct", "evidence": "graded"},
            },
        ),
        (
            "run_finished",
            {
                "ok": False,
                "checkin_verified": True,
                "quiz_outcome": {"verdict": "incorrect", "evidence": "graded"},
            },
        ),
    ],
)
def test_both_completed_steps_skip_the_whole_day(journal, event, data):
    journal.append("failed-run", event, data)
    with pytest.raises(ValueError):
        authorize_recovery(
            journal, "failed-run", "Even a repair does not authorize a duplicate answer"
        )


def test_active_or_other_day_or_simulated_run_is_not_recovery(journal):
    journal.append("active-run", "run_started", {"simulation": False})
    with pytest.raises(ValueError, match="latest real"):
        authorize_recovery(journal, "failed-run", "stale parent cannot be retried")
    with pytest.raises(ValueError, match="No explicit finished"):
        authorize_recovery(journal, "active-run", "active parent cannot be retried")
    journal.append("simulation", "run_started", {"simulation": True})
    journal.append("simulation", "run_finished", {"ok": False})
    with pytest.raises(ValueError):
        authorize_recovery(journal, "simulation", "simulation cannot prove website state")


def test_no_arbitrary_daily_repair_cap(journal):
    for index in range(4):
        run = f"repair-{index}"
        journal.append(run, "run_started", {"simulation": False})
        journal.append(run, "run_finished", {"ok": False})
        authorize_recovery(journal, run, "A separate verified pre-submission repair")


@pytest.mark.parametrize(
    "data",
    [
        {"ok": False, "checkin_verified": True},
        {"ok": True, "skipped": True},
        {"ok": False, "quiz_outcome": {"verdict": "unknown", "evidence": "timeout"}},
        {"ok": False, "quiz_outcome": {"verdict": "incorrect", "evidence": "graded"}},
    ],
)
def test_consumed_permit_or_partial_completion_does_not_block_fresh_state_check(journal, data):
    journal.append("failed-run", "submission_authorized", {})
    journal.append("failed-run", "run_finished", data)
    authorize_recovery(
        journal, "failed-run", "Repair finished; fresh run must check website before actions"
    )
    assert journal.today_records()[-1]["data"]["submission_permit_seen"] is True


@pytest.mark.parametrize(
    "simulation,checkin,verdict,evidence,expected",
    [
        (False, True, "correct", "explicit grade", True),
        (False, True, "incorrect", "explicit grade", True),
        (False, True, "already_completed_unknown", "already answered", True),
        (False, True, "unknown", "timeout", False),
        (False, True, "ambiguous", "conflicting messages", False),
        (False, True, "correct", "", False),
        (False, False, "correct", "explicit grade", False),
        (True, True, "correct", "simulation grade", False),
    ],
)
def test_progress_requires_both_real_explicit_results(
    journal, simulation, checkin, verdict, evidence, expected
):
    journal.append("latest", "run_started", {"simulation": simulation})
    journal.append(
        "latest",
        "run_finished",
        {
            "simulation": simulation,
            "ok": True,
            "checkin_verified": checkin,
            "quiz_outcome": {"verdict": verdict, "evidence": evidence},
        },
    )
    assert journal.daily_progress()["completed"] is expected
    assert QuizJournal(journal.root).daily_progress()["completed"] is expected
