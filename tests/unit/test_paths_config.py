import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from web_content_agent.cli import _localhost_listener
from web_content_agent.config import BrowserConfig, load_source_config
from web_content_agent.paths import get_desktop_path


def test_desktop_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WCA_DESKTOP_PATH", str(tmp_path))
    assert get_desktop_path() == tmp_path.resolve()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only: Windows Desktop folder")
def test_windows_desktop_resolves_to_existing_path() -> None:
    assert get_desktop_path().is_dir()


def test_config_read_and_validate(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    source.write_text(
        "name: demo\nsite_name: fixture\nhomepage_url: https://fixture.test/\n"
        "allowed_domains: [fixture.test]\nmax_articles: 3\n",
        encoding="utf-8",
    )
    config = load_source_config(source)
    assert config.max_articles == 3
    assert config.resolved_collection_dir() == tmp_path.resolve()
    assert config.model.base_url == "http://127.0.0.1:1234/v1"
    assert config.model.identifier == "local-agent-vlm"
    assert config.model.quant == "Q6_K"
    assert config.model.model_key.startswith("DavidAU/Qwen3.5-9B-The-Defiant-Fable-")
    assert config.model.model_key.endswith("NEO-MAX-Q6_K.gguf")


def test_config_rejects_cloud_model_endpoint(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    source.write_text(
        "name: demo\nsite_name: fixture\nhomepage_url: https://fixture.test/\n"
        "allowed_domains: [fixture.test]\nmodel:\n  base_url: https://api.openai.com/v1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="localhost"):
        load_source_config(source)


def test_course_discovery_url_must_stay_in_allowed_domains(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    source.write_text(
        "name: demo\nsite_name: fixture\n"
        "homepage_url: https://fixture.test/courses/demo\n"
        "discovery_url: https://outside.test/courses/demo/lesson\n"
        "allowed_domains: [fixture.test]\nstrategy: course\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="discovery host"):
        load_source_config(source)


def test_browser_delay_range_is_validated() -> None:
    with pytest.raises(ValueError, match="max_navigation_delay_seconds"):
        BrowserConfig(min_navigation_delay_seconds=10, max_navigation_delay_seconds=2)


def test_polite_daily_limit_and_five_point_three_minute_delay_are_supported() -> None:
    config = BrowserConfig(
        min_navigation_delay_seconds=38,
        max_navigation_delay_seconds=318,
        daily_page_limit=500,
    )
    assert config.daily_page_limit == 500
    assert config.max_navigation_delay_seconds == 318


def test_localhost_listener_ignores_foreign_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    output = "TCP  127.0.0.1:1234  0.0.0.0:0  LISTENING  42\n"
    monkeypatch.setattr(
        "web_content_agent.cli.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0, output, ""),
    )
    safe, listeners = _localhost_listener(1234)
    assert safe is True
    assert listeners == ["TCP  127.0.0.1:1234  0.0.0.0:0  LISTENING  42"]
