import pytest
from pydantic import BaseModel, ValidationError

from web_content_agent.article_selection import (
    ArticleSelection,
    SelectedArticle,
    canonicalize_url,
    safe_image_decision,
    safe_slug,
)
from web_content_agent.config import ModelConfig
from web_content_agent.discovery import collect_course_candidates
from web_content_agent.lmstudio import LMStudioClient, extract_json_object
from web_content_agent.worker import _article_output_path, _staging_key


class RetryAnswer(BaseModel):
    status: str


def test_url_canonicalization() -> None:
    assert (
        canonicalize_url("HTTPS://Example.COM:443/news//story/?utm_source=x&b=2&a=1#section")
        == "https://example.com/news/story?a=1&b=2"
    )


def test_slug_cleanup() -> None:
    assert safe_slug("  A Windows: Story / with * marks?  ") == "a-windows-story-with-marks"
    assert len(safe_slug("x" * 200)) <= 72
    assert _staging_key("006-a-very-long-course-lesson-title") == "006"


def test_article_selection_schema_supports_variable_contiguous_items() -> None:
    one = ArticleSelection.model_validate(
        {
            "articles": [
                {
                    "rank": 1,
                    "title": "Only story",
                    "url": "https://x.test/1",
                    "reason": "Normal editorial story",
                }
            ]
        }
    )
    assert len(one.articles) == 1
    with pytest.raises(ValidationError):
        ArticleSelection.model_validate(
            {
                "articles": [
                    {
                        "rank": 1,
                        "title": "Only story",
                        "url": "https://x.test/1",
                        "reason": "Normal editorial story",
                    },
                    {
                        "rank": 3,
                        "title": "Skipped rank",
                        "url": "https://x.test/3",
                        "reason": "Non-contiguous rank must fail",
                    },
                ]
            }
        )


def test_course_candidates_preserve_order_and_scope() -> None:
    html = """
    <main><section class="curriculum">
      <a href="/courses/demo/lesson-one">Lesson One</a>
      <a href="/courses/demo/module/two">Module Two</a>
      <a href="/courses/other/lesson">Other Course</a>
      <a href="https://outside.test/courses/demo/x">Outside</a>
      <a href="/courses/demo/lesson-one?utm_source=x">Duplicate</a>
    </section></main>
    """
    candidates = collect_course_candidates(html, "https://x.test/courses/demo", ["x.test"])
    assert [item.title for item in candidates] == ["Lesson One", "Module Two"]


def test_course_candidates_allow_short_technical_titles() -> None:
    html = '<main><a href="/courses/demo/cdn">CDN</a></main>'
    candidates = collect_course_candidates(
        html, "https://fixture.test/courses/demo", ["fixture.test"]
    )
    assert [(item.title, item.url) for item in candidates] == [
        ("CDN", "https://fixture.test/courses/demo/cdn")
    ]


def test_numbered_course_curriculum_preserves_section_hierarchy() -> None:
    html = """
    <nav><div class="course-group">
      <div><h5><span>36.</span><span>Design WhatsApp</span></h5></div>
      <a href="/courses/demo/lta/system-design-whatsapp">System Design: WhatsApp</a>
      <a href="/courses/demo/lta/requirements-whatsapp">Requirements of WhatsApp’s Design</a>
    </div></nav>
    """
    candidates = collect_course_candidates(
        html, "https://fixture.test/courses/demo", ["fixture.test"]
    )
    assert [
        (
            item.section_number,
            item.section_title,
            item.lesson_number,
            item.title,
            item.url,
        )
        for item in candidates
    ] == [
        (
            36,
            "Design WhatsApp",
            1,
            "System Design: WhatsApp",
            "https://fixture.test/courses/demo/lta/system-design-whatsapp",
        ),
        (
            36,
            "Design WhatsApp",
            2,
            "Requirements of WhatsApp’s Design",
            "https://fixture.test/courses/demo/lta/requirements-whatsapp",
        ),
    ]


def test_course_output_path_uses_readable_section_and_lesson_folders() -> None:
    article = SelectedArticle(
        rank=145,
        title="System Design: WhatsApp",
        url="https://fixture.test/courses/demo/lta/system-design-whatsapp",
        reason="Visible lesson in the numbered course curriculum",
        section_number=36,
        section_title="Design WhatsApp",
        lesson_number=1,
    )
    assert _article_output_path(article).as_posix() == (
        "36_Design_WhatsApp/36.1_System_Design_WhatsApp"
    )


def test_extract_model_json_fenced() -> None:
    assert extract_json_object('```json\n{"status":"ok"}\n```') == {"status": "ok"}


def test_structured_retries_empty_model_output(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LMStudioClient(ModelConfig(max_retries=1))
    responses = ["", '{"status":"ok"}']
    monkeypatch.setattr(client, "_post", lambda payload, purpose: responses.pop(0))
    answer = client.structured(RetryAnswer, [{"role": "user", "content": "Return JSON"}], "test")
    assert answer.status == "ok"
    assert responses == []


def test_image_decision_invalid_defaults_to_review() -> None:
    decision = safe_image_decision({"decision": "drop"})
    assert decision.decision == "review"
    assert decision.category == "unknown"


def test_low_confidence_drop_defaults_to_review() -> None:
    decision = safe_image_decision(
        {"category": "logo", "decision": "drop", "confidence": 0.3, "reason": "Maybe a logo"}
    )
    assert decision.decision == "review"
