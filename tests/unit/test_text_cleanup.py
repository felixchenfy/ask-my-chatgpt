from web_content_agent.text_cleanup import conservative_text_cleanup


def test_mit_template_sections_are_removed_without_losing_body() -> None:
    markdown = """# Story

Deck text.

### Press Contact:

Name and email

### Media Download

[![thumbnail](images/001.jpg)](/download)

#### *Terms of Use:

Images for download boilerplate

[Close](#)

![lead](images/002.jpg)

Caption:

Useful lead caption.

Previous item
Next item

The article body remains.

### Share this news article on:

- [Facebook](https://facebook.test/share)

Paper link remains.

## Related Links

Template links disappear.
"""
    cleaned = conservative_text_cleanup(markdown)
    assert "Press Contact" not in cleaned
    assert "Media Download" not in cleaned
    assert "Images for download" not in cleaned
    assert "Facebook" not in cleaned
    assert "Previous item" not in cleaned
    assert "Related Links" not in cleaned
    assert "Template links" not in cleaned
    assert "![lead](images/002.jpg)" in cleaned
    assert "Useful lead caption." in cleaned
    assert "The article body remains." in cleaned
    assert "Paper link remains." in cleaned
