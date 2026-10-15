"""Same-operation PDF continuation, no Chrome or live submission."""

import hashlib
import json
from datetime import timedelta

import pytest

from web_content_agent import chatgpt_chrome_bridge as module


@pytest.mark.parametrize(
    "case",
    [
        "exact",
        "exact-restored",
        "unexpected-pending",
        "unexpected-no-progress",
        "unexpected-no-name-proof",
        "unexpected-other-name",
        "unexpected-multiple",
        "unexpected-ready",
        "unexpected-other-filename",
        "restored-duplicate-tab",
        "restored-draft-text",
        "missing-diagnostic",
        "old-diagnostic",
        "changed-parent",
        "changed-file",
        "wrong-name",
        "draft-text",
        "uploading",
        "duplicate-card",
        "duplicate-tab",
        "wrong-tab-source",
        "wrong-phase",
        "wrong-error",
        "permit",
        "changed-question",
        "no-session",
        "auto-key",
        "force",
        "new-chat",
    ],
)
def test_only_fresh_bound_empty_attachment_draft_is_reused(tmp_path, monkeypatch, case):
    monkeypatch.setattr(module, "_attachment_policy", lambda: ([tmp_path], 1024))
    pdf = tmp_path / "review.pdf"
    pdf.write_bytes(b"%PDF-1.4\nOffline fixture\n")
    args = {
        "attachment_path": str(pdf),
        "conversation_url": "https://chatgpt.com/c/example",
        "session_key": "pdf-session",
        "idempotency_key": "pdf-operation",
    }
    if case == "no-session":
        args.pop("session_key")
    if case == "auto-key":
        args.pop("idempotency_key")
    old = module.ChatGptChromeBridge(tmp_path / "state")
    parent = old.start_message("Exact question", **args)
    old.claim_command()
    parent_observation = {
        "phase": "composer_ready" if case == "wrong-phase" else "attachment_staging"
    }
    if case.startswith("unexpected-"):
        parent_observation.update(
            composerDiagnostic={"composerCharacterCount": 0, "draftUploadInProgress": True},
            attachment={
                "state": "unexpected",
                "ready": False,
                "cardCount": 1,
                "expectedCardCount": 0,
                "expectedFilename": "review.pdf",
                "observedFilename": "",
                "filenameMatch": "mismatch",
            },
        )
        if case == "unexpected-no-progress":
            parent_observation["composerDiagnostic"]["draftUploadInProgress"] = False
        if case == "unexpected-no-name-proof":
            parent_observation["attachment"].pop("observedFilename")
        if case == "unexpected-other-name":
            parent_observation["attachment"]["observedFilename"] = "other.pdf"
        if case == "unexpected-multiple":
            parent_observation["attachment"]["cardCount"] = 2
        if case == "unexpected-ready":
            parent_observation["attachment"]["ready"] = True
        if case == "unexpected-other-filename":
            parent_observation["attachment"]["expectedFilename"] = "other.pdf"
    old.observe(parent, parent_observation)
    old.complete(
        parent,
        {
            "ok": False,
            "status": "failed",
            "error": "Other error"
            if case == "wrong-error"
            else "ChatGPT attachment staging is unexpected"
            if case.startswith("unexpected-")
            else "ChatGPT did not finish staging review.pdf",
        },
    )
    original = old.record_path(parent).read_bytes()
    diagnostic = {
        "failed_run_id": parent,
        "failed_record_unchanged": True,
        "failed_record_sha256": hashlib.sha256(original).hexdigest(),
        "observed_at": (
            module._now() - timedelta(minutes=10 if case == "old-diagnostic" else 0)
        ).isoformat(),
        "attachment_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "send_tasks_queued": 0,
        "response_reads_queued": 0,
        "last_observation": {
            "phase": "composer_diagnostic",
            "tabId": 17,
            "tabSource": "created"
            if case == "wrong-tab-source"
            else "existing"
            if "restored" in case
            else "managed",
            "composerDiagnostic": {
                "matchingTabCount": 2 if case in {"duplicate-tab", "restored-duplicate-tab"} else 1,
                "composerCharacterCount": 1 if case in {"draft-text", "restored-draft-text"} else 0,
                "draftUploadInProgress": case == "uploading",
            },
            "attachment": {
                "state": "ready",
                "ready": True,
                "visible": True,
                "filenameMatch": "exact",
                "cardCount": 2 if case == "duplicate-card" else 1,
                "expectedCardCount": 1,
                "expectedFilename": "review.pdf",
                "observedFilename": "other.pdf" if case == "wrong-name" else "review.pdf",
            },
        },
    }
    if case != "missing-diagnostic":
        old.record_path(f"inspect-{parent}").write_text(json.dumps(diagnostic), encoding="utf-8")
    if case == "changed-parent":
        old._update_record(parent, extra_observation=True)
    if case == "permit":
        old._update_record(parent, submission_authorized=True)
    if case == "changed-file":
        pdf.write_bytes(b"%PDF-1.4\nChanged revision\n")
    preserved = old.record_path(parent).read_bytes()
    new = module.ChatGptChromeBridge(tmp_path / "state")
    retry_args = dict(args)
    if case == "new-chat":
        retry_args.pop("conversation_url")
    run = new.start_message(
        "Changed question" if case == "changed-question" else "Exact question",
        **retry_args,
        force_send=case == "force",
        new_conversation=case == "new-chat",
    )
    command = new.claim_command()
    if case in {"exact", "exact-restored", "unexpected-pending"}:
        assert command["preserved_queued_attachment"] == {"run_id": parent, "tab_id": 17}
        assert (
            new._read_record(run)["preserved_queued_attachment"]
            == command["preserved_queued_attachment"]
        )
    elif command:
        assert "preserved_queued_attachment" not in command
    assert old.record_path(parent).read_bytes() == preserved
