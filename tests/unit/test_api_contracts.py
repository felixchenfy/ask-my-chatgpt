"""Contract tests for every public entry point: MCP tools, CLI commands and local HTTP bridges.

These tests are offline. They exercise argument forwarding, validation and refusal paths
with fakes; they never open ChatGPT, a real website, LM Studio or a browser.
"""

from __future__ import annotations

import http.client
import inspect
import json
import secrets
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from typer.testing import CliRunner

import web_content_agent.mcp.server as server
import web_content_agent.mcp.tools as tools
from web_content_agent import cli
from web_content_agent.chatgpt_chrome_bridge import ChatGptBridgeHandler, ChatGptChromeBridge
from web_content_agent.onepoint3acres import chrome_bridge as quiz_bridge_module
from web_content_agent.onepoint3acres.chrome_bridge import BridgeServer, ChromeBridge

EXTENSION_ORIGIN = "chrome-extension://" + "a" * 32


@pytest.fixture
def desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "Desktop"
    path.mkdir()
    monkeypatch.setenv("WCA_DESKTOP_PATH", str(path))
    return path


def make_run(desktop: Path, run_id: str, *, manifest: dict, checkpoint: dict) -> Path:
    collection = desktop / "WebContent" / "sites" / "example" / "demo"
    run = collection / "runs" / run_id
    run.mkdir(parents=True)
    (collection / "source.yaml").write_text(
        "name: demo\nsite_name: example\nhomepage_url: https://example.test/\n", encoding="utf-8"
    )
    (run / "run-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run / "checkpoint.json").write_text(json.dumps(checkpoint), encoding="utf-8")
    return run


# --------------------------------------------------------------------------------------
# MCP tool surface: each tool forwards its arguments to the matching implementation.
# --------------------------------------------------------------------------------------

FORWARDING_CASES = [
    (
        "ask_chatgpt",
        "ask_chatgpt_data",
        {
            "question": "Q",
            "conversation_url": "https://chatgpt.com/c/x",
            "attachment_path": None,
            "timeout_seconds": 60,
            "thinking_mode": "current",
            "minimum_response_characters": 5,
        },
    ),
    (
        "read_chatgpt_conversation",
        "read_chatgpt_conversation_data",
        {
            "conversation_url": "https://chatgpt.com/c/x",
            "timeout_seconds": 30,
            "minimum_response_characters": None,
            "expected_message_id": "m1",
        },
    ),
    ("start_collection", "start_collection_data", {"source_config": "s.yaml"}),
    ("get_run_status", "get_run_status_data", {"run_id": "r"}),
    ("resume_run", "resume_run_data", {"run_id": "r"}),
    ("validate_run", "validate_run_data", {"run_id": "r"}),
    ("list_collections", "list_collections_data", {}),
    ("get_failure", "get_failure_data", {"run_id": "r"}),
    ("create_context_handoff", "create_context_handoff_data", {"run_id": "r"}),
    (
        "create_collection",
        "create_collection_data",
        {"url": "https://e.test", "name": "n", "instructions": "", "strategy": "news"},
    ),
]


@pytest.mark.parametrize(("tool", "target", "arguments"), FORWARDING_CASES)
def test_mcp_tool_forwards_arguments(monkeypatch, tool, target, arguments):
    seen: list[tuple[tuple, dict]] = []

    def fake(*args, **kwargs):
        seen.append((args, kwargs))
        return {"ok": True}

    monkeypatch.setattr(server, target, fake)
    assert getattr(server, tool)(**arguments) == {"ok": True}
    args, kwargs = seen[0]
    forwarded = list(args) + list(kwargs.values())
    for value in arguments.values():
        assert value in forwarded


@pytest.mark.asyncio
async def test_mcp_discover_site_forwards(monkeypatch):
    async def fake(url, instructions):
        return {"url": url, "instructions": instructions}

    monkeypatch.setattr(server, "discover_site_data", fake)
    assert await server.discover_site("https://e.test", "find") == {
        "url": "https://e.test",
        "instructions": "find",
    }


def test_mcp_ask_chatgpt_defaults_are_documented():
    parameters = inspect.signature(server.ask_chatgpt).parameters
    assert parameters["thinking_mode"].default == "prefer_max"
    assert parameters["timeout_seconds"].default == 900
    assert (
        inspect.signature(server.read_chatgpt_conversation).parameters["timeout_seconds"].default
        == 120
    )


# --------------------------------------------------------------------------------------
# Collection tool implementations not covered elsewhere.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["ftp://example.test/", "https://user:pw@example.test/", "not a url", "file:///etc"]
)
@pytest.mark.asyncio
async def test_discover_site_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        await tools.discover_site_data(url)


@pytest.mark.asyncio
async def test_discover_site_respects_robots(monkeypatch):
    monkeypatch.setattr(tools, "_robots_allowed", lambda _url, _agent: False)
    with pytest.raises(PermissionError, match="robots"):
        await tools.discover_site_data("https://example.test/")


@pytest.mark.asyncio
async def test_discover_site_returns_in_scope_candidates(monkeypatch, tmp_path):
    html = """<html><body>
      <a href="/posts/first-article">First article on model serving</a>
      <a href="/posts/second-article">Second article on inference</a>
      <a href="https://elsewhere.test/out">Offsite link</a>
    </body></html>"""

    class FakePage:
        url = "https://example.test/"

        async def content(self):
            return html

        async def title(self):
            return "Example"

    class FakeBrowser:
        def __init__(self, *_args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def goto(self, _url):
            return FakePage()

    monkeypatch.setattr(tools, "_robots_allowed", lambda _url, _agent: True)
    monkeypatch.setattr(tools, "BrowserController", FakeBrowser)
    monkeypatch.setattr(tools, "runtime_root", lambda: tmp_path)
    result = await tools.discover_site_data("https://example.test/", "x" * 2000)
    assert result["status"] == "discovered"
    assert result["page_title"] == "Example"
    assert len(result["instructions"]) == 1000
    urls = [item["url"] for item in result["candidates"]]
    assert all(url.startswith("https://example.test/") for url in urls)
    assert result["candidate_count"] == len(urls) >= 1


def test_robots_parser_disallows_and_fails_open(httpx_mock):
    httpx_mock.add_response(
        url="https://blocked.test/robots.txt", text="User-agent: *\nDisallow: /private\n"
    )
    assert tools._robots_allowed("https://blocked.test/private/page", "wca") is False
    httpx_mock.add_response(url="https://open.test/robots.txt", status_code=404)
    assert tools._robots_allowed("https://open.test/any", "wca") is True


def test_start_collection_spawns_one_worker(monkeypatch):
    calls = []

    def fake_spawn(source_config, **kwargs):
        calls.append((source_config, kwargs))
        return SimpleNamespace(run_id="run-1", pid=42, source_config=source_config)

    monkeypatch.setattr(tools, "spawn_worker", fake_spawn)
    result = tools.start_collection_data("C:/WebContent/sites/a/b/source.yaml")
    assert result["run_id"] == "run-1"
    assert result["status"] == "running"
    assert "Do not poll" in result["next_tool"]
    assert calls == [("C:/WebContent/sites/a/b/source.yaml", {})]


def test_get_run_status_refuses_rapid_polling(desktop, monkeypatch):
    run_id = "poll-run"
    make_run(
        desktop,
        run_id,
        manifest={"run_id": run_id, "status": "success"},
        checkpoint={"current_stage": "done", "articles": {}, "selected_articles": []},
    )
    monkeypatch.setattr(tools, "_LAST_STATUS_POLL", {})
    assert tools.get_run_status_data(run_id)["status"] == "success"
    second = tools.get_run_status_data(run_id)
    assert second["status"] == "poll_too_soon"
    assert second["retry_after_seconds"] > 0


def test_resume_run_paths(desktop, monkeypatch):
    spawned = []
    monkeypatch.setattr(
        tools,
        "spawn_worker",
        lambda source, **kwargs: spawned.append((source, kwargs)) or SimpleNamespace(pid=7),
    )
    statuses = iter(
        [
            {"status": "running", "can_resume": True},
            {"status": "failed", "can_resume": False},
            {"status": "failed", "can_resume": True},
        ]
    )
    monkeypatch.setattr(tools, "get_run_status_data", lambda _run_id: next(statuses))
    run = make_run(
        desktop,
        "resume-run",
        manifest={"run_id": "resume-run", "status": "failed", "source_config": "missing.yaml"},
        checkpoint={"can_resume": True},
    )
    assert tools.resume_run_data("resume-run")["status"] == "already_running"
    with pytest.raises(RuntimeError, match="not resumable"):
        tools.resume_run_data("resume-run")
    assert tools.resume_run_data("resume-run") == {
        "run_id": "resume-run",
        "status": "running",
        "pid": 7,
    }
    assert spawned == [
        (str(run.parents[1] / "source.yaml"), {"run_id": "resume-run", "resume": True})
    ]


def test_validate_run_reports_errors(desktop, monkeypatch):
    make_run(desktop, "validate-run", manifest={}, checkpoint={})
    monkeypatch.setattr(tools, "_validate_manifest", lambda _run: [])
    assert tools.validate_run_data("validate-run")["status"] == "passed"
    monkeypatch.setattr(tools, "_validate_manifest", lambda _run: [f"e{i}" for i in range(30)])
    failed = tools.validate_run_data("validate-run")
    assert failed["status"] == "failed"
    assert failed["error_count"] == 30
    assert len(failed["errors"]) == 20


@pytest.mark.parametrize("run_id", ["../escape", "a/b", "", "x" * 200])
def test_run_tools_reject_unsafe_run_ids(desktop, run_id):
    with pytest.raises(ValueError, match="Invalid run_id"):
        tools.validate_run_data(run_id)


# --------------------------------------------------------------------------------------
# CLI (wca) commands.
# --------------------------------------------------------------------------------------


def test_cli_lists_all_commands():
    result = CliRunner().invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for command in ["doctor", "run", "login", "resume", "status", "validate", "handoff"]:
        assert command in result.output
    assert "new-source" in result.output


def test_cli_new_source_creates_collection_once(desktop):
    runner = CliRunner()
    args = ["new-source", "--url", "https://www.example.test/blog", "--name", "My Blog"]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    source = desktop / "WebContent/sites/example-test/my-blog/source.yaml"
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    assert data["homepage_url"] == "https://www.example.test/blog"
    assert data["allowed_domains"] == ["example.test"]
    assert runner.invoke(cli.app, args).exit_code != 0  # never overwrites


def test_cli_new_source_rejects_non_http_url(desktop):
    result = CliRunner().invoke(cli.app, ["new-source", "--url", "ftp://x.test", "--name", "n"])
    assert result.exit_code != 0


def test_cli_status_and_validate(desktop):
    make_run(
        desktop,
        "cli-run",
        manifest={"run_id": "cli-run", "status": "failed"},
        checkpoint={"current_stage": "article_download", "can_resume": True},
    )
    status = CliRunner().invoke(cli.app, ["status", "cli-run"])
    assert status.exit_code == 0
    assert json.loads(status.output)["manifest_status"] == "failed"
    validate = CliRunner().invoke(cli.app, ["validate", "missing-run"])
    assert validate.exit_code != 0


# --------------------------------------------------------------------------------------
# ChatGPT bridge loopback HTTP API (used only by the paired Chrome Companion).
# --------------------------------------------------------------------------------------


@pytest.fixture
def chatgpt_http(tmp_path):
    bridge = ChatGptChromeBridge(tmp_path)
    http_server = ThreadingHTTPServer(("127.0.0.1", 0), ChatGptBridgeHandler)
    http_server.bridge = bridge
    http_server.pairing_token = secrets.token_hex(32)
    thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    thread.start()
    try:
        yield bridge, http_server
    finally:
        http_server.shutdown()
        http_server.server_close()


def _request(port, method, path, *, token=None, origin=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-WCA-Token"] = token
    if origin is not None:
        headers["Origin"] = origin
    conn.request(method, path, body=json.dumps(body or {}), headers=headers)
    response = conn.getresponse()
    payload = response.read()
    conn.close()
    return response.status, payload


@pytest.mark.parametrize(
    "route", ["/before-submit", "/observation", "/event", "/submitted", "/complete"]
)
def test_chatgpt_bridge_post_routes_require_pairing(chatgpt_http, route):
    _bridge, http_server = chatgpt_http
    status, _ = _request(http_server.server_port, "POST", route, body={"run_id": "x"})
    assert status == 403


def test_chatgpt_bridge_unknown_routes_are_404(chatgpt_http):
    _bridge, http_server = chatgpt_http
    token = http_server.pairing_token
    assert _request(http_server.server_port, "GET", "/nope", token=token)[0] == 404
    assert _request(http_server.server_port, "POST", "/nope", token=token)[0] == 404


def test_chatgpt_bridge_unpaired_command_only_advertises_reload(chatgpt_http):
    bridge, http_server = chatgpt_http
    run_id = bridge.start_message("Private question must not leak")
    status, payload = _request(http_server.server_port, "GET", "/command")
    assert status == 200
    decoded = json.loads(payload)
    assert decoded["action"] == "reload_extension"
    assert "Private question" not in payload.decode("utf-8")
    assert bridge.claim_command()["run_id"] == run_id  # still unclaimed


def test_chatgpt_bridge_event_route_accepts_paired_extension(chatgpt_http):
    _bridge, http_server = chatgpt_http
    status, payload = _request(
        http_server.server_port,
        "POST",
        "/event",
        token=http_server.pairing_token,
        origin=EXTENSION_ORIGIN,
        body={"run_id": "unknown", "message": "hello"},
    )
    assert status in {200, 400}
    assert b"forbidden" not in payload


# --------------------------------------------------------------------------------------
# Optional 1point3acres desktop-app loopback HTTP API.
# --------------------------------------------------------------------------------------


@pytest.fixture
def quiz_http(monkeypatch):
    monkeypatch.setattr(quiz_bridge_module, "BRIDGE_PORT", 0)
    quiz_server = BridgeServer(
        ChromeBridge(lambda _message: None),
        request_run=lambda: True,
        request_close=lambda: True,
        request_daily_run=lambda: False,
        expected_extension_version=lambda: "0.0.1",
    )
    thread = threading.Thread(target=quiz_server.serve_forever, daemon=True)
    thread.start()
    try:
        yield quiz_server
    finally:
        quiz_server.shutdown()
        quiz_server.server_close()


def test_quiz_bridge_token_is_per_install_not_constant(quiz_http):
    assert not hasattr(quiz_bridge_module, "BRIDGE_TOKEN")
    assert quiz_http.token == quiz_bridge_module.bridge_token()


@pytest.mark.parametrize("route", ["/run", "/close", "/run-daily", "/simulate", "/complete"])
def test_quiz_bridge_rejects_web_page_origins_even_with_token(quiz_http, route):
    status, _ = _request(
        quiz_http.server_port,
        "POST",
        route,
        token=quiz_http.token,
        origin="https://attacker.example",
    )
    assert status == 403


def test_quiz_bridge_cors_is_limited_to_extension(quiz_http):
    port = quiz_http.server_port
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
        foreign = client.options("/run", headers={"Origin": "https://attacker.example"})
        assert foreign.status_code == 403
        assert "access-control-allow-origin" not in foreign.headers
        allowed = client.options("/run", headers={"Origin": EXTENSION_ORIGIN})
        assert allowed.status_code == 204
        assert allowed.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
        assert client.get("/status").status_code == 403
        ok = client.get(
            "/status?log_limit=0",
            headers={"X-WCA-Token": quiz_http.token, "Origin": EXTENSION_ORIGIN},
        )
        assert ok.status_code == 200
        assert ok.headers["access-control-allow-origin"] == EXTENSION_ORIGIN


def test_quiz_bridge_control_routes(quiz_http):
    port = quiz_http.server_port
    token = quiz_http.token
    assert _request(port, "POST", "/run", token=token)[0] == 202
    assert _request(port, "POST", "/close", token=token)[0] == 202
    assert _request(port, "POST", "/run-daily", token=token)[0] == 409
    assert _request(port, "GET", "/status?log_limit=x", token=token)[0] == 400
    assert _request(port, "GET", "/unknown", token=token)[0] == 404


# --------------------------------------------------------------------------------------
# Live-check script safety: it must refuse to send without explicit confirmation.
# --------------------------------------------------------------------------------------


def test_live_check_script_requires_explicit_confirmation():
    import subprocess
    import sys

    script = Path(__file__).parents[2] / "scripts/chatgpt-live-check.py"
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 2
    assert "--confirm-send" in result.stderr
