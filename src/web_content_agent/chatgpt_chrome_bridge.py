from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

from web_content_agent.chatgpt_pairing import load_pairing_token
from web_content_agent.chrome_companion import (
    negotiate_companion,
    read_companion_extension_version,
)
from web_content_agent.paths import runtime_root

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 18732
MAX_QUESTION_LENGTH = 100_000
DEDUPLICATION_WINDOW = timedelta(minutes=30)
THROTTLE_COOLDOWN = timedelta(minutes=30)
COMPANION_CLAIM_TIMEOUT_SECONDS = 75.0
COMPANION_RESULT_GRACE_SECONDS = 30.0
AUTOMATIC_READ_RECOVERY_TIMEOUT_SECONDS = 600
SESSION_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RUN_ID_PATTERN = re.compile(r"chatgpt-\d{8}-\d{6}-[0-9a-f]{6}")
ThinkingMode = Literal["current", "prefer_max", "max"]

_OBSERVATION_FIELDS = frozenset(
    "phase submitted messageId candidateMessageId candidateVerified candidateMounted "
    "candidateCount matches promptContainerPresent expectedPromptLength observedPromptLength "
    "stableIdPresent composerEmpty "
    "attachment userCount baselineUserCount baselineIdCount orderingAnchorPresent identityEvidence "
    "destinationMatches conversationUrl renderEvidence generating fatalError confirmationElapsedMs "
    "confirmationBudgetMs submissionAttempted submissionConfirmed submissionOutcomeUnknown "
    "zeroSubmissionProven hasNewResponse responseCharacterCount responseLineCount "
    "expectationMatches responseComplete completionEvidence generationEvidence "
    "responseTextPresent responseCandidateCount responseTextEvidence "
    "conversationUrlCanonical awaitingSubmittedMessage composerErrorCode composerEvidence "
    "composerElapsedMs composerBudgetMs tabLoadEvidence destinationEvidence tabSource "
    "inspectionPhase thinkingEvidence tabId selectedFileCount composerDiagnostic".split()
)

_THINKING_EVIDENCE_FIELDS = frozenset(
    "requestedMode verified selectionAttempted unsupportedReason triggerCount selectedLabel "
    "observedComposerLabels sliderPresent sliderMinimum sliderMaximum sliderValue "
    "sliderDescription".split()
)

_COMPOSER_DIAGNOSTIC_FIELDS = frozenset(
    "formPresent formContainsComposer selectedFileCount selectedFilename "
    "selectedFilesInForm composerCharacterCount matchingTabCount draftUploadInProgress "
    "sendButtonPresent sendButtonType sendButtonDisabled".split()
)


def _bounded_observation(value: Any) -> dict[str, Any]:
    """Structural diagnostics only; never persist arbitrary DOM/prompt bodies."""
    if not isinstance(value, dict):
        return {}

    def scalar(item: Any) -> Any:
        if isinstance(item, str):
            return item[:256]
        if isinstance(item, (bool, int, float)) or item is None:
            return item
        return None

    return {
        key: (
            {
                str(k)[:64]: scalar(v)
                for k, v in list(item.items())[:24]
                if (
                    key != "composerEvidence"
                    or (k in {"present", "visible"} and isinstance(v, bool))
                )
                and (key != "thinkingEvidence" or k in _THINKING_EVIDENCE_FIELDS)
                and (key != "composerDiagnostic" or k in _COMPOSER_DIAGNOSTIC_FIELDS)
            }
            if isinstance(item, dict)
            else scalar(item)
        )
        for key, item in value.items()
        if key in _OBSERVATION_FIELDS
    }


def _now() -> datetime:
    return datetime.now().astimezone()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _validate_session_key(value: str | None) -> str | None:
    if value is None:
        return None
    key = value.strip()
    if not SESSION_KEY_PATTERN.fullmatch(key):
        raise ValueError(
            "session_key must be 1-128 characters using letters, digits, '.', '_', or '-'"
        )
    return key


def _validate_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    key = value.strip()
    if not SESSION_KEY_PATTERN.fullmatch(key):
        raise ValueError(
            "idempotency_key must be 1-128 characters using letters, digits, '.', '_', or '-'"
        )
    return key


def _validate_conversation_url(value: str | None) -> str:
    url = (value or "https://chatgpt.com/").strip()
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "chatgpt.com"
        or parsed.port not in (None, 443)
    ):
        raise ValueError("conversation_url must use https://chatgpt.com/")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("conversation_url must not contain credentials, a query, or a fragment")
    path = parsed.path.rstrip("/") or "/"
    path = re.sub(r"^/c/local-chatgpt%3[aA]", "/c/local-chatgpt:", path)
    project = re.fullmatch(r"/g/g-p-[A-Za-z0-9-]+/c/([A-Za-z0-9-]+)", path)
    if project:
        path = f"/c/{project.group(1)}"
    local_provisional = re.fullmatch(
        r"/c/local-chatgpt:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        path,
    )
    if (
        path != "/"
        and not re.fullmatch(r"/c/(?:WEB:)?[A-Za-z0-9-]+", path)
        and not local_provisional
    ):
        raise ValueError("conversation_url must be ChatGPT home or a supported conversation route")
    return f"https://chatgpt.com{path}"


def _canonical_conversation_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        url = _validate_conversation_url(value)
    except ValueError:
        return None
    path = urlparse(url).path
    if not path.startswith("/c/"):
        return None
    conversation_id = path.removeprefix("/c/")
    return None if conversation_id.startswith(("WEB:", "local-chatgpt:")) else url


def _validate_thinking_mode(value: str) -> ThinkingMode:
    if value not in {"current", "prefer_max", "max"}:
        raise ValueError("thinking_mode must be 'current', 'prefer_max', or 'max'")
    return value


def _validate_expected_count(value: int | None, name: str, maximum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer between 1 and {maximum}")
    return value


def _validate_expected_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    digest = value.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("expected_response_sha256 must contain exactly 64 hexadecimal characters")
    return digest


def _normalize_response_text(value: str) -> str:
    lines = [
        re.sub(r"[\t ]+", " ", line).strip()
        for line in unicodedata.normalize("NFC", value)
        .replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .replace("\ufeff", "")
        .splitlines()
    ]
    kept: list[str] = []
    for line in lines:
        if line or (kept and kept[-1]):
            kept.append(line)
    return "\n".join(kept).strip()


def _response_metrics(value: str) -> tuple[str, int, int, str]:
    normalized = _normalize_response_text(value)
    characters = len(normalized.replace("\n", ""))
    lines = len(normalized.split("\n")) if normalized else 0
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return normalized, characters, lines, digest


def _attachment_policy() -> tuple[list[Path], int]:
    config_path = Path(
        os.getenv(
            "CHATGPT_PDF_REVIEW_CONFIG",
            str(Path.home() / ".codex" / "chatgpt-pdf-review" / "config.json"),
        )
    ).expanduser()
    if not config_path.is_file():
        raise FileNotFoundError(
            f"ChatGPT PDF policy was not found at {config_path}; run the bridge setup first"
        )
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    roots = [Path(value).expanduser().resolve() for value in config.get("allowedPdfRoots", [])]
    if not roots:
        raise ValueError("allowedPdfRoots must contain at least one directory")
    maximum_bytes = int(config.get("maximumPdfSizeMb", 512)) * 1024 * 1024
    return roots, maximum_bytes


def _validate_attachment_path(value: str | None) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        raise ValueError("attachment_path must be absolute")
    if requested.suffix.lower() != ".pdf":
        raise ValueError("only PDF attachments are accepted")
    try:
        resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise FileNotFoundError(f"PDF was not found or is unreadable: {requested}") from exc
    if not resolved.is_file():
        raise ValueError("attachment_path must refer to a regular file")
    roots, maximum_bytes = _attachment_policy()
    comparable = str(resolved).casefold() if os.name == "nt" else str(resolved)
    contained = False
    for root in roots:
        root_value = str(root).casefold() if os.name == "nt" else str(root)
        if comparable == root_value or comparable.startswith(root_value.rstrip("\\/") + os.sep):
            contained = True
            break
    if not contained:
        raise PermissionError(f"PDF is outside the configured allowed roots: {resolved}")
    if resolved.stat().st_size > maximum_bytes:
        raise ValueError(f"PDF exceeds the configured {maximum_bytes}-byte limit")
    return str(resolved), resolved.name


class ChatGptChromeBridge:
    def __init__(self, state_root: Path | None = None) -> None:
        self.state_root = state_root or runtime_root() / "state" / "chatgpt-bridge"
        self._lock = threading.Lock()
        self._command: dict[str, Any] | None = None
        self._claimed = False
        self._claimed_event = threading.Event()
        self._submitted_event = threading.Event()
        self._completed = threading.Event()
        self._result: dict[str, Any] | None = None
        self._active_request_key: str | None = None
        self._preflight_result: dict[str, Any] | None = None
        self.cancel_event = threading.Event()

    @property
    def shared_state_path(self) -> Path:
        return self.state_root / "bridge-state.json"

    @contextmanager
    def _shared_state(self):
        self.state_root.mkdir(parents=True, exist_ok=True)
        lock_path = self.state_root / "bridge-state.lock"
        with lock_path.open("a+b") as lock_file:
            # Cross-process lock: duplicate-send protection must hold even when
            # two MCP server processes share one state directory.
            if os.name == "nt":
                import msvcrt

                if lock_file.seek(0, os.SEEK_END) == 0:
                    lock_file.write(b"0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                if self.shared_state_path.is_file():
                    state = json.loads(self.shared_state_path.read_text(encoding="utf-8"))
                else:
                    state = {"schema_version": 1, "sessions": {}, "requests": {}, "throttle": {}}
                yield state
                temporary = self.shared_state_path.with_suffix(".json.tmp")
                temporary.write_text(
                    json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                temporary.replace(self.shared_state_path)
            finally:
                if os.name == "nt":
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _request_key(
        self,
        question: str,
        destination: str,
        attachment: str | None,
        thinking_mode: str,
        session_key: str | None,
        idempotency_key: str | None,
        minimum_response_characters: int | None,
        expected_response_characters: int | None,
        expected_response_lines: int | None,
        expected_response_sha256: str | None,
    ) -> str:
        if idempotency_key:
            return f"explicit:{idempotency_key}"
        payload = json.dumps(
            [
                question,
                session_key or destination,
                attachment,
                thinking_mode,
                minimum_response_characters,
                expected_response_characters,
                expected_response_lines,
                expected_response_sha256,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"auto:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"

    def _retryable_zero_submission(self, previous: dict[str, Any]) -> bool:
        """Only a terminal, authoritative no-permit record releases a fingerprint.

        This permits a later caller-authorized attempt; it never starts one.
        Missing, inconsistent or older evidence remains blocked.
        """
        statuses = {"failed", "extension_not_connected", "cancelled_before_submission"}
        run_id = previous.get("run_id")
        if previous.get("status") not in statuses or not isinstance(run_id, str):
            return False
        if not re.fullmatch(r"chatgpt-\d{8}-\d{6}-[0-9a-f]{6}", run_id):
            return False
        try:
            record = self._read_record(run_id)
        except (OSError, ValueError):
            return False
        if (
            not isinstance(record, dict)
            or record.get("run_id") != run_id
            or record.get("status") not in statuses
            or record.get("zero_submission_proven") is not True
            or record.get("message_submitted") is not False
        ):
            return False
        for evidence in (previous, previous.get("result", {}), record, record.get("result", {})):
            if not isinstance(evidence, dict) or evidence.get("ok"):
                return False
            if evidence.get("zero_submission_proven") is False:
                return False
            if any(
                evidence.get(field)
                for field in (
                    "submission_authorized",
                    "submission_attempted",
                    "message_submitted",
                    "submitted_message_id",
                    "submitted_at",
                )
            ):
                return False
        return True

    def _inspected_queued_attachment(self, parent: dict[str, Any]) -> dict[str, Any] | None:
        """Reuse only a fresh, production-inspected empty attachment draft.

        The diagnostic is tied to the unchanged failed parent, not caller fields.
        The worker rechecks the exact tab, current card and blank text before input.
        """
        run_id = parent["run_id"]
        attachment = parent.get("attachment_path")
        filename = parent.get("attachment_filename")
        parent_observation = parent.get("last_observation", {})
        parent_card = parent_observation.get("attachment", {})
        parent_composer = parent_observation.get("composerDiagnostic", {})
        pending_card_failure = (
            parent.get("result", {}).get("error") == "ChatGPT attachment staging is unexpected"
            and isinstance(parent_composer, dict)
            and isinstance(parent_card, dict)
            and parent_composer.get("draftUploadInProgress") is True
            and parent_composer.get("composerCharacterCount") == 0
            and parent_card.get("state") == "unexpected"
            and parent_card.get("ready") is False
            and parent_card.get("cardCount") == 1
            and parent_card.get("expectedCardCount") == 0
            and parent_card.get("expectedFilename") == filename
            and parent_card.get("observedFilename") == ""
            and parent_card.get("filenameMatch") == "mismatch"
        )
        if (
            not attachment
            or parent_observation.get("phase") != "attachment_staging"
            or (
                parent.get("result", {}).get("error")
                != f"ChatGPT did not finish staging {filename}"
                and not pending_card_failure
            )
        ):
            return None
        try:
            diagnostic = self._read_record(f"inspect-{run_id}")
            observed_at = _parse_datetime(diagnostic.get("observed_at"))
            observation = diagnostic.get("last_observation", {})
            card = observation.get("attachment", {})
            composer = observation.get("composerDiagnostic", {})
            age = _now() - observed_at if observed_at else None
            if (
                diagnostic.get("failed_run_id") != run_id
                or diagnostic.get("failed_record_unchanged") is not True
                or diagnostic.get("failed_record_sha256")
                != hashlib.sha256(self.record_path(run_id).read_bytes()).hexdigest()
                or age is None
                or not timedelta(0) <= age <= timedelta(minutes=5)
                or diagnostic.get("send_tasks_queued") != 0
                or diagnostic.get("response_reads_queued") != 0
                or observation.get("phase") != "composer_diagnostic"
                # A user can reopen the same conversation in a new existing tab.
                # Unique canonical matching is established by the production
                # diagnostic; the worker still requires this exact inspected ID.
                or observation.get("tabSource") not in {"managed", "existing"}
                or type(observation.get("tabId")) is not int
                or composer.get("matchingTabCount") != 1
                or composer.get("composerCharacterCount") != 0
                or composer.get("draftUploadInProgress") is not False
                or card.get("state") != "ready"
                or card.get("ready") is not True
                or card.get("visible") is not True
                or card.get("cardCount") != 1
                or card.get("expectedCardCount") != 1
                or card.get("filenameMatch") != "exact"
                or card.get("expectedFilename") != filename
                or card.get("observedFilename") != filename
                or diagnostic.get("attachment_sha256")
                != hashlib.sha256(Path(attachment).read_bytes()).hexdigest()
            ):
                return None
        except (OSError, ValueError, TypeError, AttributeError):
            return None
        return {"run_id": run_id, "tab_id": observation["tabId"]}

    def correct_unsent_thinking_mode(
        self,
        *,
        idempotency_key: str,
        expected_run_id: str,
        source_task: str,
        reason: str,
        apply: bool = False,
    ) -> dict[str, Any]:
        """Audit an authorized max -> prefer_max correction; never queue browser work."""
        key = _validate_idempotency_key(idempotency_key)
        if not key or not _validate_session_key(source_task):
            raise ValueError("An explicit operation key and source task are required")
        if not reason.strip() or len(reason) > 1024:
            raise ValueError("A concise authorization/correction reason is required")
        request_key = f"explicit:{key}"
        with self._lock, self._shared_state() as state:
            previous = state.get("requests", {}).get(request_key, {})
            if self._command is not None or previous.get("run_id") != expected_run_id:
                raise ValueError("The expected failed run is no longer the current operation")
            if previous.get("status") != "failed" or not self._retryable_zero_submission(previous):
                raise ValueError("Correction requires authoritative terminal zero submission")
            record = self._read_record(expected_run_id)
            if record.get("thinking_mode") != "max" or record.get("idempotency_key") != key:
                raise ValueError("Only an erroneous strict-Max request can be corrected")

            def fingerprint(mode: str) -> str:
                return self._request_key(
                    record["question"],
                    record["conversation_url"],
                    record.get("attachment_path"),
                    mode,
                    record.get("session_key"),
                    None,
                    record.get("minimum_response_characters"),
                    record.get("expected_response_characters"),
                    record.get("expected_response_lines"),
                    record.get("expected_response_sha256"),
                )

            old_fingerprint, new_fingerprint = fingerprint("max"), fingerprint("prefer_max")
            existing = previous.get("thinking_mode_correction")
            if (
                isinstance(existing, dict)
                and existing.get("from_run_id") == expected_run_id
                and existing.get("from_fingerprint") == old_fingerprint
                and existing.get("to_fingerprint") == new_fingerprint
                and previous.get("request_fingerprint") == new_fingerprint
            ):
                return {"status": "already_corrected", "tasks_queued": 0, "correction": existing}
            if previous.get("request_fingerprint") != old_fingerprint:
                raise ValueError("The failed record and operation fingerprint do not agree")
            audit = {
                "from_run_id": expected_run_id,
                "source_task": source_task,
                "reason": reason.strip(),
                "from_mode": "max",
                "to_mode": "prefer_max",
                "from_fingerprint": old_fingerprint,
                "to_fingerprint": new_fingerprint,
                "original_record_sha256": hashlib.sha256(
                    self.record_path(expected_run_id).read_bytes()
                ).hexdigest(),
                "at": _now().isoformat(),
            }
            if apply:
                previous["request_fingerprint"] = new_fingerprint
                previous["thinking_mode_correction"] = audit
                state.setdefault("thinking_mode_corrections", {})[expected_run_id] = audit
            return {
                "status": "corrected" if apply else "correction_ready",
                "tasks_queued": 0,
                "correction": audit,
            }

    def _preflight(
        self,
        state: dict[str, Any],
        request_key: str,
        session_key: str | None,
        destination: str,
        force_send: bool,
        request_fingerprint: str,
    ) -> tuple[dict[str, Any] | None, str]:
        now = _now()
        blocked_until = _parse_datetime(state.get("throttle", {}).get("blocked_until"))
        if blocked_until and blocked_until > now and not force_send:
            return (
                {
                    "ok": False,
                    "status": "throttled_cooldown",
                    "error": (
                        "ChatGPT reported throttling; no browser action or "
                        "submission was attempted."
                    ),
                    "retry_after": blocked_until.isoformat(),
                    "message_submitted": False,
                },
                destination,
            )
        if session_key and destination == "https://chatgpt.com/":
            destination = str(
                state.get("sessions", {}).get(session_key, {}).get("conversation_url")
                or destination
            )
        previous = state.get("requests", {}).get(request_key)
        created_at = _parse_datetime((previous or {}).get("created_at"))
        recent = created_at is not None and now - created_at <= DEDUPLICATION_WINDOW
        # Time can expire a convenience fingerprint, never an explicit operation
        # identity or an unresolved external side effect. Legacy records without
        # a timestamp/fingerprint remain protected as well.
        persistent = request_key.startswith("explicit:") or (
            previous and previous.get("status") != "completed"
        )
        if previous and (recent or persistent) and not force_send:
            if previous.get("request_fingerprint") not in (None, request_fingerprint):
                return (
                    {
                        "ok": False,
                        "status": "idempotency_conflict",
                        "error": "This operation key belongs to different request arguments.",
                        "message_submitted": bool(previous.get("message_submitted", False)),
                        "original_run_id": previous.get("run_id"),
                    },
                    destination,
                )
            result = previous.get("result")
            if previous.get("status") == "completed" and isinstance(result, dict):
                return (
                    {**result, "deduplicated": True, "original_run_id": previous.get("run_id")},
                    destination,
                )
            if self._retryable_zero_submission(previous):
                return None, destination
            # Carry the original run's message identity so a caller whose first
            # attempt was interrupted can do the single bound read without
            # digging through task records. Nothing is submitted again.
            original: dict[str, Any] = {}
            original_run_id = previous.get("run_id")
            if isinstance(original_run_id, str) and RUN_ID_PATTERN.fullmatch(original_run_id):
                try:
                    original = self._read_record(original_run_id)
                except (OSError, ValueError):
                    original = {}
            return (
                {
                    "ok": False,
                    "status": "duplicate_blocked",
                    "error": (
                        "This operation was already started and remains protected; "
                        "it was not submitted again."
                    ),
                    "message_submitted": bool(
                        previous.get("message_submitted", False)
                        or original.get("message_submitted", False)
                    ),
                    "submitted_message_id": str(original.get("submitted_message_id") or ""),
                    "conversation_url": str(original.get("conversation_url") or destination or ""),
                    "original_run_id": original_run_id,
                },
                destination,
            )
        return None, destination

    def start_message(
        self,
        question: str,
        conversation_url: str | None = None,
        attachment_path: str | None = None,
        thinking_mode: str = "current",
        session_key: str | None = None,
        idempotency_key: str | None = None,
        new_conversation: bool = False,
        force_send: bool = False,
        minimum_response_characters: int | None = None,
        expected_response_characters: int | None = None,
        expected_response_lines: int | None = None,
        expected_response_sha256: str | None = None,
        response_timeout_seconds: int = 900,
    ) -> str:
        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("question must not be empty")
        if len(normalized_question) > MAX_QUESTION_LENGTH:
            raise ValueError(f"question must not exceed {MAX_QUESTION_LENGTH} characters")
        verified_session_key = _validate_session_key(session_key)
        verified_idempotency_key = _validate_idempotency_key(idempotency_key)
        if new_conversation and conversation_url:
            raise ValueError("new_conversation cannot be combined with conversation_url")
        destination = _validate_conversation_url(None if new_conversation else conversation_url)
        verified_thinking_mode = _validate_thinking_mode(thinking_mode)
        verified_minimum_characters = _validate_expected_count(
            minimum_response_characters, "minimum_response_characters", 1_000_000
        )
        verified_expected_characters = _validate_expected_count(
            expected_response_characters, "expected_response_characters", 1_000_000
        )
        verified_expected_lines = _validate_expected_count(
            expected_response_lines, "expected_response_lines", 100_000
        )
        verified_expected_sha256 = _validate_expected_sha256(expected_response_sha256)
        if not 1 <= response_timeout_seconds <= 3600:
            raise ValueError("response_timeout_seconds must be between 1 and 3600")
        attachment, attachment_filename = _validate_attachment_path(attachment_path)
        request_key = self._request_key(
            normalized_question,
            destination,
            attachment,
            verified_thinking_mode,
            verified_session_key,
            verified_idempotency_key,
            verified_minimum_characters,
            verified_expected_characters,
            verified_expected_lines,
            verified_expected_sha256,
        )
        request_fingerprint = self._request_key(
            normalized_question,
            destination,
            attachment,
            verified_thinking_mode,
            verified_session_key,
            None,
            verified_minimum_characters,
            verified_expected_characters,
            verified_expected_lines,
            verified_expected_sha256,
        )
        # Serialize validation + reservation across callers/processes. A failed
        # fingerprint must not be released to two simultaneous retry calls.
        with self._lock, self._shared_state() as state:
            immediate, destination = self._preflight(
                state,
                request_key,
                None if new_conversation else verified_session_key,
                destination,
                force_send,
                request_fingerprint,
            )
            if immediate is not None:
                self._preflight_result = immediate
                return ""
            if self._command is not None and not self._completed.is_set():
                raise RuntimeError("another ChatGPT request is already running")
            now = _now()
            run_id = f"chatgpt-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
            command = {
                "action": "ask_chatgpt",
                "run_id": run_id,
                "question": normalized_question,
                "conversation_url": destination,
                "attachment_path": attachment,
                "attachment_filename": attachment_filename,
                "thinking_mode": verified_thinking_mode,
                "session_key": verified_session_key,
                "new_conversation": new_conversation,
                "response_timeout_seconds": response_timeout_seconds,
            }
            if verified_minimum_characters is not None:
                command["minimum_response_characters"] = verified_minimum_characters
            if verified_expected_characters is not None:
                command["expected_response_characters"] = verified_expected_characters
            if verified_expected_lines is not None:
                command["expected_response_lines"] = verified_expected_lines
            if verified_expected_sha256 is not None:
                command["expected_response_sha256"] = verified_expected_sha256
            self._command = command
            self._claimed = False
            self._claimed_event.clear()
            self._submitted_event.clear()
            self._completed.clear()
            self._result = None
            self._preflight_result = None
            self._active_request_key = request_key
            record = {
                "schema_version": 1,
                "run_id": run_id,
                "status": "queued",
                "created_at": now.isoformat(),
                "question": normalized_question,
                "conversation_url": destination,
                "attachment_path": attachment,
                "attachment_filename": attachment_filename,
                "thinking_mode": verified_thinking_mode,
                "session_key": verified_session_key,
                "idempotency_key": verified_idempotency_key,
                "message_submitted": False,
                "response_timeout_seconds": response_timeout_seconds,
            }
            for key in (
                "minimum_response_characters",
                "expected_response_characters",
                "expected_response_lines",
                "expected_response_sha256",
            ):
                if key in command:
                    record[key] = command[key]
            previous = state.get("requests", {}).get(request_key, {})
            if self._retryable_zero_submission(previous):
                record["retry_of_run_id"] = previous["run_id"]
                parent = self._read_record(previous["run_id"])
                parent_tab = parent.get("last_observation", {}).get("tabId")
                parent_observation = parent.get("last_observation", {})
                input_verification_failed = parent.get("result", {}).get("error") == (
                    "ChatGPT composer does not contain the exact queued question"
                )
                verified_draft_send_unready = (
                    parent.get("result", {}).get("error") == "ChatGPT Send button is not ready"
                    and parent_observation.get("phase") == "composer_input_verification"
                    and parent_observation.get("matches") is True
                )
                # Recover only the same explicit text-only operation after its
                # input-verification failure. The worker must still match the
                # entire visible draft in this exact tab and must not type again.
                if (
                    verified_idempotency_key
                    and verified_session_key
                    and not force_send
                    and not new_conversation
                    and attachment is None
                    and parent.get("attachment_path") is None
                    and parent.get("question") == normalized_question
                    and parent.get("session_key") == verified_session_key
                    and (input_verification_failed or verified_draft_send_unready)
                    and type(parent_tab) is int
                ):
                    draft = {"run_id": previous["run_id"], "tab_id": parent_tab}
                    command["preserved_queued_draft"] = draft
                    record["preserved_queued_draft"] = draft
                if (
                    verified_idempotency_key
                    and verified_session_key
                    and not force_send
                    and not new_conversation
                    and attachment == parent.get("attachment_path")
                    and parent.get("question") == normalized_question
                    and parent.get("session_key") == verified_session_key
                    and _canonical_conversation_url(destination)
                    and _canonical_conversation_url(parent.get("conversation_url"))
                    == _canonical_conversation_url(destination)
                ):
                    queued_attachment = self._inspected_queued_attachment(parent)
                    if queued_attachment:
                        command["preserved_queued_attachment"] = queued_attachment
                        record["preserved_queued_attachment"] = queued_attachment
                correction = previous.get("thinking_mode_correction")
                if correction and correction.get("to_fingerprint") == request_fingerprint:
                    record["thinking_mode_correction"] = correction
            self._write_record(
                run_id,
                record,
            )
            state.setdefault("requests", {})[request_key] = {
                "request_fingerprint": request_fingerprint,
                "run_id": run_id,
                "status": "queued",
                "created_at": now.isoformat(),
                "message_submitted": False,
            }
            canonical_destination = _canonical_conversation_url(destination)
            if verified_session_key and canonical_destination:
                state.setdefault("sessions", {})[verified_session_key] = {
                    "conversation_url": canonical_destination,
                    "updated_at": now.isoformat(),
                }
            return run_id

    def start_read(
        self,
        conversation_url: str,
        minimum_response_characters: int | None = None,
        expected_response_characters: int | None = None,
        expected_response_lines: int | None = None,
        expected_response_sha256: str | None = None,
        response_timeout_seconds: int = 120,
        expected_message_id: str | None = None,
    ) -> str:
        destination = _validate_conversation_url(conversation_url)
        if destination == "https://chatgpt.com/":
            raise ValueError("read_chatgpt_conversation requires a canonical /c/... URL")
        now = datetime.now().astimezone()
        if not 1 <= response_timeout_seconds <= 600:
            raise ValueError("response_timeout_seconds must be between 1 and 600")
        run_id = f"chatgpt-read-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
        command = {
            "action": "read_chatgpt_conversation",
            "run_id": run_id,
            "conversation_url": destination,
            "response_timeout_seconds": response_timeout_seconds,
        }
        if expected_message_id:
            if len(expected_message_id) > 256:
                raise ValueError("expected_message_id is too long")
            command["expected_message_id"] = expected_message_id
        expected = {
            "minimum_response_characters": _validate_expected_count(
                minimum_response_characters, "minimum_response_characters", 1_000_000
            ),
            "expected_response_characters": _validate_expected_count(
                expected_response_characters, "expected_response_characters", 1_000_000
            ),
            "expected_response_lines": _validate_expected_count(
                expected_response_lines, "expected_response_lines", 100_000
            ),
            "expected_response_sha256": _validate_expected_sha256(expected_response_sha256),
        }
        command.update({key: value for key, value in expected.items() if value is not None})
        with self._lock:
            if self._command is not None and not self._completed.is_set():
                raise RuntimeError("another ChatGPT request is already running")
            self._command = command
            self._claimed = False
            self._claimed_event.clear()
            self._submitted_event.clear()
            self._completed.clear()
            self._result = None
            self._active_request_key = None
            record = {
                "schema_version": 1,
                "run_id": run_id,
                "action": "read_chatgpt_conversation",
                "status": "queued",
                "created_at": now.isoformat(),
                "conversation_url": destination,
                "response_timeout_seconds": response_timeout_seconds,
                "expected_message_id": expected_message_id,
            }
            record.update({key: value for key, value in expected.items() if value is not None})
            self._write_record(
                run_id,
                record,
            )
        return run_id

    def claim_command(
        self,
        extension_version: str = "",
        worker_instance_id: str = "",
        protocol_version: int | None = None,
        expected_version: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if expected_version is not None:
                negotiation = negotiate_companion(
                    observed_version=extension_version,
                    observed_protocol=protocol_version,
                    expected_version=expected_version,
                )
                if not negotiation["compatible"]:
                    return negotiation
            if self._command is None:
                return {"action": "idle"}
            record = self._read_record(self._command["run_id"])
            if self._claimed:
                claimed_worker = str(record.get("companion", {}).get("worker_instance_id", ""))
                worker_restarted = bool(
                    worker_instance_id and claimed_worker and worker_instance_id != claimed_worker
                )
                original_is_read_only = self._command.get("action") == "read_chatgpt_conversation"
                message_submitted = bool(record.get("message_submitted", False))
                if not worker_restarted or not (original_is_read_only or message_submitted):
                    return {"action": "idle"}

                conversation_url = _canonical_conversation_url(
                    str(
                        record.get("conversation_url")
                        or self._command.get("conversation_url")
                        or ""
                    )
                )
                if conversation_url is None:
                    return {"action": "idle"}
                message_id = record.get("submitted_message_id") or self._command.get(
                    "expected_message_id"
                )
                if not original_is_read_only and not message_id:
                    return {"action": "idle"}
                recovery_command: dict[str, Any] = {
                    "action": "read_chatgpt_conversation",
                    "run_id": self._command["run_id"],
                    "conversation_url": conversation_url,
                    "message_submitted": message_submitted,
                    "recovered_after_companion_restart": True,
                }
                if message_id:
                    recovery_command["expected_message_id"] = message_id
                for key in (
                    "minimum_response_characters",
                    "expected_response_characters",
                    "expected_response_lines",
                    "expected_response_sha256",
                    "response_timeout_seconds",
                ):
                    if key in self._command:
                        recovery_command[key] = self._command[key]
                events = list(record.get("events", []))
                events.append(
                    {
                        "at": _now().isoformat(),
                        "message": (
                            "A new Chrome companion worker reclaimed the interrupted task "
                            "in read-only recovery mode."
                        ),
                    }
                )
                companion = {
                    "extension_version": extension_version[:64],
                    "worker_instance_id": worker_instance_id[:128],
                    "protocol_version": protocol_version,
                }
                self._write_record(
                    self._command["run_id"],
                    {
                        **record,
                        "status": "recovering_after_companion_restart",
                        "companion": companion,
                        "recovery_count": int(record.get("recovery_count", 0)) + 1,
                        "events": events[-50:],
                    },
                )
                return recovery_command
            self._claimed = True
            self._claimed_event.set()
            changes: dict[str, Any] = {"status": "running"}
            if extension_version or worker_instance_id:
                changes["companion"] = {
                    "extension_version": extension_version[:64],
                    "worker_instance_id": worker_instance_id[:128],
                    "protocol_version": protocol_version,
                }
            self._update_record(self._command["run_id"], **changes)
            return dict(self._command)

    def event(self, run_id: str, message: str) -> None:
        with self._lock:
            if self._command is None or run_id != self._command.get("run_id"):
                return
            record = self._read_record(run_id)
            events = list(record.get("events", []))
            events.append(
                {"at": datetime.now().astimezone().isoformat(), "message": message[:1000]}
            )
            self._write_record(run_id, {**record, "events": events[-50:]})

    def begin_submission(self, run_id: str) -> bool:
        """Single-use commit boundary. A granted permit is ambiguous until acknowledged."""
        with self._lock:
            if (
                self.cancel_event.is_set()
                or not self._command
                or self._command.get("run_id") != run_id
            ):
                return False
            record = self._read_record(run_id)
            if record.get("submission_authorized") or self._command.get("action") != "ask_chatgpt":
                return False
            self._update_record(run_id, submission_authorized=True, zero_submission_proven=False)
            return True

    def observe(self, run_id: str, observation: Any) -> bool:
        """Save bounded evidence and acknowledge liveness, without granting a permit.

        Candidate IDs remain diagnostics. Only /submitted can bind response identity.
        The worker stops observing when cancellation/timeout revokes this command.
        """
        with self._lock:
            if (
                self.cancel_event.is_set()
                or self._completed.is_set()
                or not self._command
                or self._command.get("run_id") != run_id
            ):
                return False
            safe = _bounded_observation(observation)
            record = self._read_record(run_id)
            self._update_record(
                run_id,
                last_observation=safe,
                submission_attempted=bool(
                    record.get("submission_attempted") or safe.get("submissionAttempted")
                ),
            )
            return True

    def cancel_active(self) -> None:
        self.cancel_event.set()
        with self._lock:
            if self._command is None:
                return
            run_id = self._command["run_id"]
            record = self._read_record(run_id)
            submitted = bool(record.get("message_submitted"))
            uncertain = bool(record.get("submission_authorized")) and not submitted
            status = (
                "cancelled_after_submission"
                if submitted
                else (
                    "cancelled_submission_unknown" if uncertain else "cancelled_before_submission"
                )
            )
            result = {
                "ok": False,
                "status": status,
                "message_submitted": submitted,
                "zero_submission_proven": not submitted and not uncertain,
                "response_extraction_verified": False,
                "last_observation": _bounded_observation(record.get("last_observation")),
                "submission_attempted": bool(record.get("submission_attempted")),
                "submitted_message_id": record.get("submitted_message_id", ""),
            }
            self._update_record(run_id, **result)
            request_key = self._active_request_key
            self._command = None
            self._active_request_key = None
            self._result = result
            self._claimed_event.set()
            self._completed.set()
        if request_key:
            with self._shared_state() as state:
                state.setdefault("requests", {}).setdefault(request_key, {}).update(result)

    def mark_submitted(
        self, run_id: str, conversation_url: str | None = None, message_id: str | None = None
    ) -> None:
        with self._lock:
            if self._command is None or run_id != self._command.get("run_id"):
                return
            submitted_at = _now().isoformat()
            canonical_url = _canonical_conversation_url(conversation_url)
            changes: dict[str, Any] = {
                "status": "submitted",
                "message_submitted": True,
                "submitted_at": submitted_at,
                "zero_submission_proven": False,
            }
            if message_id and len(message_id) <= 256:
                changes["submitted_message_id"] = message_id
            if conversation_url:
                changes["conversation_url"] = _validate_conversation_url(conversation_url)
            self._update_record(run_id, **changes)
            self._submitted_event.set()
            request_key = self._active_request_key
            session_key = self._command.get("session_key")
        if request_key:
            with self._shared_state() as state:
                request = state.setdefault("requests", {}).setdefault(request_key, {})
                request.update(changes)
                if session_key and canonical_url:
                    state.setdefault("sessions", {})[session_key] = {
                        "conversation_url": canonical_url,
                        "updated_at": submitted_at,
                    }

    def complete(self, run_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            if self._command is None or run_id != self._command.get("run_id"):
                return
            record = self._read_record(run_id)
            safe_result = {
                "ok": bool(result.get("ok")),
                "status": str(result.get("status", "completed" if result.get("ok") else "failed")),
                "response_text": str(result.get("response_text", "")),
                "conversation_url": str(result.get("conversation_url", "")),
                "error": str(result.get("error", "")),
                "thinking_mode": str(result.get("thinking_mode", "")),
                "thinking_mode_requested": str(result.get("thinking_mode_requested", "")),
                "thinking_mode_verified": bool(result.get("thinking_mode_verified", False)),
                "thinking_mode_selection_attempted": bool(
                    result.get("thinking_mode_selection_attempted", False)
                ),
                "thinking_mode_fallback_reason": str(
                    result.get("thinking_mode_fallback_reason", "")
                ),
                "response_extraction_verified": bool(
                    result.get("response_extraction_verified", False)
                ),
                "message_submitted": bool(
                    record.get("message_submitted", False) or result.get("message_submitted", False)
                ),
                "last_observation": (
                    _bounded_observation(result.get("last_observation"))
                    or _bounded_observation(record.get("last_observation"))
                ),
                "submission_attempted": bool(
                    record.get("submission_attempted") or result.get("submission_attempted")
                ),
                # A callback cannot override the authoritative single-use permit.
                "zero_submission_proven": not bool(
                    record.get("submission_authorized")
                    or record.get("message_submitted")
                    or result.get("message_submitted")
                    or record.get("submission_attempted")
                    or result.get("submission_attempted")
                ),
                "submitted_message_id": record.get("submitted_message_id", ""),
            }
            normalized, character_count, line_count, digest = _response_metrics(
                safe_result["response_text"]
            )
            safe_result.update(
                {
                    "response_character_count": character_count,
                    "response_line_count": line_count,
                    "response_sha256": digest,
                }
            )
            expectation_errors: list[str] = []
            if not _canonical_conversation_url(safe_result["conversation_url"]):
                expectation_errors.append(
                    "ChatGPT has not resolved a stable canonical conversation URL"
                )
            minimum_characters = self._command.get("minimum_response_characters")
            expected_characters = self._command.get("expected_response_characters")
            expected_lines = self._command.get("expected_response_lines")
            expected_sha256 = self._command.get("expected_response_sha256")
            if minimum_characters is not None and character_count < minimum_characters:
                expectation_errors.append(
                    f"expected at least {minimum_characters} characters "
                    f"but received {character_count}"
                )
            if expected_characters is not None and character_count != expected_characters:
                expectation_errors.append(
                    f"expected {expected_characters} characters but received {character_count}"
                )
            if expected_lines is not None and line_count != expected_lines:
                expectation_errors.append(
                    f"expected {expected_lines} lines but received {line_count}"
                )
            if expected_sha256 is not None and digest != expected_sha256:
                expectation_errors.append("response SHA-256 did not match the expected text")
            if safe_result["ok"] and expectation_errors:
                safe_result.update(
                    {
                        "ok": False,
                        "status": "response_unverified",
                        "error": "; ".join(expectation_errors),
                        "response_extraction_verified": False,
                    }
                )
            if safe_result["ok"]:
                _validate_conversation_url(safe_result["conversation_url"])
                if not safe_result["response_text"].strip():
                    raise ValueError("Chrome returned an empty ChatGPT response")
                if not safe_result["response_extraction_verified"]:
                    raise ValueError("Chrome did not verify ChatGPT response extraction")
            self._result = safe_result
            command = dict(self._command)
            request_key = self._active_request_key
            self._command = None
            self._active_request_key = None
            self._update_record(
                run_id,
                status="completed" if safe_result["ok"] else "failed",
                completed_at=datetime.now().astimezone().isoformat(),
                result=safe_result,
                last_observation=safe_result["last_observation"],
                zero_submission_proven=safe_result["zero_submission_proven"],
                submission_attempted=safe_result["submission_attempted"],
            )
        with self._shared_state() as state:
            if request_key:
                request = state.setdefault("requests", {}).setdefault(request_key, {})
                request.update(
                    {
                        "status": safe_result["status"],
                        "completed_at": _now().isoformat(),
                        "message_submitted": safe_result["message_submitted"],
                        "result": safe_result,
                    }
                )
            session_key = command.get("session_key")
            canonical_result_url = _canonical_conversation_url(safe_result["conversation_url"])
            if (
                session_key
                and canonical_result_url
                and (safe_result["ok"] or safe_result["message_submitted"])
            ):
                state.setdefault("sessions", {})[session_key] = {
                    "conversation_url": canonical_result_url,
                    "updated_at": _now().isoformat(),
                }
            if safe_result["status"] == "throttled":
                state["throttle"] = {
                    "blocked_until": (_now() + THROTTLE_COOLDOWN).isoformat(),
                    "observed_at": _now().isoformat(),
                }
        self._completed.set()

    def wait(self, timeout_seconds: float = 900) -> dict[str, Any]:
        claim_timeout = min(COMPANION_CLAIM_TIMEOUT_SECONDS, timeout_seconds)
        if not self._claimed_event.wait(claim_timeout):
            with self._lock:
                # A claim may win after Event.wait times out but before this lock.
                if not self._claimed and not self._completed.is_set():
                    command = self._command
                    request_key = self._active_request_key
                    self._command = None
                    self._active_request_key = None
                    if command:
                        self._update_record(
                            command["run_id"],
                            status="extension_not_connected",
                            zero_submission_proven=True,
                        )
                        if request_key:
                            with self._shared_state() as state:
                                state.setdefault("requests", {}).setdefault(request_key, {}).update(
                                    status="extension_not_connected",
                                    zero_submission_proven=True,
                                    message_submitted=False,
                                )
                    raise TimeoutError(
                        "Chrome Companion did not claim the task within the connection window. "
                        "No task was claimed or submitted; inspect the local bridge and "
                        "Companion connection before retrying the unchanged request."
                    )
        with self._lock:
            command = dict(self._command or {})
        response_timeout = float(command.get("response_timeout_seconds", timeout_seconds))
        phase_deadline = time.monotonic() + timeout_seconds + COMPANION_RESULT_GRACE_SECONDS
        submitted_deadline: float | None = None
        while not self._completed.is_set():
            if self._submitted_event.is_set() and submitted_deadline is None:
                submitted_deadline = (
                    time.monotonic() + response_timeout + COMPANION_RESULT_GRACE_SECONDS
                )
            deadline = submitted_deadline or phase_deadline
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._completed.wait(min(0.25, remaining))
        if not self._completed.is_set():
            with self._lock:
                command = self._command
                self._command = None
                if command:
                    record = self._read_record(command["run_id"])
                    submitted = bool(record.get("message_submitted", False))
                    self._update_record(
                        command["run_id"],
                        status="timeout_after_submission"
                        if submitted
                        else "timeout_before_submission",
                    )
                    request_key = self._active_request_key
                    self._active_request_key = None
                else:
                    submitted = False
                    request_key = None
            if request_key:
                with self._shared_state() as state:
                    state.setdefault("requests", {}).setdefault(request_key, {}).update(
                        {
                            "status": "timeout_after_submission"
                            if submitted
                            else "timeout_before_submission",
                            "message_submitted": submitted,
                        }
                    )
            raise TimeoutError(
                "ChatGPT timed out after the message was submitted; automatic resend is blocked"
                if submitted
                else "ChatGPT did not complete before the requested timeout"
            )
        return dict(self._result or {"ok": False, "error": "Chrome returned no result"})

    def record_path(self, run_id: str) -> Path:
        return self.state_root / f"{run_id}.json"

    def _read_record(self, run_id: str) -> dict[str, Any]:
        return json.loads(self.record_path(run_id).read_text(encoding="utf-8"))

    def _write_record(self, run_id: str, record: dict[str, Any]) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        target = self.record_path(run_id)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(target)

    def _update_record(self, run_id: str, **changes: Any) -> None:
        self._write_record(run_id, {**self._read_record(run_id), **changes})


class ChatGptBridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    # HTTPServer enables SO_REUSEADDR by default. On Windows that permits two
    # live processes to bind this port, splitting Companion traffic between runs.
    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, bridge: ChatGptChromeBridge) -> None:
        self.bridge = bridge
        self.pairing_token = load_pairing_token()
        super().__init__((BRIDGE_HOST, BRIDGE_PORT), ChatGptBridgeHandler)


class ChatGptBridgeHandler(BaseHTTPRequestHandler):
    server: ChatGptBridgeServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _cors(self) -> None:
        origin = self.headers.get("Origin", "")
        if re.fullmatch(r"chrome-extension://[a-p]{32}", origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-WCA-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        origin = self.headers.get("Origin", "")
        allowed_origin = not origin or bool(re.fullmatch(r"chrome-extension://[a-p]{32}", origin))
        return allowed_origin and secrets.compare_digest(
            self.headers.get("X-WCA-Token", ""), self.server.pairing_token
        )

    def _body(self) -> dict[str, Any]:
        length = min(int(self.headers.get("Content-Length", "0")), 2_000_000)
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request body must be an object")
        return value

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not re.fullmatch(r"chrome-extension://[a-p]{32}", self.headers.get("Origin", "")):
            self._json(403, {"error": "forbidden origin"})
            return
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not self._authorized():
            # Migration advertises only public version metadata, never a command or secret.
            # Old installed workers know /command, and can safely reload to load pairing.
            origin = self.headers.get("Origin", "")
            if parsed.path == "/command" and (
                not origin or re.fullmatch(r"chrome-extension://[a-p]{32}", origin)
            ):
                self._json(
                    200,
                    {
                        "action": "reload_extension",
                        "expected_extension_version": read_companion_extension_version(),
                        "protocol_version": 1,
                    },
                )
            else:
                self._json(403, {"error": "forbidden"})
        elif parsed.path == "/command":
            query = parse_qs(parsed.query)
            raw_protocol = (query.get("protocol_version") or [""])[0].strip()
            try:
                protocol_version = int(raw_protocol) if raw_protocol else None
                expected_version = read_companion_extension_version()
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._json(503, {"error": f"extension version unavailable: {exc}"})
                return
            self._json(
                200,
                self.server.bridge.claim_command(
                    extension_version=(query.get("extension_version") or [""])[0],
                    worker_instance_id=(query.get("worker_instance_id") or [""])[0],
                    protocol_version=protocol_version,
                    expected_version=expected_version,
                ),
            )
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._json(403, {"error": "forbidden"})
            return
        try:
            body = self._body()
            run_id = str(body.get("run_id", ""))
            if self.path == "/before-submit":
                self._json(200, {"ok": self.server.bridge.begin_submission(run_id)})
            elif self.path == "/observation":
                self._json(
                    200,
                    {"ok": self.server.bridge.observe(run_id, body.get("last_observation"))},
                )
            elif self.path == "/event":
                self.server.bridge.event(run_id, str(body.get("message", "")))
                self._json(200, {"ok": True})
            elif self.path == "/submitted":
                self.server.bridge.mark_submitted(
                    run_id,
                    str(body.get("conversation_url", "")) or None,
                    str(body.get("message_id", "")) or None,
                )
                self._json(200, {"ok": True})
            elif self.path == "/complete":
                self.server.bridge.complete(run_id, body)
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "not found"})
        except Exception as exc:
            self._json(400, {"error": f"{type(exc).__name__}: {exc}"})


_bridge = ChatGptChromeBridge()
_server: ChatGptBridgeServer | None = None
_server_lock = threading.Lock()


class BridgeBusyError(RuntimeError):
    """Another process owns the local bridge; this request was never queued."""


def _busy_result() -> dict[str, Any]:
    return {
        "ok": False,
        "status": "bridge_busy",
        "error": "Another local request owns the bridge. Wait for that operation to finish.",
        "message_submitted": False,
        "zero_submission_proven": True,
    }


def ensure_bridge_server() -> ChatGptChromeBridge:
    global _server
    with _server_lock:
        if _server is None:
            try:
                _server = ChatGptBridgeServer(_bridge)
            except OSError as exc:
                if exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) in {
                    10048,  # WSAEADDRINUSE
                    10013,  # WSAEACCES when an exclusive Windows listener owns the port
                }:
                    raise BridgeBusyError("Another process owns the local bridge") from exc
                raise
            threading.Thread(target=_server.serve_forever, daemon=True).start()
    return _bridge


def _wait_for_bridge_server(
    timeout_seconds: float, cancel_event: threading.Event
) -> ChatGptChromeBridge | None:
    """Wait only for transport ownership; never queue, claim, cancel or retry a task.

    Each installed adapter call has its own Python process. Competing calls must
    finish this admission step before creating records or touching Chrome. Once
    admitted, the existing durable idempotency and submit-permit checks still run.
    """
    deadline = time.monotonic() + timeout_seconds
    while not cancel_event.is_set():
        try:
            return ensure_bridge_server()
        except BridgeBusyError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            cancel_event.wait(min(0.5, remaining))
    return None


def _cancelled_before_queue_result() -> dict[str, Any]:
    return {
        "ok": False,
        "status": "cancelled_before_submission",
        "error": "Cancelled while waiting for the local bridge; no task was queued.",
        "message_submitted": False,
        "zero_submission_proven": True,
    }


def ensure_chrome_running() -> None:
    if os.name == "nt":
        check = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if "chrome.exe" in check.stdout.casefold():
            return
    candidates = [
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    chrome_path = next((item for item in candidates if item and Path(item).is_file()), None)
    if chrome_path is None:
        raise RuntimeError("Google Chrome was not found")
    subprocess.Popen(
        [chrome_path, "--no-first-run", "--no-default-browser-check"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def ask_chatgpt_data(
    question: str,
    conversation_url: str | None = None,
    attachment_path: str | None = None,
    timeout_seconds: int = 900,
    thinking_mode: str = "current",
    session_key: str | None = None,
    idempotency_key: str | None = None,
    new_conversation: bool = False,
    force_send: bool = False,
    minimum_response_characters: int | None = None,
    expected_response_characters: int | None = None,
    expected_response_lines: int | None = None,
    expected_response_sha256: str | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    if not 1 <= timeout_seconds <= 3600:
        raise ValueError("timeout_seconds must be between 1 and 3600")
    cancel_event = cancel_event or threading.Event()
    try:
        bridge = _wait_for_bridge_server(timeout_seconds, cancel_event)
    except BridgeBusyError:
        return _busy_result()
    if bridge is None:
        return _cancelled_before_queue_result()
    bridge.cancel_event = cancel_event
    run_id = bridge.start_message(
        question,
        conversation_url,
        attachment_path,
        thinking_mode,
        session_key,
        idempotency_key,
        new_conversation,
        force_send,
        minimum_response_characters,
        expected_response_characters,
        expected_response_lines,
        expected_response_sha256,
        timeout_seconds,
    )
    if not run_id:
        return dict(bridge._preflight_result or {"ok": False, "status": "preflight_failed"})
    if bridge.cancel_event.is_set():
        bridge.cancel_active()
        return {
            **(bridge._result or {}),
            "run_id": run_id,
            "task_file": str(bridge.record_path(run_id)),
        }
    ensure_chrome_running()
    try:
        result = bridge.wait(timeout_seconds)
    except TimeoutError as exc:
        record_path = bridge.record_path(run_id)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        result = {
            "ok": False,
            "status": record.get("status", "timeout"),
            "response_text": "",
            "conversation_url": str(record.get("conversation_url", "")),
            "message_submitted": bool(record.get("message_submitted")),
            "zero_submission_proven": bool(record.get("zero_submission_proven")),
            "submitted_message_id": record.get("submitted_message_id", ""),
            "last_observation": _bounded_observation(record.get("last_observation")),
            "submission_attempted": bool(record.get("submission_attempted")),
            "response_extraction_verified": False,
            "error": str(exc),
        }
    send_result = {
        **result,
        "run_id": run_id,
        "task_file": str(bridge.record_path(run_id)),
    }
    recovery_url = _canonical_conversation_url(str(result.get("conversation_url", "")))
    if (
        result.get("status") in {"response_unverified", "timeout_after_submission"}
        and result.get("message_submitted")
        and recovery_url
        and result.get("submitted_message_id")
        and not bridge.cancel_event.is_set()
    ):
        try:
            recovery = read_chatgpt_conversation_data(
                conversation_url=recovery_url,
                timeout_seconds=AUTOMATIC_READ_RECOVERY_TIMEOUT_SECONDS,
                minimum_response_characters=minimum_response_characters,
                expected_response_characters=expected_response_characters,
                expected_response_lines=expected_response_lines,
                expected_response_sha256=expected_response_sha256,
                expected_message_id=result["submitted_message_id"],
                cancel_event=bridge.cancel_event,
            )
        except Exception as exc:
            return {
                **send_result,
                "automatic_read_recovery_attempted": True,
                "automatic_read_recovery_status": "failed",
                "automatic_read_recovery_error": f"{type(exc).__name__}: {exc}",
            }
        if recovery.get("ok"):
            return {
                **recovery,
                "status": "completed_after_read_recovery",
                "message_submitted": True,
                "automatic_read_recovery_attempted": True,
                "automatic_read_recovery_status": "completed",
                "send_run_id": run_id,
                "send_task_file": str(bridge.record_path(run_id)),
                "recovery_run_id": recovery.get("run_id"),
                "recovery_task_file": recovery.get("task_file"),
            }
        return {
            **send_result,
            "automatic_read_recovery_attempted": True,
            "automatic_read_recovery_status": str(recovery.get("status", "failed")),
            "automatic_read_recovery_error": str(recovery.get("error", "")),
            "recovery_run_id": recovery.get("run_id"),
            "recovery_task_file": recovery.get("task_file"),
        }
    return {**send_result, "automatic_read_recovery_attempted": False}


def read_chatgpt_conversation_data(
    conversation_url: str,
    timeout_seconds: int = 120,
    minimum_response_characters: int | None = None,
    expected_response_characters: int | None = None,
    expected_response_lines: int | None = None,
    expected_response_sha256: str | None = None,
    expected_message_id: str | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    if not 1 <= timeout_seconds <= 600:
        raise ValueError("timeout_seconds must be between 1 and 600")
    cancel_event = cancel_event or threading.Event()
    try:
        bridge = _wait_for_bridge_server(timeout_seconds, cancel_event)
    except BridgeBusyError:
        return _busy_result()
    if bridge is None:
        return _cancelled_before_queue_result()
    bridge.cancel_event = cancel_event
    run_id = bridge.start_read(
        conversation_url,
        minimum_response_characters,
        expected_response_characters,
        expected_response_lines,
        expected_response_sha256,
        timeout_seconds,
        expected_message_id,
    )
    if bridge.cancel_event.is_set():
        bridge.cancel_active()
        return {
            **(bridge._result or {}),
            "run_id": run_id,
            "task_file": str(bridge.record_path(run_id)),
        }
    ensure_chrome_running()
    result = bridge.wait(timeout_seconds)
    return {
        **result,
        "run_id": run_id,
        "task_file": str(bridge.record_path(run_id)),
    }
