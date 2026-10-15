from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import trafilatura
from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as to_markdown

from .article_selection import canonicalize_url

REMOVE_SELECTORS = [
    "header",
    "footer",
    "nav",
    "aside",
    "script",
    "style",
    "noscript",
    "form",
    "[role=navigation]",
    "[role=banner]",
    "[role=contentinfo]",
    ".newsletter",
    ".related",
    ".related-stories",
    ".social-share",
    ".share-buttons",
    ".advertisement",
    ".ad-container",
    "[data-ad]",
]


@dataclass
class ExtractedArticle:
    title: str
    author: str | None
    published_at: str | None
    canonical_url: str
    markdown: str
    image_candidates: list[dict[str, Any]]
    method: str
    character_count: int


def _meta(soup: BeautifulSoup, *selectors: tuple[str, str]) -> str | None:
    for attribute, value in selectors:
        node = soup.find("meta", attrs={attribute: value})
        if node and node.get("content"):
            return str(node["content"]).strip()
    return None


def _json_ld(soup: BeautifulSoup) -> dict[str, Any]:
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.string or node.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        items = (
            data
            if isinstance(data, list)
            else data.get("@graph", [data])
            if isinstance(data, dict)
            else []
        )
        for item in items:
            kind = item.get("@type") if isinstance(item, dict) else None
            kinds = kind if isinstance(kind, list) else [kind]
            if any(value in {"NewsArticle", "Article", "ReportageNewsArticle"} for value in kinds):
                return item
    return {}


def _best_image_url(image: Tag, base_url: str) -> str | None:
    picture = image.find_parent("picture")
    if picture:
        for source in picture.select("source[srcset]"):
            srcset = str(source.get("srcset", ""))
            if srcset:
                return urljoin(base_url, srcset.split(",")[-1].strip().split()[0])
    for attr in ("data-src", "data-lazy-src", "data-original", "src"):
        value = image.get(attr)
        if isinstance(value, str) and value and not value.startswith("data:"):
            return urljoin(base_url, value)
    srcset = image.get("srcset")
    if isinstance(srcset, str) and srcset:
        return urljoin(base_url, srcset.split(",")[-1].strip().split()[0])
    return None


def _nearby_text(image: Tag) -> str:
    parts: list[str] = []
    for sibling in (image.find_previous(["p", "figcaption"]), image.find_next(["p", "figcaption"])):
        if sibling:
            parts.append(" ".join(sibling.get_text(" ", strip=True).split()))
    return " ".join(parts)[:1200]


def extract_image_candidates(root: Tag, base_url: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for position, image in enumerate(root.select("img"), start=1):
        url = _best_image_url(image, base_url)
        if not url or url in seen_urls:
            continue
        image["src"] = url
        for attribute in ("srcset", "data-src", "data-lazy-src", "data-original"):
            image.attrs.pop(attribute, None)
        figure = image.find_parent("figure")
        caption_node = figure.find("figcaption") if figure else None
        parent = image.parent if isinstance(image.parent, Tag) else None
        ancestor = image.find_parent(["figure", "article", "section", "div"])
        result.append(
            {
                "position": position,
                "original_url": url,
                "alt": str(image.get("alt", "")).strip(),
                "caption": " ".join(caption_node.get_text(" ", strip=True).split())
                if caption_node
                else "",
                "dom_ancestor": ancestor.name if ancestor else "",
                "dom_context": " ".join(
                    filter(
                        None,
                        [
                            ancestor.name if ancestor else "",
                            " ".join(ancestor.get("class", [])) if ancestor else "",
                            str(ancestor.get("id", "")) if ancestor else "",
                            " ".join(parent.get("class", [])) if parent else "",
                        ],
                    )
                )[:400],
                "nearby_text": _nearby_text(image),
                "declared_width": image.get("width"),
                "declared_height": image.get("height"),
            }
        )
        seen_urls.add(url)
    return result


def extract_article_from_html(html: str, page_url: str) -> ExtractedArticle:
    soup = BeautifulSoup(html, "lxml")
    structured = _json_ld(soup)
    canonical_node = soup.select_one('link[rel="canonical"]')
    canonical = (
        str(canonical_node.get("href"))
        if canonical_node and canonical_node.get("href")
        else str(structured.get("url") or page_url)
    )
    title = (
        str(structured.get("headline") or "").strip()
        or _meta(soup, ("property", "og:title"), ("name", "twitter:title"))
        or (soup.title.get_text(strip=True) if soup.title else "Untitled article")
    )
    author_data = structured.get("author")
    if isinstance(author_data, list):
        author = ", ".join(
            str(item.get("name"))
            for item in author_data
            if isinstance(item, dict) and item.get("name")
        )
    elif isinstance(author_data, dict):
        author = str(author_data.get("name")) if author_data.get("name") else None
    else:
        author = _meta(
            soup,
            ("name", "author"),
            ("property", "article:author"),
            ("property", "og:author"),
        )
    if not author:
        author_node = soup.select_one(
            '[itemprop="author"] [itemprop="name"], '
            '[itemprop="author"] .news-article--author, [itemprop="author"]'
        )
        if author_node:
            author = " ".join(author_node.get_text(" ", strip=True).split())
    published = str(structured.get("datePublished") or "").strip() or _meta(
        soup, ("property", "article:published_time"), ("name", "date")
    )
    if not published:
        published_node = soup.select_one(
            '[itemprop="datePublished"] time[datetime], time[itemprop="datePublished"][datetime]'
        )
        if published_node and published_node.get("datetime"):
            published = str(published_node.get("datetime")).strip()
    lesson_root = soup.select_one(".ed-lesson-content")
    article_root = soup.find("article")
    main_root = soup.find("main")
    root = lesson_root or article_root or main_root
    method = (
        "site_lesson_content"
        if lesson_root
        else "semantic_article"
        if article_root
        else "semantic_main"
    )
    if not isinstance(root, Tag):
        extracted = trafilatura.extract(
            html, url=page_url, output_format="html", include_images=True
        )
        if not extracted:
            raise ValueError("No semantic article/main and trafilatura could not extract content")
        fragment = BeautifulSoup(extracted, "lxml")
        root = fragment.body or fragment
        method = "trafilatura"
    image_candidates = extract_image_candidates(root, page_url)
    for selector in REMOVE_SELECTORS:
        for node in root.select(selector):
            node.decompose()
    markdown = to_markdown(str(root), heading_style="ATX", bullets="-")
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    if not markdown:
        raise ValueError("Extracted article body is empty")
    return ExtractedArticle(
        title=title,
        author=author or None,
        published_at=published or None,
        canonical_url=canonicalize_url(urljoin(page_url, canonical)),
        markdown=markdown,
        image_candidates=image_candidates,
        method=method,
        character_count=len(markdown),
    )
