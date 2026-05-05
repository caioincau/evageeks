# fetcher/academic.py
"""Fetch and extract text from open-access academic papers about Evangelion."""
import json
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from parser.chunker import chunk_article

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

PAGE_ID_BASE = 14_000_000

PAPERS = [
    {
        "url": "https://iopn.library.illinois.edu/journals/jams/article/download/822/730/2959",
        "title": "A Deleuzo-Guattarian Critique of Neon Genesis Evangelion",
        "authors": "Various",
        "year": 2021,
    },
    {
        "url": "https://zenodo.org/records/15576803/files/article.pdf",
        "title": "Exploring the Human Psyche in Neon Genesis Evangelion",
        "authors": "Various",
        "year": 2025,
    },
    {
        "url": "https://soar.suny.edu/bitstream/handle/20.500.12648/1589/Carpentieri_Thesis.pdf",
        "title": "Hand to God: Neon Genesis Evangelion's Gnostic Gospel",
        "authors": "Carpentieri",
        "year": 2019,
    },
]

# Topic areas inferred from paper titles for categorisation
_TOPIC_KEYWORDS = {
    "psyche": "Psychology",
    "psycho": "Psychology",
    "gnostic": "Religion",
    "gospel": "Religion",
    "deleuze": "Philosophy",
    "guattari": "Philosophy",
    "critique": "Critical Theory",
    "gender": "Gender Studies",
    "feminist": "Gender Studies",
    "cultural": "Cultural Studies",
    "music": "Music",
    "visual": "Visual Analysis",
}


def _infer_topic(title: str) -> str:
    """Infer a broad topic area from a paper title."""
    lower = title.lower()
    for keyword, topic in _TOPIC_KEYWORDS.items():
        if keyword in lower:
            return topic
    return "Evangelion Studies"


def _extract_text_from_pdf_bytes(pdf_bytes: bytes, paper_title: str) -> Optional[str]:
    """Try to extract text from PDF bytes using available libraries.

    Tries PyMuPDF (fitz) first, then pdfplumber. Returns None if no PDF
    library is installed.
    """
    # Attempt 1: PyMuPDF (fitz)
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = []
        for page in doc:
            pages.append(page.get_text())
        doc.close()
        text = "\n\n".join(pages)
        if text.strip():
            return text.strip()
    except ImportError:
        pass
    except Exception as e:
        print(f"    PyMuPDF extraction failed for '{paper_title}': {e}")

    # Attempt 2: pdfplumber
    try:
        import io
        import pdfplumber

        pages = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    pages.append(page_text)
        text = "\n\n".join(pages)
        if text.strip():
            return text.strip()
    except ImportError:
        pass
    except Exception as e:
        print(f"    pdfplumber extraction failed for '{paper_title}': {e}")

    return None


def _make_reference_text(paper: dict) -> str:
    """Create a reference-only article body when PDF extraction is unavailable."""
    return (
        f"Title: {paper['title']}\n"
        f"Authors: {paper['authors']}\n"
        f"Year: {paper['year']}\n"
        f"URL: {paper['url']}\n\n"
        "Full text could not be extracted. "
        "This entry serves as a reference to the original open-access paper. "
        "Please consult the URL above for the complete text."
    )


def _normalise_extracted_text(text: str) -> str:
    """Clean up common PDF-extraction artefacts."""
    # Collapse runs of whitespace within lines
    text = re.sub(r'[ \t]+', ' ', text)
    # Normalise line breaks (remove single newlines inside paragraphs)
    text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)
    # Collapse multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def fetch_paper(
    paper: dict,
    session: Optional[httpx.Client] = None,
) -> tuple[str, bool]:
    """Download a paper and extract its text.

    Returns:
        (text, is_full_text) — the extracted text and whether it is the
        full content or just a reference stub.
    """
    session = session or httpx.Client(timeout=60.0, follow_redirects=True, headers=HEADERS)
    resp = session.get(paper["url"])
    resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")

    # If the response is a PDF, try extraction
    if "application/pdf" in content_type or paper["url"].lower().endswith(".pdf"):
        extracted = _extract_text_from_pdf_bytes(resp.content, paper["title"])
        if extracted:
            return _normalise_extracted_text(extracted), True
        print(f"    No PDF library available; creating reference article")
        return _make_reference_text(paper), False

    # Some repositories serve HTML landing pages; try to read as text
    if "text/html" in content_type:
        # Strip HTML tags for a rough extraction
        text = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', resp.text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) > 500:
            return text, True
        return _make_reference_text(paper), False

    # Plain-text response
    if resp.text and len(resp.text.strip()) > 200:
        return resp.text.strip(), True

    return _make_reference_text(paper), False


def run_academic_fetch(
    output_dir: str,
    rate_limit: float = 3.0,
) -> list[dict]:
    """Fetch open-access academic papers and save as JSON articles for ingestion.

    Args:
        output_dir: Directory to write JSON article files.
        rate_limit: Seconds to wait between HTTP requests.

    Returns:
        List of article dicts that were saved.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    session = httpx.Client(timeout=60.0, follow_redirects=True, headers=HEADERS)
    articles: list[dict] = []

    for i, paper in enumerate(PAPERS):
        slug = re.sub(r'[^a-zA-Z0-9_]', '_', paper["title"])[:80]
        outfile = output_path / f"academic_{slug}.json"

        # Skip existing (resumable)
        if outfile.exists():
            print(f"  Skipping '{paper['title']}' (already exists)")
            continue

        print(f"Fetching: {paper['title']}...")
        try:
            text, is_full_text = fetch_paper(paper, session=session)

            if is_full_text and len(text) < 200:
                print(f"  Skipping (too short: {len(text)} chars)")
                continue

            topic = _infer_topic(paper["title"])

            article = {
                "page_id": PAGE_ID_BASE + i,
                "slug": f"academic_{slug}",
                "title": paper["title"],
                "display_title": paper["title"],
                "namespace": 0,
                "content_model": "academic",
                "language": "en",
                "wikitext": text,
                "html": "",
                "summary": text[:500],
                "sections": [],
                "categories": ["Academic Papers", topic],
                "infobox": {
                    "authors": paper["authors"],
                    "year": paper["year"],
                    "url": paper["url"],
                },
                "templates": [],
                "internal_links": [],
                "external_links": [paper["url"]],
                "iw_links": [],
                "lang_links": [],
                "properties": {},
                "protection": [],
                "rev_id": None,
                "length_bytes": len(text),
                "parse_warnings": [],
                "touched_at": None,
                "references": [],
                "source_type": "academic",
                "source_url": paper["url"],
                "authority": 85,
            }

            # Generate chunks
            chunks = chunk_article(article)
            article["chunks"] = chunks

            outfile.write_text(json.dumps(article, ensure_ascii=False, default=str))
            articles.append(article)
            label = "full text" if is_full_text else "reference only"
            print(f"  Saved ({len(text)} chars, {len(chunks)} chunks, {label})")

        except Exception as e:
            print(f"  Error: {e}")

        time.sleep(rate_limit)

    print(f"Total: {len(articles)} academic papers saved to {output_path}")
    return articles
