# fetcher/qmisato.py
"""Scrape Qmisato's Tumblr blog — deep Eva character psychology analysis."""
import json
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from parser.chunker import chunk_article

BLOG_BASE_URL = "https://qmisato.tumblr.com"

# Tags to look for when filtering Evangelion-related posts
EVA_TAGS = {"evangelion", "nge", "neon genesis evangelion", "analysis",
            "character analysis", "eva", "shinji", "asuka", "rei", "misato",
            "kaworu", "gendo", "end of evangelion", "eoe", "rebuilds"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

PAGE_ID_BASE = 13_000_000


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


def _extract_post_blocks(html: str) -> list[dict]:
    """Extract individual post blocks from a Tumblr page.

    Tumblr themes vary widely, so we try multiple strategies to find posts.
    Returns a list of dicts with keys: url, title, content_html.
    """
    posts: list[dict] = []

    # Strategy 1: <article> tags with post data
    article_pattern = re.compile(
        r'<article[^>]*>(.*?)</article>',
        re.DOTALL | re.IGNORECASE,
    )
    for match in article_pattern.finditer(html):
        block = match.group(1)
        post = _parse_post_block(block)
        if post:
            posts.append(post)

    if posts:
        return posts

    # Strategy 2: div.post or div.entry blocks
    div_pattern = re.compile(
        r'<div[^>]*class="[^"]*(?:post|entry)[^"]*"[^>]*>(.*?)</div>\s*(?=<div[^>]*class="[^"]*(?:post|entry)|$)',
        re.DOTALL | re.IGNORECASE,
    )
    for match in div_pattern.finditer(html):
        block = match.group(1)
        post = _parse_post_block(block)
        if post:
            posts.append(post)

    return posts


def _parse_post_block(block_html: str) -> Optional[dict]:
    """Parse a single post block HTML and extract url, title, content."""
    # Find permalink
    url_match = re.search(
        r'<a[^>]+href="(https?://qmisato\.tumblr\.com/post/\d+[^"]*)"',
        block_html,
        re.IGNORECASE,
    )
    url = url_match.group(1) if url_match else None

    # Find title (h1, h2, or post-title class)
    title = None
    for pattern in [
        r'<h[12][^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</h[12]>',
        r'<h[12][^>]*>([^<]+)</h[12]>',
        r'<a[^>]*class="[^"]*post-title[^"]*"[^>]*>([^<]+)</a>',
    ]:
        title_match = re.search(pattern, block_html, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()
            break

    # Find post body/content
    content_html = None
    for pattern in [
        r'<div[^>]*class="[^"]*(?:post-content|body|entry-content|caption|post-body|text)[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*class="[^"]*description[^"]*"[^>]*>(.*?)</div>',
    ]:
        content_match = re.search(pattern, block_html, re.DOTALL | re.IGNORECASE)
        if content_match:
            content_html = content_match.group(1)
            break

    if not content_html:
        # Use the whole block as content fallback
        content_html = block_html

    if not url:
        return None

    return {"url": url, "title": title, "content_html": content_html}


def _discover_post_urls_from_page(html: str) -> list[str]:
    """Extract all individual post URLs from a Tumblr page.

    Used as a fallback when post blocks are not parseable inline.
    """
    urls: list[str] = []
    seen: set[str] = set()
    pattern = re.compile(
        r'href="(https?://qmisato\.tumblr\.com/post/(\d+)[^"]*)"',
        re.IGNORECASE,
    )
    for match in pattern.finditer(html):
        url = match.group(1)
        post_id = match.group(2)
        if post_id not in seen:
            seen.add(post_id)
            urls.append(url)
    return urls


def _extract_single_post(html: str) -> tuple[Optional[str], str]:
    """Extract title and content from a single Tumblr post page.

    Returns (title, content_text).
    """
    # Title from og:title or <title>
    title = None
    og = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html, re.IGNORECASE)
    if og:
        title = og.group(1).strip()
    else:
        title_tag = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
        if title_tag:
            raw = title_tag.group(1).strip()
            # Strip blog name suffix
            raw = re.sub(r'\s*[-|]\s*(?:qmisato|tumblr).*$', '', raw, flags=re.IGNORECASE)
            title = raw.strip() if raw.strip() else None

    # Content extraction: try common Tumblr content containers
    content = ""
    for pattern in [
        r'<div[^>]*class="[^"]*(?:post-content|body|entry-content|caption|post-body|text)[^"]*"[^>]*>(.*?)</div>',
        r'<article[^>]*>(.*?)</article>',
        r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>',
        r'<main[^>]*>(.*?)</main>',
    ]:
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            content = _extract_text_from_html(match.group(1))
            break

    if not content:
        body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
        if body_match:
            content = _extract_text_from_html(body_match.group(1))

    return title, content


def _extract_tags_from_post(html: str) -> list[str]:
    """Extract tags from a Tumblr post page."""
    tags: list[str] = []
    # Tumblr tags are often in <a> tags with class "tag" or href containing /tagged/
    tag_pattern = re.compile(
        r'<a[^>]+href="[^"]*?/tagged/([^"]+)"[^>]*>',
        re.IGNORECASE,
    )
    for match in tag_pattern.finditer(html):
        raw_tag = match.group(1).replace('-', ' ').replace('+', ' ').strip().lower()
        if raw_tag and raw_tag not in tags:
            tags.append(raw_tag)
    return tags


def _is_eva_related(tags: list[str], content: str) -> bool:
    """Check if a post is Evangelion-related based on tags or content."""
    # Check tags
    for tag in tags:
        if tag.lower() in EVA_TAGS:
            return True

    # Check content for Eva keywords (case-insensitive)
    content_lower = content.lower()
    eva_keywords = [
        "evangelion", "nerv", "seele", "instrumentality",
        "shinji ikari", "asuka langley", "rei ayanami", "misato katsuragi",
        "kaworu nagisa", "gendo ikari", "entry plug", "angel attack",
        "unit-01", "unit 01", "eva-01", "eva 01",
        "end of evangelion", "third impact",
    ]
    return any(kw in content_lower for kw in eva_keywords)


def fetch_qmisato_posts(
    output_dir: Path,
    max_pages: int = 50,
    rate_limit: float = 3.0,
    session: Optional[httpx.Client] = None,
) -> list[dict]:
    """Scrape Qmisato's Tumblr by paginating through the blog.

    Args:
        output_dir: Directory to write JSON article files.
        max_pages: Maximum number of listing pages to scrape.
        rate_limit: Seconds between requests.
        session: Optional httpx.Client.

    Returns:
        List of article dicts that were saved.
    """
    session = session or httpx.Client(
        timeout=30.0, follow_redirects=True, headers=HEADERS,
    )
    articles: list[dict] = []
    all_post_urls: list[str] = []
    seen_post_ids: set[str] = set()

    # Phase 1: Paginate through the blog to discover post URLs
    for page_num in range(1, max_pages + 1):
        if page_num == 1:
            url = BLOG_BASE_URL
        else:
            url = f"{BLOG_BASE_URL}/page/{page_num}"

        print(f"Fetching listing page {page_num}: {url}")
        try:
            resp = session.get(url)
            if resp.status_code == 404:
                print(f"  Page {page_num} not found, stopping pagination.")
                break
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            print(f"  Stopped pagination at page {page_num}.")
            break

        post_urls = _discover_post_urls_from_page(resp.text)
        if not post_urls:
            print(f"  No posts found on page {page_num}, stopping.")
            break

        new_count = 0
        for post_url in post_urls:
            # Extract the post ID from the URL for deduplication
            id_match = re.search(r'/post/(\d+)', post_url)
            if id_match:
                post_id = id_match.group(1)
                if post_id not in seen_post_ids:
                    seen_post_ids.add(post_id)
                    all_post_urls.append(post_url)
                    new_count += 1

        print(f"  Found {len(post_urls)} links, {new_count} new")

        if new_count == 0:
            print("  No new posts found, stopping.")
            break

        time.sleep(rate_limit)

    print(f"Discovered {len(all_post_urls)} unique post URLs")

    # Phase 2: Fetch each individual post
    for seq, post_url in enumerate(all_post_urls):
        # Build slug from post ID
        id_match = re.search(r'/post/(\d+)(?:/([^?#]+))?', post_url)
        if id_match:
            post_id = id_match.group(1)
            url_slug = id_match.group(2) or post_id
        else:
            post_id = str(seq)
            url_slug = str(seq)

        slug = re.sub(r'[^a-zA-Z0-9_]', '_', url_slug)[:80]
        outfile = output_dir / f"qmisato_{slug}.json"

        # Skip existing (resumable)
        if outfile.exists():
            print(f"  Skipping post {post_id} (already exists)")
            continue

        print(f"  Fetching post {post_id}: {post_url}...")
        try:
            resp = session.get(post_url)
            resp.raise_for_status()
            page_html = resp.text

            title, content = _extract_single_post(page_html)
            tags = _extract_tags_from_post(page_html)

            if len(content) < 200:
                print(f"    Skipping (too short: {len(content)} chars)")
                continue

            # Filter to Eva-related posts
            if not _is_eva_related(tags, content):
                print(f"    Skipping (not Eva-related)")
                continue

            display_title = title or f"Qmisato Post {post_id}"

            article = {
                "page_id": PAGE_ID_BASE + seq,
                "slug": f"qmisato_{slug}",
                "title": display_title,
                "display_title": display_title,
                "namespace": 0,
                "content_model": "analysis",
                "language": "en",
                "wikitext": content,
                "html": "",
                "summary": content[:500],
                "sections": [],
                "categories": ["Fan Analysis", "Qmisato"],
                "infobox": {
                    "source": post_url,
                    "tumblr_post_id": post_id,
                    "tags": tags,
                },
                "templates": [],
                "internal_links": [],
                "external_links": [post_url],
                "iw_links": [],
                "lang_links": [],
                "properties": {},
                "protection": [],
                "rev_id": None,
                "length_bytes": len(content),
                "parse_warnings": [],
                "touched_at": None,
                "references": [],
                "source_type": "analysis",
                "source_url": post_url,
                "authority": 60,
            }

            # Generate chunks
            chunks = chunk_article(article)
            article["chunks"] = chunks

            outfile.write_text(json.dumps(article, ensure_ascii=False, default=str))
            articles.append(article)
            print(f"    Saved ({len(content)} chars, {len(chunks)} chunks)")

        except Exception as e:
            print(f"    Error: {e}")

        time.sleep(rate_limit)

    return articles


def run_qmisato_fetch(
    output_dir: str,
    rate_limit: float = 3.0,
) -> list[dict]:
    """Entry point: fetch all Evangelion analysis posts from Qmisato's Tumblr.

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

    articles = fetch_qmisato_posts(
        output_dir=output_path,
        rate_limit=rate_limit,
        session=session,
    )

    print(f"Total: {len(articles)} Qmisato analysis posts saved to {output_path}")
    return articles
