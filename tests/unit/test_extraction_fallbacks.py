from web_content_agent.extraction import extract_article_from_html


def test_itemprop_author_and_date_fallbacks() -> None:
    html = """
    <html><head>
      <title>Fallback story</title>
      <link rel="canonical" href="https://fixture.test/fallback">
    </head><body><article>
      <div itemprop="author"><span class="news-article--author">Test Author</span></div>
      <div itemprop="datePublished"><time datetime="2026-08-12T15:00:00Z">August 12</time></div>
      <h1>Fallback story</h1><p>Substantive article paragraph.</p>
    </article></body></html>
    """
    extracted = extract_article_from_html(html, "https://fixture.test/fallback")
    assert extracted.author == "Test Author"
    assert extracted.published_at == "2026-08-12T15:00:00Z"


def test_educative_lesson_content_wins_over_course_navigation() -> None:
    html = """
    <html><head><title>Dropbox System Design</title></head><body>
      <nav><h5>36. Design WhatsApp</h5><a href="/lesson">Navigation lesson</a></nav>
      <main><div class="ed-lesson-content">
        <h2>Requirements</h2><p>The complete Dropbox lesson body is rendered here.</p>
      </div></main>
    </body></html>
    """
    extracted = extract_article_from_html(
        html, "https://www.educative.io/courses/demo/lta/dropbox-system-design"
    )
    assert extracted.method == "site_lesson_content"
    assert "complete Dropbox lesson body" in extracted.markdown
    assert "Navigation lesson" not in extracted.markdown


def test_educative_script_marker_does_not_count_as_rendered_lesson() -> None:
    html = """
    <html><head><title>Logged-out preview</title></head><body>
      <main><p>Preview only.</p><script>{"cssSelector":".ed-lesson-content"}</script></main>
    </body></html>
    """
    extracted = extract_article_from_html(html, "https://www.educative.io/courses/demo/preview")
    assert extracted.method == "semantic_main"
