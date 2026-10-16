#!/usr/bin/env python3
"""Send messages to / read replies from the user's signed-in ChatGPT via WebContentAgent.

Thin CLI over the production adapter used by the Codex `chatgpt_pdf_review` plugin:
  integrations/codex-plugin/scripts/ask-chatgpt-adapter.py  ->  WebContentAgent bridge
  ->  Chrome Companion  ->  the real chatgpt.com tab.

Paths: WCA_PROJECT_ROOT (default: the checkout containing this skill),
CHATGPT_BRIDGE_SOURCE (plugin directory holding scripts/ask-chatgpt-adapter.py; default:
../../codex-plugin next to this skill, else <WCA root>/integrations/codex-plugin) and
WCA_PYTHON_EXECUTABLE (default: <WCA root>/.venv).

Every send is single-shot and idempotent: the idempotency key defaults to a hash of
(session key, message), so re-running the same command can never post twice; the
bridge returns `duplicate_blocked` (or the cached completed result) instead.

Usage examples (any Python 3.10+ works; the adapter itself runs in the WCA venv):
  python gpt.py ask --to https://chatgpt.com/c/<id> --message "hello"
  python gpt.py ask --to design-review --file prompt.md
  python gpt.py ask --new --name my-topic --message "Start a discussion about ..."
  python gpt.py read --to design-review --expected-message-id <id>
  python gpt.py list
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

HOME = Path(os.environ.get("USERPROFILE") or Path.home())
# integrations/claude-skill/chatgpt-bridge inside a WebContentAgent checkout. Symlinks and
# junctions are resolved, so a linked ~/.claude/skills/chatgpt-bridge still finds the repo.
SKILL_DIR = Path(__file__).resolve().parent.parent
BRIDGE_MODULE = Path("src/web_content_agent/chatgpt_chrome_bridge.py")
ADAPTER_RELATIVE = Path("scripts/ask-chatgpt-adapter.py")


def _default_wca_root() -> Path | None:
    """WCA_PROJECT_ROOT, else the WebContentAgent repository that contains this skill."""
    if os.environ.get("WCA_PROJECT_ROOT"):
        return Path(os.environ["WCA_PROJECT_ROOT"]).expanduser().resolve()
    if len(SKILL_DIR.parents) > 2 and (SKILL_DIR.parents[2] / BRIDGE_MODULE).is_file():
        return SKILL_DIR.parents[2]
    return None


def _default_adapter(wca_root: Path | None) -> Path | None:
    """CHATGPT_BRIDGE_SOURCE, else the sibling codex-plugin, else <WCA root>/integrations."""
    if os.environ.get("CHATGPT_BRIDGE_SOURCE"):
        return Path(os.environ["CHATGPT_BRIDGE_SOURCE"]).expanduser() / ADAPTER_RELATIVE
    in_repo = SKILL_DIR.parent.parent / "codex-plugin" / ADAPTER_RELATIVE
    if in_repo.is_file():
        return in_repo
    if wca_root is not None:
        return wca_root / "integrations" / "codex-plugin" / ADAPTER_RELATIVE
    return None


def _default_python(wca_root: Path | None) -> Path | None:
    if os.environ.get("WCA_PYTHON_EXECUTABLE"):
        return Path(os.environ["WCA_PYTHON_EXECUTABLE"]).expanduser()
    if wca_root is None:
        return None
    return wca_root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")


WCA_ROOT = _default_wca_root()
ADAPTER = _default_adapter(WCA_ROOT)
WCA_PYTHON = _default_python(WCA_ROOT)

STATE_DIR = HOME / ".claude/chatgpt-bridge"
REGISTRY = STATE_DIR / "conversations.json"
RECEIPTS = STATE_DIR / "receipts"
CODEX_REGISTRY = HOME / ".codex/chatgpt-pdf-review/conversations.json"  # read-only fallback

KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CONV_RE = re.compile(r"^https://chatgpt\.com/(?:g/g-p-[^/]+/)?c/([0-9a-fA-F-]{8,})")

# Statuses for which the message may already be posted: never resend.
DO_NOT_RESEND = {
    "submission_unconfirmed",
    "timeout_after_submission",
    "cancelled_submission_unknown",
    "duplicate_blocked",
    "throttled_cooldown",
    "idempotency_conflict",
}


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("conversations"), dict):
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:  # corrupt registry: never overwrite it silently
        raise SystemExit(f"Cannot parse registry {path}: {exc}") from exc
    return {"version": 1, "conversations": {}}


def _save_registry(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(REGISTRY)


def _resolve(to: str | None) -> tuple[str | None, str | None]:
    """Return (alias, url) for an alias or URL. Alias may be None."""
    if not to:
        return None, None
    if to.startswith("https://"):
        if "/c/WEB:" in to:
            return None, to  # provisional route, usable once; never saved
        if not CONV_RE.match(to):
            raise SystemExit(f"Not a ChatGPT conversation URL: {to}")
        mine = _load(REGISTRY)["conversations"]
        conv_id = CONV_RE.match(to).group(1)
        for alias, entry in mine.items():
            m = CONV_RE.match(entry.get("url", ""))
            if m and m.group(1) == conv_id:
                return alias, to
        return None, to
    for path in (REGISTRY, CODEX_REGISTRY):
        entry = _load(path)["conversations"].get(to)
        if entry and entry.get("url"):
            return to, entry["url"]
    raise SystemExit(f"Unknown conversation alias '{to}'. Run `gpt.py list` or pass a URL.")


def _session_key(explicit: str | None, alias: str | None, url: str | None) -> str:
    if explicit:
        key = explicit
    elif alias:
        key = alias
    elif url and CONV_RE.match(url):
        key = "claude-" + CONV_RE.match(url).group(1)[:8]
    else:
        key = "claude-new-" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    if not KEY_RE.match(key):
        key = re.sub(r"[^A-Za-z0-9._-]+", "-", key).strip("-.")[:120] or "claude-session"
    return key


def _run_adapter(request: dict) -> dict:
    if WCA_ROOT is None or not (WCA_ROOT / BRIDGE_MODULE).is_file():
        raise SystemExit(
            f"WebContentAgent checkout not found ({WCA_ROOT or 'not configured'}). Set "
            "WCA_PROJECT_ROOT to your checkout, or run this skill from (or linked to) "
            "<checkout>/integrations/claude-skill/chatgpt-bridge."
        )
    for path, what in ((WCA_PYTHON, "WebContentAgent venv python"), (ADAPTER, "bridge adapter")):
        if path is None or not path.exists():
            raise SystemExit(f"Missing {what}: {path}")
    env = {
        **os.environ,
        "WCA_PROJECT_ROOT": str(WCA_ROOT),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.Popen(
        [str(WCA_PYTHON), str(ADAPTER)],
        cwd=str(WCA_ROOT),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    payload = json.dumps({**request, "_cancel_protocol": 1}, ensure_ascii=False) + "\n"
    # The adapter treats stdin EOF as a cancel request, so stdin must stay open until it exits.
    chunks: dict[str, list[str]] = {"out": [], "err": []}
    readers = [
        threading.Thread(target=lambda s=s, k=k: chunks[k].append(s.read()), daemon=True)
        for s, k in ((proc.stdout, "out"), (proc.stderr, "err"))
    ]
    for t in readers:
        t.start()
    proc.stdin.write(payload)
    proc.stdin.flush()
    try:
        while proc.poll() is None:
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    except KeyboardInterrupt:
        # Cooperative cancel: revokes an ungranted submit permit. Killing alone is unsafe.
        try:
            proc.stdin.write(json.dumps({"action": "cancel"}) + "\n")
            proc.stdin.flush()
        except Exception:
            pass
        proc.wait(timeout=60)
    for t in readers:
        t.join(timeout=10)
    try:
        proc.stdin.close()
    except Exception:
        pass
    stdout, stderr = "".join(chunks["out"]), "".join(chunks["err"])
    if proc.returncode != 0:
        return {"ok": False, "status": "adapter_error", "error": (stderr or "").strip()[-4000:]}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"ok": False, "status": "adapter_invalid_json", "error": stdout[-4000:]}


def _write_receipt(kind: str, session_key: str, request: dict, result: dict) -> Path:
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RECEIPTS / f"{stamp}-{kind}-{session_key}.json"
    path.write_text(
        json.dumps({"request": request, "result": result}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _update_registry(alias: str | None, result: dict, title: str | None) -> str | None:
    url = result.get("conversation_url") or ""
    if not alias or not CONV_RE.match(url) or "/c/WEB:" in url:
        return None
    data = _load(REGISTRY)
    entry = data["conversations"].setdefault(alias, {})
    if entry.get("url") and CONV_RE.match(entry["url"]).group(1) != CONV_RE.match(url).group(1):
        print(
            f"[gpt] registry alias '{alias}' already points to {entry['url']}; not overwritten.",
            file=sys.stderr,
        )
        return None
    entry["url"] = url
    if title:
        entry["title"] = title
    entry["last_status"] = result.get("status")
    entry["last_submitted_message_id"] = result.get("submitted_message_id") or entry.get(
        "last_submitted_message_id"
    )
    entry["updated_at"] = _dt.datetime.now(_dt.timezone.utc).strftime(  # noqa: UP017 (3.10)
        "%Y-%m-%dT%H:%M:%SZ"
    )
    _save_registry(data)
    return alias


def _report(result: dict, receipt: Path, as_json: bool) -> int:
    verified = bool(result.get("response_extraction_verified"))
    status = result.get("status")
    if as_json:
        print(json.dumps({**result, "receipt": str(receipt)}, ensure_ascii=False, indent=2))
    elif verified and result.get("response_text") is not None:
        print(result["response_text"])
    summary = {
        "status": status,
        "verified": verified,
        "conversation_url": result.get("conversation_url"),
        "submitted_message_id": result.get("submitted_message_id"),
        "message_submitted": result.get("message_submitted"),
        "chars": result.get("response_character_count"),
        "receipt": str(receipt),
    }
    if result.get("error"):
        summary["error"] = result["error"]
    print("[gpt] " + json.dumps(summary, ensure_ascii=False), file=sys.stderr)
    if not verified:
        if (
            status in DO_NOT_RESEND
            or result.get("message_submitted")
            or result.get("submission_attempted")
        ):
            print(
                "[gpt] The message may already be posted. DO NOT resend or change the key. "
                "If a submitted_message_id exists and automatic_read_recovery_attempted is false, "
                "use `gpt.py read --expected-message-id <id>` once.",
                file=sys.stderr,
            )
        elif result.get("zero_submission_proven"):
            print(
                "[gpt] Zero submission proven; the same command (same key) may be retried "
                "after fixing the cause.",
                file=sys.stderr,
            )
        return 2
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    if args.message is not None:
        message = args.message
    elif args.file:
        message = Path(args.file).read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        message = sys.stdin.read()
    else:
        raise SystemExit("Provide --message, --file, or pipe the message on stdin.")
    if not message.strip():
        raise SystemExit("Refusing to send an empty message.")
    if args.new and args.to:
        raise SystemExit("--new cannot be combined with --to.")
    alias, url = _resolve(args.to)
    alias = args.name or alias
    if args.name and not KEY_RE.match(args.name):
        raise SystemExit("--name must match [A-Za-z0-9][A-Za-z0-9._-]*")
    session_key = _session_key(args.session_key, alias, url)
    key = (
        args.key
        or "claude-"
        + hashlib.sha256(f"{session_key}\n{message}\n{args.attach or ''}".encode()).hexdigest()[:24]
    )
    request = {
        "question": message,
        "session_key": session_key,
        "idempotency_key": key,
        "timeout_seconds": args.timeout,
        "thinking_mode": args.thinking,
    }
    if url:
        request["conversation_url"] = url
    if args.new:
        request["new_conversation"] = True
    if args.attach:
        request["attachment_path"] = str(Path(args.attach).resolve())
    if args.min_chars:
        request["minimum_response_characters"] = args.min_chars
    print(
        f"[gpt] sending to {url or '(new conversation)'} session={session_key} key={key}",
        file=sys.stderr,
    )
    result = _run_adapter(request)
    receipt = _write_receipt("ask", session_key, request, result)
    saved = _update_registry(alias, result, args.title)
    if saved:
        print(
            f"[gpt] registry alias '{saved}' -> {result.get('conversation_url')}", file=sys.stderr
        )
    return _report(result, receipt, args.json)


def cmd_read(args: argparse.Namespace) -> int:
    alias, url = _resolve(args.to)
    if not url:
        raise SystemExit("read needs --to <alias|url>.")
    request = {
        "action": "read_chatgpt_conversation",
        "conversation_url": url,
        "timeout_seconds": args.timeout,
    }
    if args.expected_message_id:
        request["expected_message_id"] = args.expected_message_id
    if args.min_chars:
        request["minimum_response_characters"] = args.min_chars
    session_key = _session_key(None, alias, url)
    result = _run_adapter(request)
    receipt = _write_receipt("read", session_key, request, result)
    return _report(result, receipt, args.json)


def cmd_list(_: argparse.Namespace) -> int:
    for label, path in (("claude", REGISTRY), ("codex (read-only)", CODEX_REGISTRY)):
        convs = _load(path)["conversations"]
        print(f"# {label}: {path}")
        for alias, entry in sorted(convs.items()):
            print(f"  {alias:45s} {entry.get('url', '')}  {entry.get('last_status', '')}")
    return 0


def main() -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    ask = sub.add_parser("ask", help="send one message and wait for the verified reply")
    ask.add_argument("--to", help="conversation alias or https://chatgpt.com/c/... URL")
    ask.add_argument("--new", action="store_true", help="start a new conversation")
    ask.add_argument("--name", help="registry alias to save this conversation under")
    ask.add_argument("--title", help="optional human title stored in the registry")
    src = ask.add_mutually_exclusive_group()
    src.add_argument("--message", "-m")
    src.add_argument("--file", "-f", help="UTF-8 file containing the message")
    ask.add_argument("--attach", help="optional PDF to attach (must be under allowed roots)")
    ask.add_argument("--session-key")
    ask.add_argument("--key", help="explicit idempotency key (default: hash of session+message)")
    ask.add_argument("--thinking", default="current", choices=["current", "prefer_max", "max"])
    ask.add_argument("--min-chars", type=int)
    ask.add_argument("--timeout", type=int, default=900, help="seconds, 1..3600")
    ask.add_argument("--json", action="store_true", help="print the full result JSON to stdout")
    ask.set_defaults(func=cmd_ask)

    read = sub.add_parser("read", help="read-only recovery of an existing reply (never sends)")
    read.add_argument("--to", required=True)
    read.add_argument("--expected-message-id", help="bind to the submitted user message")
    read.add_argument("--min-chars", type=int)
    read.add_argument("--timeout", type=int, default=120, help="seconds, 1..600")
    read.add_argument("--json", action="store_true")
    read.set_defaults(func=cmd_read)

    lst = sub.add_parser("list", help="list known conversation aliases")
    lst.set_defaults(func=cmd_list)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
