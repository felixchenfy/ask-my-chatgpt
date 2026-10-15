import base64
import io
import json
import re
import subprocess

import httpx
import pytest
from PIL import Image
from pydantic import BaseModel

from web_content_agent.config import ModelConfig
from web_content_agent.lmstudio import LMStudioClient, ModelServerUnavailable


class Answer(BaseModel):
    status: str
    value: int


class VisionAnswer(BaseModel):
    color: str
    description: str


@pytest.fixture(scope="module")
def client() -> LMStudioClient:
    return LMStudioClient(ModelConfig())


@pytest.mark.lmstudio
def test_model_identifier_and_text(client: LMStudioClient) -> None:
    assert client.actual_model_identifier() == "local-agent-vlm"
    model = client.loaded_model_details()
    assert model["publisher"] == "DavidAU"
    assert model["quantization"] == "Q6_K"
    assert model["type"] == "vlm"
    assert model["loaded_context_length"] == 32768
    inventory = json.loads(
        subprocess.check_output(
            [client._lms_path(), "ps", "--json"], text=True, encoding="utf-8", timeout=20
        )
    )
    assert (
        next(item for item in inventory if item["identifier"] == "local-agent-vlm")["ttlMs"]
        == 300000
    )
    native = next(
        item
        for item in client.native_models()
        if any(
            instance.get("id") == "local-agent-vlm" for instance in item.get("loaded_instances", [])
        )
    )
    instance = native["loaded_instances"][0]
    assert instance["config"]["parallel"] == 1
    assert instance["config"]["flash_attention"] is True
    assert instance["config"]["offload_kv_cache_to_gpu"] is True
    assert "LOCAL_OK" in client.text_smoke()
    assert client.last_context_usage is not None
    assert client.last_context_usage.total_reserved_tokens <= 32768


@pytest.mark.lmstudio
def test_structured_json(client: LMStudioClient) -> None:
    answer = client.structured(
        Answer,
        [{"role": "user", "content": "Return status 'ready' and integer value 42 as JSON."}],
        "integration_json",
        100,
    )
    assert answer.status.lower() == "ready"
    assert answer.value == 42


@pytest.mark.lmstudio
def test_vision_request(client: LMStudioClient) -> None:
    stream = io.BytesIO()
    Image.new("RGB", (256, 256), "red").save(stream, format="PNG")
    encoded = base64.b64encode(stream.getvalue()).decode("ascii")
    answer = client.structured(
        VisionAnswer,
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Identify the dominant color and describe the image."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                ],
            }
        ],
        "integration_vision",
        150,
    )
    assert "red" in answer.color.lower() or "red" in answer.description.lower()


@pytest.mark.lmstudio
def test_tool_call_generation(client: LMStudioClient) -> None:
    payload = {
        "model": "local-agent-vlm",
        "messages": [
            {
                "role": "user",
                "content": "Call report_number with number 17. Do not answer normally.",
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "report_number",
                    "description": "Report the requested integer.",
                    "parameters": {
                        "type": "object",
                        "properties": {"number": {"type": "integer"}},
                        "required": ["number"],
                    },
                },
            }
        ],
        "tool_choice": "required",
        "temperature": 0,
        "max_tokens": 512,
    }
    response = httpx.post(
        f"{client.config.base_url}/chat/completions",
        json=payload,
        timeout=client.config.timeout_seconds,
    )
    response.raise_for_status()
    message = response.json()["choices"][0]["message"]
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        tool_call = tool_calls[0]
        arguments = tool_call["function"]["arguments"]
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        assert tool_call["function"]["name"] == "report_number"
        assert int(arguments["number"]) == 17
        return

    # Bionic 1.0.6's OpenAI-compatible endpoint currently exposes this
    # Qwen3.5 template's valid native call as reasoning XML instead of mapping
    # it to tool_calls. The real Chat/MCP host path is tested separately in UI.
    reasoning = str(message.get("reasoning_content", ""))
    match = re.fullmatch(
        r"<tool_call>\s*<function=report_number>\s*"
        r"<parameter=number>\s*(\d+)\s*</parameter>\s*"
        r"</function>\s*</tool_call>",
        reasoning,
    )
    assert match is not None
    assert int(match.group(1)) == 17


@pytest.mark.lmstudio
def test_finite_timeout() -> None:
    client = LMStudioClient(ModelConfig(timeout_seconds=0.001))
    with pytest.raises(httpx.TimeoutException):
        client.text_smoke()


@pytest.mark.lmstudio
def test_server_unavailable_error() -> None:
    client = LMStudioClient(ModelConfig(base_url="http://127.0.0.1:1/v1"))
    with pytest.raises(ModelServerUnavailable, match="LM Studio unavailable"):
        client.models(timeout=0.1)
