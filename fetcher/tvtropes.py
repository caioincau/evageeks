# fetcher/tvtropes.py
"""Fetch TV Tropes content for Evangelion articles."""
import json
import re
import time
from pathlib import Path
from typing import Optional
import httpx


_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Key Evangelion pages on TV Tropes
TVTROPES_PAGES = [
    {
        "url": "https://tvtropes.org/pmwiki/pmwiki.php/Anime/NeonGenesisEvangelion",
        "title": "Neon Genesis Evangelion - TV Tropes",
        "category": "Series",
    },
    {
        "url": "https://tvtropes.org/pmwiki/pmwiki.php/Characters/NeonGenesisEvangelion",
        "title": "Neon Genesis Evangelion Characters - TV Tropes",
        "category": "Characters",
    },
    {
        "url": "https://tvtropes.org/pmwiki/pmwiki.php/Characters/NeonGenesisEvangelionAsukaLangleySoryu",
        "title": "Asuka Langley Soryu - TV Tropes",
        "category": "Characters",
    },
    {
        "url": "https://tvtropes.org/pmwiki/pmwiki.php/Characters/NeonGenesisEvangelionNERVStaff",
        "title": "NERV Staff Characters - TV Tropes",
        "category": "Characters",
    },
    {
        "url": "https://tvtropes.org/pmwiki/pmwiki.php/YMMV/NeonGenesisEvangelion",
        "title": "Neon Genesis Evangelion YMMV - TV Tropes",
        "category": "YMMV",
    },
    {
        "url": "https://tvtropes.org/pmwiki/pmwiki.php/DeconstructedCharacterArchetype/NeonGenesisEvangelion",
        "title": "Deconstructed Character Archetype - Evangelion - TV Tropes",
        "category": "Analysis",
    },
    {
        "url": "https://tvtropes.org/pmwiki/pmwiki.php/Anime/RebuildOfEvangelion",
        "title": "Rebuild of Evangelion - TV Tropes",
        "category": "Rebuild",
    },
    {
        "url": "https://tvtropes.org/pmwiki/pmwiki.php/Manga/NeonGenesisEvangelion",
        "title": "Neon Genesis Evangelion Manga - TV Tropes",
        "category": "Manga",
    },
    {
        "url": "https://tvtropes.org/pmwiki/pmwiki.php/LightNovel/NeonGenesisEvangelionAnima",
        "title": "Neon Genesis Evangelion ANIMA - TV Tropes",
        "category": "ANIMA",
    },
    {
        "url": "https://tvtropes.org/pmwiki/pmwiki.php/Film/TheEndOfEvangelion",
        "title": "The End of Evangelion - TV Tropes",
        "category": "Film",
    },
]


def _make_session() -> httpx.Client:
    return httpx.Client(timeout=60.0, follow_redirects=True, headers=_HEADERS)


def _extract_text_from_html(html: str) -> str:
    """Simple HTML to text extraction."""
    # Remove script/style tags
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    # Decode HTML entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'")
    return text


def _extract_main_content(html: str) -> str:
    """Extract the main article content from a TV Tropes page.

    TV Tropes stores trope content inside a div with id="main-article"
    or the wikitext-body class. We try several selectors.
    """
    # Try the main article container first (most reliable)
    for pattern in [
        r'<div[^>]*id="main-article"[^>]*>(.*?)</div>\s*<!--\s*/main-article',
        r'<div[^>]*id="main-article"[^>]*>(.*?)<div[^>]*id="(?:sidebar|tropelist)"',
        r'<div[^>]*class="[^"]*article-content[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*class="[^"]*page-content[^"]*"[^>]*>(.*?)</div>',
    ]:
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            return _extract_text_from_html(match.group(1))

    # Broader fallback: grab everything between <article> tags
    match = re.search(r'<article[^>]*>(.*?)</article>', html, re.DOTALL | re.IGNORECASE)
    if match:
        return _extract_text_from_html(match.group(1))

    # Last resort: extract from body, skipping nav/footer
    body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
    if body_match:
        body = body_match.group(1)
        # Strip nav, header, footer
        body = re.sub(r'<(nav|header|footer)[^>]*>.*?</\1>', '', body, flags=re.DOTALL | re.IGNORECASE)
        return _extract_text_from_html(body)

    return _extract_text_from_html(html)


def _extract_trope_entries(html: str) -> list[str]:
    """Extract individual trope bullet-point entries from the page.

    Returns a list of trope texts for use in the sections field.
    """
    entries = []
    # TV Tropes uses <li> elements inside <ul> for trope lists
    for match in re.finditer(r'<li[^>]*>(.*?)</li>', html, re.DOTALL):
        text = _extract_text_from_html(match.group(1))
        if len(text) > 30:
            entries.append(text)
    return entries


def _slug_from_url(url: str) -> str:
    """Derive a filesystem-safe slug from a TV Tropes URL."""
    # e.g. https://tvtropes.org/pmwiki/pmwiki.php/Anime/NeonGenesisEvangelion
    #   -> Anime_NeonGenesisEvangelion
    parts = url.rstrip("/").split("/pmwiki/pmwiki.php/")
    if len(parts) == 2:
        return re.sub(r'[^a-zA-Z0-9_]', '_', parts[1])[:80]
    return re.sub(r'[^a-zA-Z0-9_]', '_', url.split("//")[1])[:80]


def fetch_tvtropes_page(
    url: str,
    session: Optional[httpx.Client] = None,
    retries: int = 3,
    rate_limit: float = 3.0,
) -> Optional[str]:
    """Fetch a single TV Tropes page, returning raw HTML or None."""
    session = session or _make_session()

    for attempt in range(retries):
        try:
            resp = session.get(url)
            if resp.status_code == 429:
                wait = rate_limit * (2 ** attempt) + 5
                print(f"    429 rate limited, waiting {wait:.0f}s (attempt {attempt + 1}/{retries})")
                time.sleep(wait)
                continue
            if resp.status_code == 403:
                print(f"    403 Forbidden for {url} (attempt {attempt + 1}/{retries})")
                time.sleep(rate_limit * (2 ** attempt))
                continue
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPStatusError:
            if attempt < retries - 1:
                time.sleep(rate_limit * (2 ** attempt))
                continue
            raise
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            if attempt < retries - 1:
                wait = rate_limit * (2 ** attempt) + 3
                print(f"    Connection error, waiting {wait:.0f}s: {e}")
                time.sleep(wait)
                continue
            print(f"  Failed after {retries} attempts: {e}")
            return None

    return None


def run_tvtropes_fetch(
    output_dir: str,
    pages_list: Optional[list[dict]] = None,
    rate_limit: float = 3.0,
) -> list[dict]:
    """Fetch TV Tropes pages and save as JSON articles for ingestion.

    Args:
        output_dir: Directory to write JSON article files.
        pages_list: List of page dicts with url/title/category keys.
                    Defaults to TVTROPES_PAGES if not provided.
        rate_limit: Seconds between requests (TV Tropes is aggressive
                    about blocking scrapers, 3s minimum recommended).
    """
    pages_list = pages_list or TVTROPES_PAGES
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    session = _make_session()
    articles = []

    for i, page_info in enumerate(pages_list):
        url = page_info["url"]
        title = page_info["title"]
        category = page_info.get("category", "General")
        slug = _slug_from_url(url)
        outfile = output_path / f"tvtropes_{slug}.json"

        if outfile.exists():
            print(f"Skipping (exists): {title}")
            continue

        print(f"Fetching: {title}...")
        try:
            html = fetch_tvtropes_page(url, session=session, rate_limit=rate_limit)
            if html is None:
                print(f"  Skipping (fetch failed)")
                continue

            text = _extract_main_content(html)
            if len(text) < 200:
                print(f"  Skipping (too short: {len(text)} chars)")
                continue

            trope_entries = _extract_trope_entries(html)

            article = {
                "page_id": 9000000 + i,
                "slug": f"tvtropes_{slug}",
                "title": title,
                "display_title": title,
                "namespace": 0,
                "content_model": "tvtropes",
                "language": "en",
                "wikitext": text,
                "html": "",
                "summary": text[:500],
                "sections": [{"title": "Tropes", "entries": trope_entries}] if trope_entries else [],
                "categories": ["TV Tropes", f"TV Tropes: {category}"],
                "infobox": {
                    "source": url,
                    "category": category,
                },
                "templates": [],
                "internal_links": [],
                "external_links": [url],
                "iw_links": [],
                "lang_links": [],
                "properties": {},
                "protection": [],
                "rev_id": None,
                "length_bytes": len(text),
                "parse_warnings": [],
                "touched_at": None,
                "references": [],
                "source_type": "tvtropes",
                "source_url": url,
                "authority": 40,
            }

            outfile.write_text(json.dumps(article, ensure_ascii=False, default=str))
            articles.append(article)
            print(f"  Saved ({len(text)} chars, {len(trope_entries)} trope entries)")

        except Exception as e:
            print(f"  Error: {e}")

        time.sleep(rate_limit)

    print(f"Total: {len(articles)} TV Tropes articles saved to {output_path}")
    return articles
