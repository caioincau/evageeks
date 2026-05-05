# fetcher/transcripts.py
"""Scrape episode transcripts from subslikescript.com."""
import json
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from parser.chunker import chunk_article

DEFAULT_SERIES_URL = "https://subslikescript.com/series/Neon_Genesis_Evangelion-112159"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

PAGE_ID_BASE = 7_000_000


def _extract_episode_links(html: str, base_url: str) -> list[dict]:
    """Parse the series page to find individual episode links.

    Returns a list of dicts with keys: url, episode_number, episode_title.
    """
    episodes = []
    # subslikescript lists episodes as <a> tags inside an <article> or main list
    # Pattern: links like /series/Neon_Genesis_Evangelion-112159/season-1/episode-1-...
    pattern = re.compile(
        r'<a[^>]+href="(/series/[^"]*?/season-\d+/episode-(\d+)-([^"]*?))"[^>]*>',
        re.IGNORECASE,
    )
    for match in pattern.finditer(html):
        path, ep_num_str, slug = match.groups()
        ep_num = int(ep_num_str)
        # Convert slug to readable title: "angels-attack" -> "Angel's Attack"
        title = slug.replace("-", " ").title()
        url = base_url.split("/series/")[0] + path
        episodes.append({
            "url": url,
            "episode_number": ep_num,
            "episode_title": title,
        })

    # Deduplicate by episode number, keeping first occurrence
    seen = set()
    unique = []
    for ep in episodes:
        if ep["episode_number"] not in seen:
            seen.add(ep["episode_number"])
            unique.append(ep)
    return sorted(unique, key=lambda e: e["episode_number"])


def _extract_transcript(html: str) -> str:
    """Extract the transcript text from an episode page.

    subslikescript stores the transcript inside a <div class="full-script">.
    """
    # Primary: look for the full-script div
    match = re.search(
        r'<div[^>]*class="full-script"[^>]*>(.*?)</div>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        raw = match.group(1)
    else:
        # Fallback: look for article content
        for tag_pattern in [
            r'<article[^>]*>(.*?)</article>',
            r'<div[^>]*class="main-content"[^>]*>(.*?)</div>',
        ]:
            match = re.search(tag_pattern, html, re.DOTALL | re.IGNORECASE)
            if match:
                raw = match.group(1)
                break
        else:
            # Last resort: body
            body = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
            raw = body.group(1) if body else html

    # Strip HTML tags
    text = re.sub(r'<br\s*/?>', '\n', raw, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode common entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    # Normalize whitespace within lines, preserve paragraph breaks
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in text.split('\n')]
    text = '\n'.join(line for line in lines if line)
    return text.strip()


def fetch_series_transcripts(
    series_url: str,
    output_dir: str,
    rate_limit: float = 2.0,
    session: Optional[httpx.Client] = None,
) -> list[dict]:
    """Scrape all episode transcripts for a series and save as JSON articles.

    Args:
        series_url: The subslikescript series page URL.
        output_dir: Directory to write JSON article files.
        rate_limit: Seconds to wait between HTTP requests.
        session: Optional httpx.Client; one is created if not provided.

    Returns:
        List of article dicts that were saved.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    session = session or httpx.Client(
        timeout=30.0, follow_redirects=True, headers=HEADERS,
    )
    articles: list[dict] = []

    # Step 1: fetch the series index page to discover episode links
    print(f"Fetching series index: {series_url}")
    resp = session.get(series_url)
    resp.raise_for_status()
    episodes = _extract_episode_links(resp.text, series_url)
    print(f"Found {len(episodes)} episodes")

    if not episodes:
        print("No episode links found. The page structure may have changed.")
        return articles

    time.sleep(rate_limit)

    # Step 2: fetch each episode transcript
    for ep in episodes:
        ep_num = ep["episode_number"]
        ep_title = ep["episode_title"]
        slug = re.sub(r'[^a-zA-Z0-9_]', '_', f"ep{ep_num:02d}_{ep_title}")[:80]
        outfile = output_path / f"transcript_{slug}.json"

        # Skip existing (resumable)
        if outfile.exists():
            print(f"  Skipping Episode {ep_num} (already exists)")
            continue

        print(f"  Fetching Episode {ep_num}: {ep_title}...")
        try:
            resp = session.get(ep["url"])
            resp.raise_for_status()
            transcript_text = _extract_transcript(resp.text)

            if len(transcript_text) < 100:
                print(f"    Skipping (too short: {len(transcript_text)} chars)")
                continue

            article = {
                "page_id": PAGE_ID_BASE + ep_num,
                "slug": f"transcript_{slug}",
                "title": f"Episode {ep_num} Transcript: {ep_title}",
                "display_title": f"Episode {ep_num} Transcript: {ep_title}",
                "namespace": 0,
                "content_model": "transcript",
                "language": "en",
                "wikitext": transcript_text,
                "html": "",
                "summary": transcript_text[:500],
                "sections": [],
                "categories": ["Transcripts", f"Episode {ep_num}"],
                "infobox": {
                    "episode_number": ep_num,
                    "source": ep["url"],
                },
                "templates": [],
                "internal_links": [],
                "external_links": [ep["url"]],
                "iw_links": [],
                "lang_links": [],
                "properties": {},
                "protection": [],
                "rev_id": None,
                "length_bytes": len(transcript_text),
                "parse_warnings": [],
                "touched_at": None,
                "references": [],
                "source_type": "transcript",
                "source_url": ep["url"],
                "authority": 60,
            }

            # Generate chunks
            chunks = chunk_article(article)
            article["chunks"] = chunks

            outfile.write_text(json.dumps(article, ensure_ascii=False, default=str))
            articles.append(article)
            print(f"    Saved ({len(transcript_text)} chars, {len(chunks)} chunks)")

        except Exception as e:
            print(f"    Error: {e}")

        time.sleep(rate_limit)

    print(f"Total: {len(articles)} transcripts saved to {output_path}")
    return articles


def run_transcript_fetch(
    output_dir: str,
    series_url: str = DEFAULT_SERIES_URL,
    rate_limit: float = 2.0,
) -> list[dict]:
    """Entry point: fetch all Evangelion episode transcripts.

    Args:
        output_dir: Directory to write JSON article files.
        series_url: The subslikescript series page URL.
        rate_limit: Seconds to wait between HTTP requests.

    Returns:
        List of article dicts that were saved.
    """
    return fetch_series_transcripts(
        series_url=series_url,
        output_dir=output_dir,
        rate_limit=rate_limit,
    )
