"""Real loopback ownership and admission regressions; no Chrome or submissions."""

import errno
import socket
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

import web_content_agent.chatgpt_chrome_bridge as module


@pytest.fixture
def local_bridge(tmp_path, monkeypatch):
    bridge = module.ChatGptChromeBridge(tmp_path)
    monkeypatch.setattr(module, "BRIDGE_PORT", 0)
    monkeypatch.setattr(module, "load_pairing_token", lambda: "offline-fixture")
    monkeypatch.setattr(module, "_bridge", bridge)
    monkeypatch.setattr(module, "_server", None)
    yield bridge
    if module._server is not None:
        module._server.shutdown()
        module._server.server_close()


def test_only_one_live_listener_can_own_the_bridge_port(local_bridge, monkeypatch):
    with module.ChatGptBridgeServer(local_bridge) as owner:
        monkeypatch.setattr(module, "BRIDGE_PORT", owner.server_port)
        with pytest.raises(module.BridgeBusyError):
            module.ensure_bridge_server()
        assert module._server is None
        assert local_bridge._command is None
        assert not list(local_bridge.state_root.glob("*.json"))
        # A legacy HTTPServer with SO_REUSEADDR cannot steal the new listener.
        with pytest.raises(OSError):
            with ThreadingHTTPServer(owner.server_address, module.ChatGptBridgeHandler):
                pytest.fail("legacy listener stole an occupied bridge port")
    # Port acquisition recovers without touching the previous owner or Chrome.
    assert module.ensure_bridge_server() is local_bridge


def test_new_bridge_refuses_a_legacy_listener(local_bridge, monkeypatch):
    with ThreadingHTTPServer(("127.0.0.1", 0), module.ChatGptBridgeHandler) as legacy:
        monkeypatch.setattr(module, "BRIDGE_PORT", legacy.server_port)
        with pytest.raises(module.BridgeBusyError):
            module.ensure_bridge_server()
        assert module._server is None


def test_competing_adapter_process_cannot_bind(local_bridge, monkeypatch):
    with module.ChatGptBridgeServer(local_bridge) as owner:
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys\n"
                "import web_content_agent.chatgpt_chrome_bridge as m\n"
                "m.BRIDGE_PORT = int(sys.argv[1])\n"
                "m.load_pairing_token = lambda: 'offline-fixture'\n"
                "try:\n"
                "    m.ensure_bridge_server()\n"
                "except m.BridgeBusyError:\n"
                "    assert m._server is None and m._bridge._command is None\n"
                "    print('busy_without_queueing')\n"
                "else:\n"
                "    raise SystemExit('second process bound occupied port')\n",
                str(owner.server_port),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert child.returncode == 0, child.stderr
        assert child.stdout.strip() == "busy_without_queueing"


@pytest.mark.skipif(not hasattr(socket, "SO_EXCLUSIVEADDRUSE"), reason="Windows socket option")
def test_windows_listener_uses_exclusive_ownership(local_bridge):
    with module.ChatGptBridgeServer(local_bridge) as owner:
        assert owner.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE) == 1
        assert owner.socket.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0


@pytest.mark.parametrize("read_only", [False, True])
def test_waiter_admits_once_after_owner_releases_without_browser_work(
    local_bridge, monkeypatch, read_only
):
    owner = module.ChatGptBridgeServer(local_bridge)
    monkeypatch.setattr(module, "BRIDGE_PORT", owner.server_port)
    attempted = threading.Event()
    original = module.ensure_bridge_server
    attempts = []

    def acquire():
        attempts.append(True)
        try:
            return original()
        except module.BridgeBusyError:
            attempted.set()
            raise

    monkeypatch.setattr(module, "ensure_bridge_server", acquire)

    def release():
        assert attempted.wait(5)
        assert local_bridge._command is None
        assert not list(local_bridge.state_root.glob("*.json"))
        owner.server_close()

    releaser = threading.Thread(target=release)
    releaser.start()
    starts = []

    def start(*args):
        starts.append(args)
        # Stop this fixture after admission, before any real browser work.
        raise ValueError("admitted-fixture")

    monkeypatch.setattr(local_bridge, "start_read" if read_only else "start_message", start)
    try:
        with pytest.raises(ValueError, match="admitted-fixture"):
            if read_only:
                module.read_chatgpt_conversation_data(
                    "https://chatgpt.com/c/fixture",
                    expected_message_id="original",
                    timeout_seconds=5,
                )
            else:
                module.ask_chatgpt_data(
                    "Exact question",
                    session_key="session",
                    idempotency_key="operation",
                    timeout_seconds=5,
                )
        assert len(attempts) >= 2
        assert len(starts) == 1
        if read_only:
            assert starts[0][-1] == "original"
        else:
            assert starts[0][0] == "Exact question"
            assert starts[0][4:8] == ("session", "operation", False, False)
    finally:
        owner.server_close()
        releaser.join(5)


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("already_cancelled", [False, True])
def test_cancel_waiter_never_queues_or_cancels_owner(
    local_bridge, monkeypatch, read_only, already_cancelled
):
    cancelled = threading.Event()
    if already_cancelled:
        cancelled.set()
    with module.ChatGptBridgeServer(local_bridge) as owner:
        monkeypatch.setattr(module, "BRIDGE_PORT", owner.server_port)
        original = module.ensure_bridge_server

        def acquire():
            try:
                return original()
            finally:
                cancelled.set()

        monkeypatch.setattr(module, "ensure_bridge_server", acquire)
        result = (
            module.read_chatgpt_conversation_data(
                "https://chatgpt.com/c/fixture", cancel_event=cancelled
            )
            if read_only
            else module.ask_chatgpt_data("Unsent", cancel_event=cancelled)
        )
        assert result["status"] == "cancelled_before_submission"
        assert result["zero_submission_proven"] is True
        assert result["message_submitted"] is False
        assert module._server is None
        assert not list(local_bridge.state_root.glob("*.json"))
        assert not local_bridge.cancel_event.is_set()
        assert owner.fileno() != -1


def test_unrelated_bind_errors_are_not_retried(local_bridge, monkeypatch):
    def fail(_bridge):
        raise OSError(errno.EINVAL, "bad socket fixture")

    monkeypatch.setattr(module, "ChatGptBridgeServer", fail)
    with pytest.raises(OSError, match="bad socket fixture"):
        module._wait_for_bridge_server(900, threading.Event())
