# fetcher/export.py
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
import httpx
import yaml


def _load_config() -> dict:
    with open(Path(__file__).parent.parent / "config.yaml") as f:
        return yaml.safe_load(f)


def fetch_page_list(
    wiki_url: str,
    namespace: int = 0,
    session: Optional[httpx.Client] = None,
    rate_limit: float = 0.5,
) -> list:
    """Return all pages in a namespace via api.php allpages."""
    session = session or httpx.Client()
    pages = []
    params = {
        "action": "query",
        "list": "allpages",
        "apnamespace": namespace,
        "aplimit": "500",
        "format": "json",
    }
    while True:
        resp = session.get(f"{wiki_url}/api.php", params=params)
        resp.raise_for_status()
        data = resp.json()
        pages.extend(data["query"]["allpages"])
        if "continue" not in data:
            break
        params.update(data["continue"])
        time.sleep(rate_limit)
    return pages


def fetch_xml_batch(
    wiki_url: str,
    titles: list,
    session: Optional[httpx.Client] = None,
    rate_limit: float = 0.5,
) -> bytes:
    """Export a batch of articles as MediaWiki XML via Special:Export."""
    session = session or httpx.Client()
    resp = session.post(
        f"{wiki_url}/index.php",
        data={
            "title": "Special:Export",
            "action": "submit",
            "pages": "\n".join(titles),
            "curonly": "1",
        },
    )
    resp.raise_for_status()
    time.sleep(rate_limit)
    return resp.content


def run_fetch(wiki_url: str, output_dir: str, batch_size: int = 500) -> None:
    """
    Main fetch entry point. Downloads all article XML to output_dir/articles.xml.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    session = httpx.Client(timeout=30.0)
    config = _load_config()
    rate_limit = config.get("rate_limit_delay", 0.5)

    print("Fetching page list...")
    pages = fetch_page_list(wiki_url, session=session, rate_limit=rate_limit)
    print(f"Found {len(pages)} pages")

    all_xml_parts = []
    for i in range(0, len(pages), batch_size):
        batch = pages[i : i + batch_size]
        titles = [p["title"] for p in batch]
        print(f"Fetching batch {i // batch_size + 1} ({len(titles)} articles)...")
        try:
            xml_bytes = fetch_xml_batch(wiki_url, titles, session, rate_limit)
            all_xml_parts.append(xml_bytes)
        except Exception as e:
            error_file = output_path.parent / "errors" / f"batch_{i}.txt"
            error_file.parent.mkdir(exist_ok=True)
            error_file.write_text(str(e))
            print(f"  Error in batch {i}: {e}")

    NS = "http://www.mediawiki.org/xml/export-0.11/"
    merged_path = output_path / "articles.xml"
    with open(merged_path, "wb") as f:
        f.write(b"<mediawiki>\n")
        for part in all_xml_parts:
            try:
                root = ET.fromstring(part)
                for page in root.findall(f"{{{NS}}}page"):
                    f.write(ET.tostring(page))
            except ET.ParseError as e:
                print(f"  Warning: failed to parse XML batch: {e}")
        f.write(b"</mediawiki>")
    print(f"Saved to {merged_path}")
