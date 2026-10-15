"""Project paths are aliases of one conversation, not arbitrary GPT destinations."""

import pytest

import web_content_agent.chatgpt_chrome_bridge as bridge_module


def test_project_route_uses_canonical_identity_for_input_and_state(tmp_path):
    project = "https://chatgpt.com/g/g-p-example/c/expected"
    canonical = "https://chatgpt.com/c/expected"
    assert bridge_module._validate_conversation_url(project) == canonical
    assert bridge_module._canonical_conversation_url(project) == canonical
    bridge = bridge_module.ChatGptChromeBridge(tmp_path)
    run = bridge.start_message("Synthetic question", project, session_key="project-test")
    assert run
    assert bridge.claim_command()["conversation_url"] == canonical


@pytest.mark.parametrize(
    "url",
    [
        "https://chatgpt.com/g/g-custom/c/expected",
        "https://chatgpt.com/g/g-p-example",
        "https://chatgpt.com/g/g-p-example/c/expected/extra",
        "https://chatgpt.com/g/g-p-example/c/WEB:expected",
        "https://chatgpt.com:444/g/g-p-example/c/expected",
        "https://name@chatgpt.com/g/g-p-example/c/expected",
        "https://chatgpt.com/g/g-p-example/c/expected?temporary-chat=true",
        "https://chatgpt.com/g/g-p-example/c/expected#fragment",
        "https://example.com/g/g-p-example/c/expected",
    ],
)
def test_project_url_validation_retains_narrow_boundary(url):
    with pytest.raises(ValueError):
        bridge_module._validate_conversation_url(url)
