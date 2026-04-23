# fetcher/interviews.py
"""Fetch external Evangelion interviews not already on the wiki."""
import re
import time
from pathlib import Path
from typing import Optional
import httpx

# Gwern.net hosts public-domain interview translations
GWERN_INTERVIEWS = [
    {
        "url": "https://gwern.net/doc/anime/eva/1996-newtype-anno-interview",
        "title": "Hideaki Anno - NewType Interview (June 1996)",
        "date": "1996-06-01",
    },
    {
        "url": "https://gwern.net/doc/anime/eva/1997-anno-english",
        "title": "Hideaki Anno - AnimeLand Interview (May 1997, English)",
        "date": "1997-05-01",
    },
    {
        "url": "https://gwern.net/doc/anime/eva/2010-crc",
        "title": "Evangelion 2.0 Complete Records Collection",
        "date": "2010-01-01",
    },
    {
        "url": "https://gwern.net/doc/anime/eva/1996-animerica-conscience",
        "title": "The Conscience of the Otaking - Studio Gainax Saga",
        "date": "1996-01-01",
    },
]


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


def fetch_interview(
    url: str,
    session: Optional[httpx.Client] = None,
) -> str:
    """Fetch and extract text from an interview URL."""
    session = session or httpx.Client(timeout=30.0, follow_redirects=True)
    resp = session.get(url)
    resp.raise_for_status()

    # Try to find the main content area
    html = resp.text
    # Look for article/main/content tags
    for pattern in [r'<article[^>]*>(.*?)</article>', r'<main[^>]*>(.*?)</main>', r'<div[^>]*id="content"[^>]*>(.*?)</div>']:
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            return _extract_text_from_html(match.group(1))

    # Fallback: extract from body
    body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
    if body_match:
        return _extract_text_from_html(body_match.group(1))

    return _extract_text_from_html(html)


def run_interview_fetch(
    output_dir: str,
    rate_limit: float = 1.0,
) -> list[dict]:
    """Fetch external interviews and save as JSON articles for ingestion."""
    import json

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    session = httpx.Client(timeout=30.0, follow_redirects=True)
    articles = []

    for i, interview in enumerate(GWERN_INTERVIEWS):
        print(f"Fetching: {interview['title']}...")
        try:
            text = fetch_interview(interview["url"], session=session)
            if len(text) < 200:
                print(f"  Skipping (too short: {len(text)} chars)")
                continue

            slug = re.sub(r'[^a-zA-Z0-9_]', '_', interview["title"])[:80]

            article = {
                "page_id": 2000000 + i,
                "slug": f"interview_{slug}",
                "title": interview["title"],
                "display_title": interview["title"],
                "namespace": 0,
                "content_model": "interview",
                "language": "en",
                "wikitext": text,
                "html": "",
                "summary": text[:500],
                "sections": [],
                "categories": ["Interviews", "Staff Statements"],
                "infobox": {"date": interview["date"], "source": interview["url"]},
                "templates": [],
                "internal_links": [],
                "external_links": [interview["url"]],
                "iw_links": [],
                "lang_links": [],
                "properties": {},
                "protection": [],
                "rev_id": None,
                "length_bytes": len(text),
                "parse_warnings": [],
                "touched_at": None,
                "references": [],
                "source_type": "interview",
                "source_url": interview["url"],
            }

            outfile = output_path / f"interview_{slug}.json"
            outfile.write_text(json.dumps(article, ensure_ascii=False, default=str))
            articles.append(article)
            print(f"  Saved ({len(text)} chars)")

        except Exception as e:
            print(f"  Error: {e}")

        time.sleep(rate_limit)

    print(f"Total: {len(articles)} interviews saved to {output_path}")
    return articles
