from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from .article_selection import ArticleSelection, LinkCandidate, canonicalize_url

EXCLUDED_HINTS = re.compile(
    r"\b(ad|advert|sponsor|newsletter|subscribe|sign.?up|author|category|tag|video|live|"
    r"podcast|login|donate|privacy|terms|about|contact|facebook|twitter|instagram|youtube)\b",
    re.I,
)


def _allowed(url: str, allowed_domains: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)


def collect_link_candidates(
    html: str,
    homepage_url: str,
    allowed_domains: list[str],
    limit: int = 40,
) -> list[LinkCandidate]:
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    candidates: list[LinkCandidate] = []
    for anchor in soup.select("a[href]"):
        if not isinstance(anchor, Tag) or anchor.find_parent(["nav", "footer"]):
            continue
        title = " ".join(anchor.get_text(" ", strip=True).split())
        href = anchor.get("href")
        if not title or len(title) < 4 or not isinstance(href, str):
            continue
        url = canonicalize_url(href, homepage_url)
        if not _allowed(url, allowed_domains) or url == canonicalize_url(homepage_url):
            continue
        path = urlparse(url).path.lower()
        context_node = anchor.find_parent(["article", "section", "li", "div"])
        context = (
            " ".join(context_node.get_text(" ", strip=True).split())[:300] if context_node else ""
        )
        semantic = " ".join(
            filter(
                None,
                [
                    anchor.get("class") and " ".join(anchor.get("class", [])),
                    anchor.get("aria-label"),
                    context_node and " ".join(context_node.get("class", [])),
                    path,
                ],
            )
        )
        if EXCLUDED_HINTS.search(f"{title} {semantic}"):
            continue
        if url in seen:
            continue
        heading = anchor.find_parent(["h1", "h2", "h3", "h4"])
        article = anchor.find_parent("article")
        prominence = (30 if heading and heading.name == "h1" else 20 if heading else 0) + (
            15 if article else 0
        )
        prominence += max(0, 10 - len(candidates) // 5)
        candidates.append(
            LinkCandidate(title=title[:300], url=url, prominence=prominence, context=context)
        )
        seen.add(url)
    return sorted(candidates, key=lambda item: item.prominence, reverse=True)[:limit]


def make_dom_summary(candidates: list[LinkCandidate]) -> str:
    return "\n".join(
        json.dumps({"index": index, **candidate.model_dump()}, ensure_ascii=False)
        for index, candidate in enumerate(candidates, start=1)
    )


def collect_course_candidates(
    html: str,
    course_url: str,
    allowed_domains: list[str],
    limit: int = 500,
) -> list[LinkCandidate]:
    """Collect same-course links in DOM order for deterministic breadth-first traversal."""
    soup = BeautifulSoup(html, "lxml")
    root = canonicalize_url(course_url)
    root_path = urlparse(root).path.rstrip("/")
    structured = _collect_numbered_curriculum(soup, course_url, root_path, allowed_domains, limit)
    if structured:
        return structured
    seen: set[str] = set()
    candidates: list[LinkCandidate] = []
    for anchor in soup.select("a[href]"):
        if not isinstance(anchor, Tag) or anchor.find_parent(["nav", "footer"]):
            continue
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        url = canonicalize_url(href, course_url)
        path = urlparse(url).path.rstrip("/")
        if url == root or url in seen or not _allowed(url, allowed_domains):
            continue
        if root_path and not (path == root_path or path.startswith(f"{root_path}/")):
            continue
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title:
            title = anchor.get("aria-label") or path.rsplit("/", 1)[-1].replace("-", " ")
        semantic = " ".join(
            filter(None, [title, anchor.get("aria-label"), " ".join(anchor.get("class", [])), path])
        )
        if EXCLUDED_HINTS.search(semantic):
            continue
        context_node = anchor.find_parent(["article", "section", "li", "div"])
        context = (
            " ".join(context_node.get_text(" ", strip=True).split())[:300] if context_node else ""
        )
        candidates.append(LinkCandidate(title=title[:300], url=url, prominence=0, context=context))
        seen.add(url)
        if len(candidates) >= limit:
            break
    return candidates


def _collect_numbered_curriculum(
    soup: BeautifulSoup,
    course_url: str,
    root_path: str,
    allowed_domains: list[str],
    limit: int,
) -> list[LinkCandidate]:
    """Parse a visible numbered sidebar without flattening its section/lesson hierarchy."""
    seen: set[str] = set()
    candidates: list[LinkCandidate] = []
    for heading in soup.select("nav h5"):
        if not isinstance(heading, Tag):
            continue
        heading_text = " ".join(heading.get_text(" ", strip=True).split())
        match = re.match(r"^(\d+)\s*\.\s*(.+)$", heading_text)
        if not match:
            continue
        section_number = int(match.group(1))
        section_title = match.group(2).strip()
        group: Tag | None = heading
        while isinstance(group, Tag):
            anchors = group.select("a[href]")
            nested_headings = group.select("h5")
            if anchors and len(nested_headings) == 1:
                break
            group = group.parent if isinstance(group.parent, Tag) else None
        if not isinstance(group, Tag):
            continue
        lesson_number = 0
        for anchor in group.select("a[href]"):
            href = anchor.get("href")
            if not isinstance(href, str):
                continue
            url = canonicalize_url(href, course_url)
            path = urlparse(url).path.rstrip("/")
            if url in seen or not _allowed(url, allowed_domains):
                continue
            if root_path and not (path == root_path or path.startswith(f"{root_path}/")):
                continue
            title = " ".join(anchor.get_text(" ", strip=True).split())
            if not title:
                continue
            lesson_number += 1
            candidates.append(
                LinkCandidate(
                    title=title[:300],
                    url=url,
                    prominence=0,
                    context=f"{section_number}. {section_title}",
                    section_number=section_number,
                    section_title=section_title[:300],
                    lesson_number=lesson_number,
                )
            )
            seen.add(url)
            if len(candidates) >= limit:
                return candidates
    return candidates


def validate_selection_against_candidates(
    selection: ArticleSelection,
    candidates: list[LinkCandidate],
    allowed_domains: list[str],
) -> ArticleSelection:
    available = {canonicalize_url(item.url) for item in candidates}
    for article in selection.articles:
        if canonicalize_url(article.url) not in available:
            raise ValueError(f"Model selected URL absent from DOM candidates: {article.url}")
        if not _allowed(article.url, allowed_domains):
            raise ValueError(f"Model selected URL outside allowed domains: {article.url}")
    return selection
