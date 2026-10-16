from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

import httpx
import pytest

from web_content_agent.onepoint3acres.chrome_bridge import BridgeServer

TEST_QUIZ_BRIDGE_TOKEN = "ab" * 32  # matches tests/conftest.py

spec = importlib.util.spec_from_file_location(
    "chrome_input_diagnostics",
    Path(__file__).parents[2] / "scripts/diagnose-chrome-input.py",
)
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


@pytest.fixture
def diagnostic_server(monkeypatch):
    monkeypatch.setattr("web_content_agent.onepoint3acres.chrome_bridge.BRIDGE_PORT", 0)
    bridge = diagnostics.InputDiagnosticBridge()
    server = BridgeServer(bridge)
    server.RequestHandlerClass = diagnostics.InputDiagnosticHandler
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
        yield client, bridge
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_diagnostic_cannot_dispatch_a_forum_run(diagnostic_server):
    client, bridge = diagnostic_server
    headers = {"X-WCA-Token": TEST_QUIZ_BRIDGE_TOKEN}
    for route in ["/run", "/run-daily", "/solve", "/review/prepare", "/review/permit", "/complete"]:
        assert client.post(route, json={}, headers=headers).status_code == 404
    assert bridge.status()["run_id"] is None
    assert bridge.status()["running"] is False


def test_fixture_is_inert_and_has_no_readable_runtime_files(diagnostic_server):
    client, _ = diagnostic_server
    response = client.get("/diagnostics/input-fixture")
    assert response.status_code == 200
    assert "form-action 'none'" in response.headers["content-security-policy"]
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "trustedSubmissions" in response.text
    assert TEST_QUIZ_BRIDGE_TOKEN not in response.text
    assert client.get("/diagnostics/state.json").status_code == 404


def test_diagnostic_claim_is_version_gated_and_not_repeated():
    bridge = diagnostics.InputDiagnosticBridge()
    args = dict(
        observed_version="0.4.1",
        expected_version="0.4.2",
        observed_protocol=1,
        worker_instance_id="test",
    )
    assert bridge.claim_command(**args)["action"] == "reload_extension"
    args["observed_version"] = "0.4.2"
    assert bridge.claim_command(**args)["action"] == "diagnose_input"
    assert bridge.claim_command(**args)["action"] == "idle"
    assert bridge.status()["run_id"] is None


def test_diagnostic_result_needs_auth_and_matching_id(diagnostic_server):
    client, bridge = diagnostic_server
    headers = {"X-WCA-Token": TEST_QUIZ_BRIDGE_TOKEN}
    body = {"diagnostic_id": bridge.diagnostic_id, "ok": True, "cases": []}
    assert client.post("/diagnostics/input-result", json=body).status_code == 403
    assert (
        client.post(
            "/diagnostics/input-result", json={"diagnostic_id": "old"}, headers=headers
        ).status_code
        == 409
    )
    assert not bridge.finished.is_set()
    assert client.post("/diagnostics/input-result", json=body, headers=headers).status_code == 200
    # The handler replies before setting the event; wait instead of racing it.
    assert bridge.finished.wait(2)
    assert client.post("/diagnostics/input-result", json=body, headers=headers).status_code == 409
