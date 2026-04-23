# fetcher/forum.py
"""Scrape EvaGeeks forum for analysis and discussion threads."""
import re
import time
from pathlib import Path
from typing import Optional
import httpx
from html.parser import HTMLParser


FORUM_URL = "https://forum.evageeks.org"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def _make_session() -> httpx.Client:
    return httpx.Client(timeout=60.0, follow_redirects=True, headers=_HEADERS)

# High-value subforums
SUBFORUMS = {
    "evangelion-tv-eoe": 4,
    "rebuild-discussion": 14,
    "evangelion-general": 3,
    "everything-else-eva": 7,
}


class _PostParser(HTMLParser):
    """Extract post text from phpBB HTML."""

    def __init__(self):
        super().__init__()
        self._in_post = False
        self._depth = 0
        self._text = []
        self.posts = []
        self._current_author = ""
        self._current_date = ""
        self._in_author = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")
        if "postbody" in cls or "content" in cls:
            self._in_post = True
            self._depth = 0
            self._text = []
        if self._in_post:
            self._depth += 1
        if "author" in cls or "username" in cls:
            self._in_author = True

    def handle_endtag(self, tag):
        if self._in_post:
            self._depth -= 1
            if self._depth <= 0:
                text = " ".join(self._text).strip()
                text = re.sub(r'\s+', ' ', text)
                if len(text) > 50:
                    self.posts.append({
                        "author": self._current_author,
                        "content": text,
                    })
                self._in_post = False
                self._text = []
        if self._in_author:
            self._in_author = False

    def handle_data(self, data):
        if self._in_post:
            self._text.append(data.strip())
        if self._in_author:
            self._current_author = data.strip()


def fetch_thread_list(
    subforum_id: int,
    max_pages: int = 5,
    session: Optional[httpx.Client] = None,
    rate_limit: float = 1.0,
) -> list[dict]:
    """Fetch thread URLs from a subforum."""
    session = session or _make_session()
    threads = []

    for page in range(max_pages):
        offset = page * 25
        url = f"{FORUM_URL}/viewforum.php?f={subforum_id}&start={offset}"
        try:
            resp = session.get(url)
            resp.raise_for_status()
            html = resp.text

            # Extract thread links: ./thread/ID/slug/ format
            for match in re.finditer(r'\./thread/(\d+)/([^/]+)/', html):
                tid = match.group(1)
                slug = match.group(2)
                title = slug.replace("-", " ")
                threads.append({
                    "thread_id": int(tid),
                    "title": title,
                    "url": f"{FORUM_URL}/thread/{tid}/{slug}/",
                })

            # Stop if no more threads found on this page
            if f"start={offset + 25}" not in html and page > 0:
                break

        except Exception as e:
            print(f"  Error fetching subforum page {page}: {e}")

        time.sleep(rate_limit)

    # Deduplicate by thread_id
    seen = set()
    unique = []
    for t in threads:
        if t["thread_id"] not in seen:
            seen.add(t["thread_id"])
            unique.append(t)

    return unique


def fetch_thread_posts(
    thread_url: str,
    session: Optional[httpx.Client] = None,
    rate_limit: float = 2.0,
    retries: int = 3,
) -> list[dict]:
    """Fetch all posts from a forum thread using regex extraction."""
    session = session or _make_session()

    for attempt in range(retries):
        try:
            resp = session.get(thread_url)
            if resp.status_code == 522:
                wait = rate_limit * (2 ** attempt) + 5
                print(f"    522 rate limit, waiting {wait:.0f}s (attempt {attempt + 1}/{retries})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            html = resp.text
            break
        except Exception as e:
            if attempt < retries - 1:
                wait = rate_limit * (2 ** attempt) + 3
                time.sleep(wait)
                continue
            print(f"  Error fetching thread {thread_url}: {e}")
            return []

    # Extract post content blocks using common phpBB patterns
    posts = []
    # Try postbody/content divs
    for match in re.finditer(r'<div[^>]*class="[^"]*(?:postbody|content)[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL):
        text = re.sub(r'<[^>]+>', ' ', match.group(1))
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) > 50:
            posts.append({"author": "", "content": text})

    # Fallback: extract paragraphs from the page if no postbody found
    if not posts:
        for match in re.finditer(r'<p[^>]*>(.*?)</p>', html, re.DOTALL):
            text = re.sub(r'<[^>]+>', ' ', match.group(1))
            text = re.sub(r'\s+', ' ', text).strip()
            if len(text) > 100:
                posts.append({"author": "", "content": text})

    time.sleep(rate_limit)
    return posts


def run_forum_fetch(
    output_dir: str,
    max_threads_per_subforum: int = 9999,
    rate_limit: float = 2.0,
) -> list[dict]:
    """Fetch forum threads and save as JSON articles for ingestion."""
    import json

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    session = _make_session()
    all_articles = []

    for subforum_name, subforum_id in SUBFORUMS.items():
        print(f"Fetching {subforum_name} (id={subforum_id})...")
        threads = fetch_thread_list(subforum_id, max_pages=200, session=session, rate_limit=max(rate_limit, 3.0))
        print(f"  Found {len(threads)} threads")

        skipped = 0
        for i, thread in enumerate(threads[:max_threads_per_subforum]):
            slug = f"forum_{subforum_name}_{thread['thread_id']}"
            outfile = output_path / f"{slug}.json"
            if outfile.exists():
                skipped += 1
                continue

            posts = fetch_thread_posts(thread["url"], session=session, rate_limit=rate_limit)
            if not posts:
                continue

            # Combine posts into article-like structure
            combined_content = []
            for post in posts:
                author = post.get("author", "Anonymous")
                combined_content.append(f"[{author}]: {post['content']}")

            wikitext = "\n\n".join(combined_content)
            slug = f"forum_{subforum_name}_{thread['thread_id']}"

            article = {
                "page_id": 1000000 + thread["thread_id"],
                "slug": slug,
                "title": thread["title"],
                "display_title": thread["title"],
                "namespace": 0,
                "content_model": "forum",
                "language": "en",
                "wikitext": wikitext,
                "html": "",
                "summary": wikitext[:500],
                "sections": [],
                "categories": [f"Forum: {subforum_name}"],
                "infobox": {},
                "templates": [],
                "internal_links": [],
                "external_links": [],
                "iw_links": [],
                "lang_links": [],
                "properties": {},
                "protection": [],
                "rev_id": None,
                "length_bytes": len(wikitext),
                "parse_warnings": [],
                "touched_at": None,
                "references": [],
                "source_type": "forum",
                "source_url": thread["url"],
            }

            outfile = output_path / f"{slug}.json"
            outfile.write_text(json.dumps(article, ensure_ascii=False, default=str))
            all_articles.append(article)

            if (i + 1 - skipped) % 10 == 0 and (i + 1 - skipped) > 0:
                print(f"  Fetched {i + 1 - skipped} new / {skipped} skipped / {len(threads)} total")

        if skipped:
            print(f"  Skipped {skipped} already downloaded threads")

    print(f"Total: {len(all_articles)} new forum articles saved to {output_path}")
    return all_articles
