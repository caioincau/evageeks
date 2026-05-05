# fetcher/evamonkey.py
"""Scrape Eva Monkey (evamonkey.com) — Platinum booklets, essays, and analysis."""
import json
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from parser.chunker import chunk_article

INDEX_PAGES = [
    {
        "url": "https://www.evamonkey.com/platinum-booklets/",
        "section": "Platinum Booklets",
    },
    {
        "url": "https://www.evamonkey.com/writings/",
        "section": "Writings",
    },
    {
        "url": "https://www.evamonkey.com/after-the-end/",
        "section": "After the End",
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

PAGE_ID_BASE = 12_000_000


def _extract_text_from_html(html: str) -> str:
    """Convert HTML fragment to plain text."""
    # Remove script/style tags
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Preserve paragraph breaks
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</?(p|div|h[1-6]|li|blockquote|tr)[^>]*>', '\n', text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode common HTML entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    # Normalize whitespace within lines, preserve paragraph breaks
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in text.split('\n')]
    text = '\n'.join(line for line in lines if line)
    return text.strip()


def _extract_article_links(html: str, base_url: str) -> list[dict]:
    """Discover article links from an Eva Monkey index/listing page.

    Looks for WordPress-style post links within the page content area.
    Returns a list of dicts with keys: url, title.
    """
    articles = []
    seen_urls: set[str] = set()

    # WordPress themes often list posts as <h2><a href="...">Title</a></h2>
    # or as <a class="entry-title-link" ...> or within <article> tags.
    # We look for links pointing back to evamonkey.com article pages.
    patterns = [
        # h1-h3 heading links (common in archive/index pages)
        re.compile(
            r'<h[1-3][^>]*>\s*<a[^>]+href="(https?://(?:www\.)?evamonkey\.com/[^"]+)"[^>]*>'
            r'([^<]+)</a>',
            re.IGNORECASE,
        ),
        # entry-title or post-title links
        re.compile(
            r'<a[^>]+class="[^"]*(?:entry-title|post-title)[^"]*"[^>]*'
            r'href="(https?://(?:www\.)?evamonkey\.com/[^"]+)"[^>]*>([^<]+)</a>',
            re.IGNORECASE,
        ),
        # Generic links within list items to evamonkey.com paths
        re.compile(
            r'<li[^>]*>\s*<a[^>]+href="(https?://(?:www\.)?evamonkey\.com/[^"]+)"[^>]*>'
            r'([^<]+)</a>',
            re.IGNORECASE,
        ),
    ]

    for pattern in patterns:
        for match in pattern.finditer(html):
            url = match.group(1).strip()
            title = match.group(2).strip()
            # Skip index pages themselves and non-article links
            if url in seen_urls:
                continue
            if any(url.rstrip('/') == idx["url"].rstrip('/') for idx in INDEX_PAGES):
                continue
            # Skip category/tag/page navigation links
            if re.search(r'/(category|tag|page)/\d*', url):
                continue
            seen_urls.add(url)
            articles.append({"url": url, "title": title})

    return articles


def _extract_post_content(html: str) -> str:
    """Extract the main post/article content from an Eva Monkey page.

    Tries WordPress content selectors in order of specificity.
    """
    # Try common WordPress content containers
    for pattern in [
        r'<div[^>]*class="[^"]*entry-content[^"]*"[^>]*>(.*?)</div>\s*(?:<(?:footer|div|aside|nav|section))',
        r'<div[^>]*class="[^"]*post-content[^"]*"[^>]*>(.*?)</div>\s*(?:<(?:footer|div|aside|nav|section))',
        r'<article[^>]*>(.*?)</article>',
        r'<div[^>]*class="[^"]*content-area[^"]*"[^>]*>(.*?)</div>',
        r'<main[^>]*>(.*?)</main>',
    ]:
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            return _extract_text_from_html(match.group(1))

    # Fallback: extract from body
    body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
    if body_match:
        return _extract_text_from_html(body_match.group(1))

    return _extract_text_from_html(html)


def _extract_page_title(html: str) -> Optional[str]:
    """Extract the <title> or og:title from the page HTML."""
    # og:title is usually cleaner
    og = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html, re.IGNORECASE)
    if og:
        return og.group(1).strip()
    title_tag = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
    if title_tag:
        # Strip common suffixes like " - Eva Monkey"
        raw = title_tag.group(1).strip()
        raw = re.sub(r'\s*[-|]\s*Eva\s*Monkey.*$', '', raw, flags=re.IGNORECASE)
        return raw.strip()
    return None


def fetch_evamonkey_section(
    index_url: str,
    section_name: str,
    output_dir: Path,
    start_id: int,
    rate_limit: float = 2.0,
    session: Optional[httpx.Client] = None,
) -> tuple[list[dict], int]:
    """Scrape all articles from one Eva Monkey index page.

    Args:
        index_url: The section index URL.
        section_name: Human-readable section name for categories.
        output_dir: Directory to write JSON article files.
        start_id: The next sequential page_id offset to use.
        rate_limit: Seconds between requests.
        session: Optional httpx.Client.

    Returns:
        Tuple of (list of saved article dicts, next available id offset).
    """
    session = session or httpx.Client(
        timeout=30.0, follow_redirects=True, headers=HEADERS,
    )
    articles: list[dict] = []

    # Fetch the index page
    print(f"Fetching index: {index_url}")
    resp = session.get(index_url)
    resp.raise_for_status()
    article_links = _extract_article_links(resp.text, index_url)
    print(f"  Found {len(article_links)} article links")

    if not article_links:
        print("  No article links found. The page structure may have changed.")
        return articles, start_id

    time.sleep(rate_limit)

    seq = start_id
    for link in article_links:
        url = link["url"]
        title = link["title"]
        slug = re.sub(r'[^a-zA-Z0-9_]', '_', title)[:80]
        outfile = output_dir / f"evamonkey_{slug}.json"

        # Skip existing (resumable)
        if outfile.exists():
            print(f"  Skipping: {title} (already exists)")
            seq += 1
            continue

        print(f"  Fetching: {title}...")
        try:
            resp = session.get(url)
            resp.raise_for_status()
            content = _extract_post_content(resp.text)

            # Use page title from HTML if available (often more accurate)
            page_title = _extract_page_title(resp.text) or title

            if len(content) < 200:
                print(f"    Skipping (too short: {len(content)} chars)")
                seq += 1
                continue

            article = {
                "page_id": PAGE_ID_BASE + seq,
                "slug": f"evamonkey_{slug}",
                "title": page_title,
                "display_title": page_title,
                "namespace": 0,
                "content_model": "evamonkey",
                "language": "en",
                "wikitext": content,
                "html": "",
                "summary": content[:500],
                "sections": [],
                "categories": ["Eva Monkey", section_name],
                "infobox": {"source": url, "section": section_name},
                "templates": [],
                "internal_links": [],
                "external_links": [url],
                "iw_links": [],
                "lang_links": [],
                "properties": {},
                "protection": [],
                "rev_id": None,
                "length_bytes": len(content),
                "parse_warnings": [],
                "touched_at": None,
                "references": [],
                "source_type": "evamonkey",
                "source_url": url,
                "authority": 70,
            }

            # Generate chunks
            chunks = chunk_article(article)
            article["chunks"] = chunks

            outfile.write_text(json.dumps(article, ensure_ascii=False, default=str))
            articles.append(article)
            print(f"    Saved ({len(content)} chars, {len(chunks)} chunks)")

        except Exception as e:
            print(f"    Error: {e}")

        seq += 1
        time.sleep(rate_limit)

    return articles, seq


def run_evamonkey_fetch(
    output_dir: str,
    rate_limit: float = 2.0,
) -> list[dict]:
    """Entry point: fetch all Eva Monkey articles across all sections.

    Args:
        output_dir: Directory to write JSON article files.
        rate_limit: Seconds to wait between HTTP requests.

    Returns:
        List of article dicts that were saved.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    session = httpx.Client(
        timeout=30.0, follow_redirects=True, headers=HEADERS,
    )

    all_articles: list[dict] = []
    next_id = 0

    for index_page in INDEX_PAGES:
        section_articles, next_id = fetch_evamonkey_section(
            index_url=index_page["url"],
            section_name=index_page["section"],
            output_dir=output_path,
            start_id=next_id,
            rate_limit=rate_limit,
            session=session,
        )
        all_articles.extend(section_articles)

    print(f"Total: {len(all_articles)} Eva Monkey articles saved to {output_path}")
    return all_articles
