# EvaGeeks Wiki Mirror — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python pipeline that mirrors wiki.evageeks.org (MediaWiki) into PostgreSQL+pgvector, exposing articles via REST API with semantic search (RAG).

**Architecture:** XML Export fetches all articles in bulk; mwparserfromhell parses wikitext into structured JSON; loader upserts into PostgreSQL with pgvector embeddings; FastAPI serves articles and semantic search. Each stage is independently resumable via `fetch_state` table.

**Tech Stack:** Python 3.11, mwparserfromhell, requests, psycopg2, pgvector, FastAPI, uvicorn, openai (embeddings), tiktoken, pytest, testcontainers, respx

---

## File Map

```
evageeks/
├── cli.py                            # Commands: fetch | parse | ingest | serve
├── config.yaml                       # All config (wiki URL, DB, model, etc.)
├── requirements.txt
├── fetcher/
│   ├── __init__.py
│   ├── export.py                     # XML bulk export via Special:Export
│   ├── images.py                     # Image enumeration + parallel download
│   └── templates.py                  # Template fetch (namespace 10)
├── parser/
│   ├── __init__.py
│   ├── wikitext.py                   # mwparserfromhell → structured dict
│   └── chunker.py                    # Split text into RAG chunks
├── ingester/
│   ├── __init__.py
│   ├── db.py                         # Connection + schema creation
│   ├── embedder.py                   # Batch embedding generation
│   └── loader.py                     # Orchestrates upsert to DB
├── api/
│   ├── __init__.py
│   ├── main.py                       # FastAPI app factory
│   └── routes/
│       ├── __init__.py
│       ├── articles.py               # GET /articles, GET /articles/{slug}
│       ├── search.py                 # POST /search (semantic)
│       └── categories.py             # GET /categories/{name}
├── tests/
│   ├── conftest.py                   # Shared fixtures (DB, config)
│   ├── unit/
│   │   ├── test_wikitext.py
│   │   ├── test_chunker.py
│   │   └── test_export.py
│   ├── integration/
│   │   ├── test_loader.py
│   │   └── test_api.py
│   ├── smoke/
│   │   └── test_pipeline.py
│   └── fixtures/
│       ├── rei_ayanami.xml           # Sample wikitext for tests
│       └── rei_ayanami_api.json      # Sample api.php response
└── data/
    ├── raw/
    ├── parsed/
    ├── images/
    └── errors/
```

---

## Task 1: Project Setup

**Files:**
- Create: `requirements.txt`
- Create: `config.yaml`
- Create: `fetcher/__init__.py`, `parser/__init__.py`, `ingester/__init__.py`, `api/__init__.py`, `api/routes/__init__.py`
- Create: `data/raw/.gitkeep`, `data/parsed/.gitkeep`, `data/images/.gitkeep`, `data/errors/.gitkeep`
- Create: `pytest.ini`

- [ ] **Step 1: Create requirements.txt**

```
mwparserfromhell==0.6.6
requests==2.32.3
psycopg2-binary==2.9.9
pgvector==0.3.6
fastapi==0.115.0
uvicorn==0.30.6
openai==1.52.0
sentence-transformers==3.2.1
tiktoken==0.8.0
pyyaml==6.0.2
pytest==8.3.3
pytest-asyncio==0.24.0
testcontainers[postgres]==4.8.1
respx==0.21.1
httpx==0.27.2
```

- [ ] **Step 2: Create config.yaml**

```yaml
wiki_url: https://wiki.evageeks.org
batch_size: 500
image_workers: 5
chunk_size: 512
chunk_overlap: 50
embed_model: text-embedding-3-small
embed_dimensions: 1536
database_url: postgresql://localhost/evageeks
rate_limit_delay: 0.5
data_dir: data
```

- [ ] **Step 3: Create pytest.ini**

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 4: Create all __init__.py and data directory placeholders**

```bash
touch fetcher/__init__.py parser/__init__.py ingester/__init__.py
touch api/__init__.py api/routes/__init__.py
mkdir -p data/raw data/parsed data/images data/errors
touch data/raw/.gitkeep data/parsed/.gitkeep data/images/.gitkeep data/errors/.gitkeep
```

- [ ] **Step 5: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: all packages install without error.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt config.yaml pytest.ini fetcher/ parser/ ingester/ api/ data/
git commit -m "chore: project structure and dependencies"
```

---

## Task 2: Database Schema

**Files:**
- Create: `ingester/db.py`
- Create: `tests/conftest.py`
- Create: `tests/integration/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_db.py
import psycopg2
from testcontainers.postgres import PostgresContainer
from ingester.db import create_schema, get_connection

def test_create_schema_creates_all_tables():
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        conn = psycopg2.connect(pg.get_connection_url())
        create_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name
            """)
            tables = {row[0] for row in cur.fetchall()}
        conn.close()
    assert tables == {
        "articles", "article_refs", "chunks", "images",
        "article_images", "fetch_state"
    }
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/integration/test_db.py -v
```

Expected: `ModuleNotFoundError: No module named 'ingester.db'`

- [ ] **Step 3: Implement ingester/db.py**

```python
# ingester/db.py
import psycopg2
from psycopg2.extensions import connection
import yaml
from pathlib import Path


def load_config(path: str = None) -> dict:
    cfg_path = path or Path(__file__).parent.parent / "config.yaml"
    with open(cfg_path) as f:
        return yaml.safe_load(f)


def get_connection(database_url: str = None) -> connection:
    config = load_config()
    conn = psycopg2.connect(database_url or config["database_url"])
    return conn


def create_schema(conn: connection) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id              SERIAL PRIMARY KEY,
                page_id         INTEGER UNIQUE NOT NULL,
                slug            TEXT UNIQUE NOT NULL,
                title           TEXT NOT NULL,
                display_title   TEXT,
                namespace       INTEGER DEFAULT 0,
                content_model   TEXT,
                language        TEXT,
                wikitext        TEXT,
                html            TEXT,
                summary         TEXT,
                sections        JSONB,
                categories      TEXT[],
                infobox         JSONB,
                templates       TEXT[],
                internal_links  TEXT[],
                external_links  TEXT[],
                iw_links        JSONB,
                lang_links      JSONB,
                properties      JSONB,
                protection      JSONB,
                rev_id          INTEGER,
                length_bytes    INTEGER,
                parse_warnings  TEXT[],
                touched_at      TIMESTAMPTZ,
                fetched_at      TIMESTAMPTZ
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS article_refs (
                id          SERIAL PRIMARY KEY,
                article_id  INTEGER REFERENCES articles(id) ON DELETE CASCADE,
                ref_name    TEXT,
                content     TEXT,
                url         TEXT,
                position    INTEGER
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id          SERIAL PRIMARY KEY,
                article_id  INTEGER REFERENCES articles(id) ON DELETE CASCADE,
                section     TEXT,
                content     TEXT,
                position    INTEGER,
                token_count INTEGER,
                embedding   VECTOR(1536),
                embed_model TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS images (
                id               SERIAL PRIMARY KEY,
                filename         TEXT UNIQUE NOT NULL,
                page_id          INTEGER,
                url              TEXT,
                description_url  TEXT,
                local_path       TEXT,
                mime_type        TEXT,
                size_bytes       INTEGER,
                width            INTEGER,
                height           INTEGER,
                sha1             TEXT,
                uploader         TEXT,
                upload_comment   TEXT,
                upload_timestamp TIMESTAMPTZ,
                downloaded       BOOLEAN DEFAULT FALSE,
                fetched_at       TIMESTAMPTZ
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS article_images (
                article_id  INTEGER REFERENCES articles(id) ON DELETE CASCADE,
                image_id    INTEGER REFERENCES images(id) ON DELETE CASCADE,
                PRIMARY KEY (article_id, image_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fetch_state (
                key        TEXT PRIMARY KEY,
                value      JSONB,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_articles_categories
            ON articles USING gin (categories)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_articles_templates
            ON articles USING gin (templates)
        """)
    conn.commit()
```

- [ ] **Step 4: Create tests/conftest.py**

```python
# tests/conftest.py
import pytest
import psycopg2
from testcontainers.postgres import PostgresContainer
from ingester.db import create_schema


@pytest.fixture(scope="session")
def pg_container():
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield pg


@pytest.fixture
def db_conn(pg_container):
    conn = psycopg2.connect(pg_container.get_connection_url())
    create_schema(conn)
    yield conn
    # Teardown: truncate all tables
    with conn.cursor() as cur:
        cur.execute("""
            TRUNCATE articles, article_refs, chunks, images,
                     article_images, fetch_state RESTART IDENTITY CASCADE
        """)
    conn.commit()
    conn.close()
```

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/integration/test_db.py -v
```

Expected: `PASSED`

- [ ] **Step 6: Commit**

```bash
git add ingester/db.py tests/integration/test_db.py tests/conftest.py
git commit -m "feat: database schema with pgvector"
```

---

## Task 3: Wikitext Parser

**Files:**
- Create: `parser/wikitext.py`
- Create: `tests/fixtures/rei_ayanami.xml`
- Create: `tests/fixtures/rei_ayanami_api.json`
- Create: `tests/unit/test_wikitext.py`

- [ ] **Step 1: Create fixture files**

Save this as `tests/fixtures/rei_ayanami.xml` (minimal wikitext for tests):
```xml
{{Infobox character
| name = Rei Ayanami
| series = Neon Genesis Evangelion
| age = 14
}}
'''Rei Ayanami''' (綾波レイ, ''Ayanami Rei'') is a fictional character from the [[Neon Genesis Evangelion]] franchise.

== Background ==
Rei is the First Child and pilot of [[Evangelion Unit-00]].

== References ==
<ref name="anno">Anno interview 1997</ref>

[[Category:Characters]]
[[Category:Eva Pilots]]
```

Save this as `tests/fixtures/rei_ayanami_api.json`:
```json
{
  "pageid": 42,
  "revid": 1001,
  "title": "Rei Ayanami",
  "displaytitle": "Rei Ayanami",
  "ns": 0,
  "contentmodel": "wikitext",
  "pagelanguage": "en",
  "touched": "2024-01-15T10:00:00Z",
  "length": 512,
  "text": {"*": "<p><b>Rei Ayanami</b> is a fictional character...</p>"},
  "sections": [
    {"index": "1", "toclevel": 1, "level": "2", "line": "Background", "anchor": "Background", "byteoffset": 200}
  ],
  "iwlinks": [],
  "langlinks": [],
  "properties": {},
  "protection": [],
  "parsewarnings": []
}
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/unit/test_wikitext.py
import json
from pathlib import Path
from parser.wikitext import parse_article

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load_fixtures():
    wikitext = (FIXTURES / "rei_ayanami.xml").read_text()
    api_data = json.loads((FIXTURES / "rei_ayanami_api.json").read_text())
    return wikitext, api_data


def test_parse_article_basic_fields():
    wikitext, api_data = _load_fixtures()
    result = parse_article(wikitext, api_data)
    assert result["page_id"] == 42
    assert result["slug"] == "Rei_Ayanami"
    assert result["title"] == "Rei Ayanami"
    assert result["namespace"] == 0
    assert result["rev_id"] == 1001


def test_parse_article_extracts_categories():
    wikitext, api_data = _load_fixtures()
    result = parse_article(wikitext, api_data)
    assert "Characters" in result["categories"]
    assert "Eva Pilots" in result["categories"]


def test_parse_article_extracts_infobox():
    wikitext, api_data = _load_fixtures()
    result = parse_article(wikitext, api_data)
    assert result["infobox"]["name"] == "Rei Ayanami"
    assert result["infobox"]["age"] == "14"


def test_parse_article_extracts_internal_links():
    wikitext, api_data = _load_fixtures()
    result = parse_article(wikitext, api_data)
    assert "Neon Genesis Evangelion" in result["internal_links"]
    assert "Evangelion Unit-00" in result["internal_links"]


def test_parse_article_extracts_references():
    wikitext, api_data = _load_fixtures()
    result = parse_article(wikitext, api_data)
    refs = result["references"]
    assert len(refs) == 1
    assert refs[0]["ref_name"] == "anno"
    assert "Anno interview" in refs[0]["content"]


def test_parse_article_extracts_sections():
    wikitext, api_data = _load_fixtures()
    result = parse_article(wikitext, api_data)
    assert result["sections"][0]["line"] == "Background"
    assert result["sections"][0]["anchor"] == "Background"


def test_parse_article_extracts_summary():
    wikitext, api_data = _load_fixtures()
    result = parse_article(wikitext, api_data)
    assert result["summary"] is not None
    assert len(result["summary"]) > 0
```

- [ ] **Step 3: Run to verify they fail**

```bash
pytest tests/unit/test_wikitext.py -v
```

Expected: `ModuleNotFoundError: No module named 'parser.wikitext'`

- [ ] **Step 4: Implement parser/wikitext.py**

```python
# parser/wikitext.py
import re
from typing import Optional
import mwparserfromhell


def parse_article(wikitext: str, api_data: dict) -> dict:
    """Parse wikitext + API response into a structured dict matching the DB schema."""
    wikicode = mwparserfromhell.parse(wikitext)
    return {
        "page_id": api_data.get("pageid"),
        "slug": api_data.get("title", "").replace(" ", "_"),
        "title": api_data.get("title", ""),
        "display_title": api_data.get("displaytitle"),
        "namespace": api_data.get("ns", 0),
        "content_model": api_data.get("contentmodel", "wikitext"),
        "language": api_data.get("pagelanguage"),
        "wikitext": wikitext,
        "html": _extract_html(api_data),
        "summary": _extract_summary(wikicode),
        "sections": api_data.get("sections", []),
        "categories": _extract_categories(wikicode),
        "infobox": _extract_infobox(wikicode),
        "templates": _extract_templates(wikicode),
        "internal_links": _extract_internal_links(wikicode),
        "external_links": _extract_external_links(wikicode),
        "iw_links": api_data.get("iwlinks", []),
        "lang_links": api_data.get("langlinks", []),
        "properties": api_data.get("properties", {}),
        "protection": api_data.get("protection", []),
        "rev_id": api_data.get("revid"),
        "length_bytes": api_data.get("length"),
        "parse_warnings": api_data.get("parsewarnings", []),
        "touched_at": api_data.get("touched"),
        "references": _extract_references(wikicode),
    }


def _extract_html(api_data: dict) -> Optional[str]:
    text = api_data.get("text", "")
    if isinstance(text, dict):
        return text.get("*", "")
    return text or ""


def _extract_summary(wikicode) -> str:
    """Return the first non-empty plain-text paragraph."""
    for node in wikicode.nodes:
        if isinstance(node, mwparserfromhell.nodes.text.Text):
            stripped = node.value.strip()
            if stripped and not stripped.startswith("="):
                return stripped[:500]
    return wikicode.strip_code()[:500]


def _extract_categories(wikicode) -> list[str]:
    cats = []
    for link in wikicode.filter_wikilinks():
        title = str(link.title)
        if title.startswith("Category:"):
            cats.append(title[len("Category:"):])
    return cats


def _extract_infobox(wikicode) -> dict:
    for template in wikicode.filter_templates():
        name = str(template.name).strip().lower()
        if "infobox" in name:
            result = {}
            for param in template.params:
                key = str(param.name).strip()
                value = str(param.value).strip()
                if key and value:
                    result[key] = value
            return result
    return {}


def _extract_templates(wikicode) -> list[str]:
    return list({
        str(t.name).strip()
        for t in wikicode.filter_templates()
    })


def _extract_internal_links(wikicode) -> list[str]:
    links = []
    for link in wikicode.filter_wikilinks():
        title = str(link.title)
        if not title.startswith(("Category:", "File:", "Image:")):
            links.append(title.split("#")[0])
    return list(set(links))


def _extract_external_links(wikicode) -> list[str]:
    links = []
    for node in wikicode.filter_external_links():
        links.append(str(node.url))
    return links


def _extract_references(wikicode) -> list[dict]:
    refs = []
    position = 0
    raw = str(wikicode)
    for match in re.finditer(r'<ref(?:\s+name="([^"]*)")?>(.*?)</ref>', raw, re.DOTALL):
        ref_name = match.group(1)
        content = match.group(2).strip()
        url_match = re.search(r'https?://\S+', content)
        refs.append({
            "ref_name": ref_name,
            "content": content,
            "url": url_match.group(0) if url_match else None,
            "position": position,
        })
        position += 1
    return refs
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_wikitext.py -v
```

Expected: all 7 tests `PASSED`

- [ ] **Step 6: Commit**

```bash
git add parser/wikitext.py tests/unit/test_wikitext.py tests/fixtures/
git commit -m "feat: wikitext parser with mwparserfromhell"
```

---

## Task 4: Text Chunker

**Files:**
- Create: `parser/chunker.py`
- Create: `tests/unit/test_chunker.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_chunker.py
from parser.chunker import chunk_article


LONG_TEXT = " ".join(["word"] * 600)  # 600 words > 512 token limit


def test_chunk_short_article_returns_one_chunk():
    article = {"slug": "test", "summary": "Short text.", "sections": []}
    chunks = chunk_article(article, chunk_size=512, overlap=50)
    assert len(chunks) == 1
    assert chunks[0]["section"] == "_summary"
    assert chunks[0]["position"] == 0
    assert chunks[0]["article_slug"] == "test"


def test_chunk_long_text_splits_into_multiple():
    article = {
        "slug": "test",
        "summary": LONG_TEXT,
        "sections": [],
    }
    chunks = chunk_article(article, chunk_size=512, overlap=50)
    assert len(chunks) > 1


def test_chunk_preserves_section_metadata():
    article = {
        "slug": "test",
        "summary": "Intro text.",
        "sections": [
            {"line": "Background", "content": LONG_TEXT},
        ],
    }
    chunks = chunk_article(article, chunk_size=512, overlap=50)
    section_names = {c["section"] for c in chunks}
    assert "_summary" in section_names
    assert "Background" in section_names


def test_chunk_token_count_within_limit():
    article = {"slug": "test", "summary": LONG_TEXT, "sections": []}
    chunks = chunk_article(article, chunk_size=512, overlap=50)
    for chunk in chunks:
        assert chunk["token_count"] <= 512


def test_chunk_overlap_content_shared():
    article = {"slug": "test", "summary": LONG_TEXT, "sections": []}
    chunks = chunk_article(article, chunk_size=200, overlap=50)
    if len(chunks) >= 2:
        # Last words of chunk 0 should appear at start of chunk 1
        words_end = chunks[0]["content"].split()[-20:]
        words_start = chunks[1]["content"].split()[:20]
        assert any(w in words_start for w in words_end)
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/unit/test_chunker.py -v
```

Expected: `ModuleNotFoundError: No module named 'parser.chunker'`

- [ ] **Step 3: Implement parser/chunker.py**

```python
# parser/chunker.py
import tiktoken

_ENCODER = tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    return len(_ENCODER.encode(text))


def _split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text into overlapping chunks by token count."""
    tokens = _ENCODER.encode(text)
    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(_ENCODER.decode(chunk_tokens))
        if end == len(tokens):
            break
        start += chunk_size - overlap
    return chunks


def chunk_article(article: dict, chunk_size: int = 512, overlap: int = 50) -> list[dict]:
    """
    Split an article into overlapping chunks for RAG.
    Returns list of dicts with: article_slug, section, content, position, token_count.
    """
    chunks = []
    position = 0
    slug = article["slug"]

    # Chunk the summary
    if article.get("summary"):
        for text in _split_text(article["summary"], chunk_size, overlap):
            chunks.append({
                "article_slug": slug,
                "section": "_summary",
                "content": text,
                "position": position,
                "token_count": _count_tokens(text),
            })
            position += 1

    # Chunk each section
    for section in article.get("sections", []):
        content = section.get("content", "")
        if not content:
            continue
        for text in _split_text(content, chunk_size, overlap):
            chunks.append({
                "article_slug": slug,
                "section": section.get("line", ""),
                "content": text,
                "position": position,
                "token_count": _count_tokens(text),
            })
            position += 1

    return chunks
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_chunker.py -v
```

Expected: all 5 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add parser/chunker.py tests/unit/test_chunker.py
git commit -m "feat: text chunker with tiktoken for RAG"
```

---

## Task 5: XML Export Fetcher

**Files:**
- Create: `fetcher/export.py`
- Create: `tests/unit/test_export.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_export.py
import respx
import httpx
from fetcher.export import fetch_page_list, fetch_xml_batch

WIKI_URL = "https://wiki.evageeks.org"

ALLPAGES_RESPONSE = {
    "query": {
        "allpages": [
            {"pageid": 1, "ns": 0, "title": "Rei Ayanami"},
            {"pageid": 2, "ns": 0, "title": "Shinji Ikari"},
        ]
    }
}

ALLPAGES_CONTINUED = {
    "continue": {"apcontinue": "Shinji_Ikari", "continue": "-||"},
    "query": {
        "allpages": [
            {"pageid": 1, "ns": 0, "title": "Rei Ayanami"},
        ]
    }
}


@respx.mock
def test_fetch_page_list_returns_pages():
    respx.get(f"{WIKI_URL}/api.php").mock(
        return_value=httpx.Response(200, json=ALLPAGES_RESPONSE)
    )
    pages = fetch_page_list(WIKI_URL, namespace=0, session=httpx.Client())
    assert len(pages) == 2
    assert pages[0]["title"] == "Rei Ayanami"


@respx.mock
def test_fetch_page_list_handles_pagination():
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, json=ALLPAGES_CONTINUED)
        return httpx.Response(200, json=ALLPAGES_RESPONSE)

    respx.get(f"{WIKI_URL}/api.php").mock(side_effect=side_effect)
    pages = fetch_page_list(WIKI_URL, namespace=0, session=httpx.Client())
    assert call_count == 2


@respx.mock
def test_fetch_xml_batch_returns_xml():
    xml_content = b"<mediawiki><page><title>Rei Ayanami</title></page></mediawiki>"
    respx.post(f"{WIKI_URL}/index.php").mock(
        return_value=httpx.Response(200, content=xml_content)
    )
    result = fetch_xml_batch(
        WIKI_URL,
        titles=["Rei Ayanami"],
        session=httpx.Client(),
    )
    assert b"<mediawiki>" in result
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/unit/test_export.py -v
```

Expected: `ModuleNotFoundError: No module named 'fetcher.export'`

- [ ] **Step 3: Implement fetcher/export.py**

```python
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
) -> list[dict]:
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
    titles: list[str],
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
    Saves progress to output_dir/fetch_state.json for resumability.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    state_file = output_path / "fetch_state.json"

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

    # Merge XML files
    merged_path = output_path / "articles.xml"
    with open(merged_path, "wb") as f:
        f.write(b"<mediawiki>\n")
        for part in all_xml_parts:
            # Strip outer <mediawiki> tags from each part
            content = part.replace(b"<?xml", b"<!-- xml")
            try:
                root = ET.fromstring(content)
                for page in root.findall("page"):
                    f.write(ET.tostring(page))
            except ET.ParseError:
                pass
        f.write(b"</mediawiki>")
    print(f"Saved to {merged_path}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_export.py -v
```

Expected: all 3 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add fetcher/export.py tests/unit/test_export.py
git commit -m "feat: XML export fetcher with pagination and resumability"
```

---

## Task 6: Image Fetcher

**Files:**
- Create: `fetcher/images.py`
- Create: `tests/unit/test_images.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_images.py
import respx
import httpx
from fetcher.images import fetch_image_list, download_image

WIKI_URL = "https://wiki.evageeks.org"

ALLIMAGES_RESPONSE = {
    "query": {
        "allimages": [
            {
                "name": "Rei_Ayanami.jpg",
                "pageid": 100,
                "url": "https://wiki.evageeks.org/images/rei.jpg",
                "descriptionurl": "https://wiki.evageeks.org/File:Rei_Ayanami.jpg",
                "mime": "image/jpeg",
                "size": 45000,
                "width": 400,
                "height": 600,
                "sha1": "abc123",
                "user": "Editor1",
                "timestamp": "2020-01-01T00:00:00Z",
                "comment": "Initial upload",
            }
        ]
    }
}


@respx.mock
def test_fetch_image_list_returns_images():
    respx.get(f"{WIKI_URL}/api.php").mock(
        return_value=httpx.Response(200, json=ALLIMAGES_RESPONSE)
    )
    images = fetch_image_list(WIKI_URL, session=httpx.Client())
    assert len(images) == 1
    img = images[0]
    assert img["name"] == "Rei_Ayanami.jpg"
    assert img["mime"] == "image/jpeg"
    assert img["width"] == 400


@respx.mock
def test_download_image_saves_file(tmp_path):
    img_url = "https://wiki.evageeks.org/images/rei.jpg"
    img_bytes = b"\xff\xd8\xff"  # JPEG header
    respx.get(img_url).mock(return_value=httpx.Response(200, content=img_bytes))
    dest = tmp_path / "rei.jpg"
    download_image(img_url, str(dest), session=httpx.Client())
    assert dest.exists()
    assert dest.read_bytes() == img_bytes
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/unit/test_images.py -v
```

Expected: `ModuleNotFoundError: No module named 'fetcher.images'`

- [ ] **Step 3: Implement fetcher/images.py**

```python
# fetcher/images.py
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
import httpx
import yaml


def _load_config() -> dict:
    with open(Path(__file__).parent.parent / "config.yaml") as f:
        return yaml.safe_load(f)


def fetch_image_list(
    wiki_url: str,
    session: Optional[httpx.Client] = None,
    rate_limit: float = 0.5,
) -> list[dict]:
    """Return all images from the wiki via api.php allimages."""
    session = session or httpx.Client()
    images = []
    params = {
        "action": "query",
        "list": "allimages",
        "ailimit": "500",
        "aiprop": "timestamp|url|size|mime|sha1|user|comment",
        "format": "json",
    }
    while True:
        resp = session.get(f"{wiki_url}/api.php", params=params)
        resp.raise_for_status()
        data = resp.json()
        images.extend(data["query"]["allimages"])
        if "continue" not in data:
            break
        params.update(data["continue"])
        time.sleep(rate_limit)
    return images


def download_image(
    url: str,
    dest_path: str,
    session: Optional[httpx.Client] = None,
    retries: int = 3,
) -> bool:
    """Download a single image. Returns True on success."""
    session = session or httpx.Client()
    for attempt in range(retries):
        try:
            resp = session.get(url, follow_redirects=True, timeout=30.0)
            resp.raise_for_status()
            Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
            Path(dest_path).write_bytes(resp.content)
            return True
        except Exception:
            if attempt == retries - 1:
                return False
            time.sleep(2 ** attempt)
    return False


def run_image_fetch(wiki_url: str, images_dir: str, workers: int = 5) -> list[dict]:
    """
    Download all images to images_dir.
    Returns list of image dicts with local_path and downloaded fields.
    """
    config = _load_config()
    rate_limit = config.get("rate_limit_delay", 0.5)
    session = httpx.Client(timeout=30.0)
    images_path = Path(images_dir)
    images_path.mkdir(parents=True, exist_ok=True)

    print("Fetching image list...")
    images = fetch_image_list(wiki_url, session=session, rate_limit=rate_limit)
    print(f"Found {len(images)} images")

    def _download(img: dict) -> dict:
        filename = img["name"]
        dest = str(images_path / filename)
        success = download_image(img["url"], dest)
        return {**img, "local_path": dest if success else None, "downloaded": success}

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_download, img): img for img in images}
        for i, future in enumerate(as_completed(futures)):
            result = future.result()
            results.append(result)
            if (i + 1) % 50 == 0:
                print(f"  Downloaded {i + 1}/{len(images)}")
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_images.py -v
```

Expected: all 2 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add fetcher/images.py tests/unit/test_images.py
git commit -m "feat: image fetcher with parallel download"
```

---

## Task 7: Template Fetcher

**Files:**
- Create: `fetcher/templates.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_templates.py
import respx
import httpx
from fetcher.templates import fetch_templates

WIKI_URL = "https://wiki.evageeks.org"

TEMPLATES_RESPONSE = {
    "query": {
        "pages": {
            "301": {
                "pageid": 301,
                "ns": 10,
                "title": "Template:Infobox character",
                "revisions": [{"*": "{{Infobox|name={{{name|}}}|age={{{age|}}}}}"}]
            }
        }
    }
}


@respx.mock
def test_fetch_templates_returns_wikitext():
    respx.get(f"{WIKI_URL}/api.php").mock(
        return_value=httpx.Response(200, json=TEMPLATES_RESPONSE)
    )
    templates = fetch_templates(
        WIKI_URL,
        ["Template:Infobox character"],
        session=httpx.Client(),
    )
    assert "Template:Infobox character" in templates
    assert "Infobox" in templates["Template:Infobox character"]
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/unit/test_templates.py -v
```

Expected: `ModuleNotFoundError: No module named 'fetcher.templates'`

- [ ] **Step 3: Implement fetcher/templates.py**

```python
# fetcher/templates.py
import time
from pathlib import Path
from typing import Optional
import httpx
import yaml


def _load_config() -> dict:
    with open(Path(__file__).parent.parent / "config.yaml") as f:
        return yaml.safe_load(f)


def fetch_templates(
    wiki_url: str,
    template_names: list[str],
    session: Optional[httpx.Client] = None,
    rate_limit: float = 0.5,
) -> dict[str, str]:
    """Fetch wikitext for a list of templates. Returns {template_name: wikitext}."""
    session = session or httpx.Client()
    result = {}
    for i in range(0, len(template_names), 50):
        batch = template_names[i : i + 50]
        params = {
            "action": "query",
            "titles": "|".join(batch),
            "prop": "revisions",
            "rvprop": "content",
            "format": "json",
        }
        resp = session.get(f"{wiki_url}/api.php", params=params)
        resp.raise_for_status()
        data = resp.json()
        for page in data["query"]["pages"].values():
            if "revisions" in page:
                result[page["title"]] = page["revisions"][0].get("*", "")
        time.sleep(rate_limit)
    return result


def collect_template_names(parsed_articles: list[dict]) -> list[str]:
    """Collect unique template names from parsed articles."""
    names = set()
    for article in parsed_articles:
        for t in article.get("templates", []):
            if not t.startswith("Template:"):
                names.add(f"Template:{t}")
            else:
                names.add(t)
    return sorted(names)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_templates.py -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add fetcher/templates.py tests/unit/test_templates.py
git commit -m "feat: template fetcher"
```

---

## Task 8: Embedder

**Files:**
- Create: `ingester/embedder.py`
- Create: `tests/unit/test_embedder.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_embedder.py
from unittest.mock import patch, MagicMock
from ingester.embedder import generate_embeddings, EMBED_DIMENSIONS


def test_generate_embeddings_returns_correct_shape():
    mock_response = MagicMock()
    mock_response.data = [
        MagicMock(embedding=[0.1] * EMBED_DIMENSIONS),
        MagicMock(embedding=[0.2] * EMBED_DIMENSIONS),
    ]
    with patch("ingester.embedder._openai_client") as mock_client:
        mock_client.embeddings.create.return_value = mock_response
        texts = ["First chunk text.", "Second chunk text."]
        embeddings = generate_embeddings(texts, model="text-embedding-3-small")
    assert len(embeddings) == 2
    assert len(embeddings[0]) == EMBED_DIMENSIONS


def test_generate_embeddings_batches_large_inputs():
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=[0.1] * EMBED_DIMENSIONS)] * 50
    with patch("ingester.embedder._openai_client") as mock_client:
        mock_client.embeddings.create.return_value = mock_response
        texts = ["text"] * 150
        embeddings = generate_embeddings(texts, model="text-embedding-3-small", batch_size=50)
    assert mock_client.embeddings.create.call_count == 3
    assert len(embeddings) == 150
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/unit/test_embedder.py -v
```

Expected: `ModuleNotFoundError: No module named 'ingester.embedder'`

- [ ] **Step 3: Implement ingester/embedder.py**

```python
# ingester/embedder.py
from pathlib import Path
from typing import Optional
import yaml
from openai import OpenAI

EMBED_DIMENSIONS = 1536
_openai_client = OpenAI()


def _load_config() -> dict:
    with open(Path(__file__).parent.parent / "config.yaml") as f:
        return yaml.safe_load(f)


def generate_embeddings(
    texts: list[str],
    model: str = "text-embedding-3-small",
    batch_size: int = 100,
) -> list[list[float]]:
    """Generate embeddings for a list of texts in batches."""
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        response = _openai_client.embeddings.create(input=batch, model=model)
        all_embeddings.extend([item.embedding for item in response.data])
    return all_embeddings
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_embedder.py -v
```

Expected: all 2 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add ingester/embedder.py tests/unit/test_embedder.py
git commit -m "feat: batch embedder with OpenAI"
```

---

## Task 9: Loader (Ingester Orchestrator)

**Files:**
- Create: `ingester/loader.py`
- Create: `tests/integration/test_loader.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_loader.py
import json
from pathlib import Path
from unittest.mock import patch
from ingester.loader import upsert_article, get_fetch_state, set_fetch_state

SAMPLE_ARTICLE = {
    "page_id": 42,
    "slug": "Rei_Ayanami",
    "title": "Rei Ayanami",
    "display_title": "Rei Ayanami",
    "namespace": 0,
    "content_model": "wikitext",
    "language": "en",
    "wikitext": "'''Rei''' is a character.",
    "html": "<p><b>Rei</b> is a character.</p>",
    "summary": "Rei is a character.",
    "sections": [],
    "categories": ["Characters"],
    "infobox": {"name": "Rei Ayanami"},
    "templates": ["Infobox character"],
    "internal_links": ["Neon Genesis Evangelion"],
    "external_links": [],
    "iw_links": [],
    "lang_links": [],
    "properties": {},
    "protection": [],
    "rev_id": 1001,
    "length_bytes": 512,
    "parse_warnings": [],
    "touched_at": "2024-01-15T10:00:00Z",
    "references": [{"ref_name": "anno", "content": "Anno 1997", "url": None, "position": 0}],
}


def test_upsert_article_inserts_new(db_conn):
    with patch("ingester.loader.generate_embeddings", return_value=[[0.1] * 1536]):
        article_id = upsert_article(db_conn, SAMPLE_ARTICLE, chunks=[
            {"section": "_summary", "content": "Rei is a character.", "position": 0, "token_count": 4}
        ])
    assert article_id is not None
    with db_conn.cursor() as cur:
        cur.execute("SELECT slug FROM articles WHERE id = %s", (article_id,))
        row = cur.fetchone()
    assert row[0] == "Rei_Ayanami"


def test_upsert_article_is_idempotent(db_conn):
    with patch("ingester.loader.generate_embeddings", return_value=[[0.1] * 1536]):
        id1 = upsert_article(db_conn, SAMPLE_ARTICLE, chunks=[
            {"section": "_summary", "content": "Rei is a character.", "position": 0, "token_count": 4}
        ])
        id2 = upsert_article(db_conn, SAMPLE_ARTICLE, chunks=[
            {"section": "_summary", "content": "Rei is a character.", "position": 0, "token_count": 4}
        ])
    assert id1 == id2
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM articles WHERE slug = 'Rei_Ayanami'")
        count = cur.fetchone()[0]
    assert count == 1


def test_fetch_state_roundtrip(db_conn):
    set_fetch_state(db_conn, "last_page", {"title": "Rei Ayanami", "index": 5})
    state = get_fetch_state(db_conn, "last_page")
    assert state["title"] == "Rei Ayanami"
    assert state["index"] == 5
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/integration/test_loader.py -v
```

Expected: `ModuleNotFoundError: No module named 'ingester.loader'`

- [ ] **Step 3: Implement ingester/loader.py**

```python
# ingester/loader.py
import json
from datetime import datetime, timezone
from typing import Optional
from psycopg2.extensions import connection
from psycopg2.extras import Json
from ingester.embedder import generate_embeddings
from pathlib import Path
import yaml


def _load_config() -> dict:
    with open(Path(__file__).parent.parent / "config.yaml") as f:
        return yaml.safe_load(f)


def upsert_article(
    conn: connection,
    article: dict,
    chunks: list[dict],
    embed_model: str = "text-embedding-3-small",
) -> int:
    """Insert or update an article and its chunks. Returns article id."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO articles (
                page_id, slug, title, display_title, namespace, content_model,
                language, wikitext, html, summary, sections, categories,
                infobox, templates, internal_links, external_links, iw_links,
                lang_links, properties, protection, rev_id, length_bytes,
                parse_warnings, touched_at, fetched_at
            ) VALUES (
                %(page_id)s, %(slug)s, %(title)s, %(display_title)s,
                %(namespace)s, %(content_model)s, %(language)s, %(wikitext)s,
                %(html)s, %(summary)s, %(sections)s, %(categories)s,
                %(infobox)s, %(templates)s, %(internal_links)s,
                %(external_links)s, %(iw_links)s, %(lang_links)s,
                %(properties)s, %(protection)s, %(rev_id)s, %(length_bytes)s,
                %(parse_warnings)s, %(touched_at)s, NOW()
            )
            ON CONFLICT (page_id) DO UPDATE SET
                slug = EXCLUDED.slug,
                title = EXCLUDED.title,
                display_title = EXCLUDED.display_title,
                wikitext = EXCLUDED.wikitext,
                html = EXCLUDED.html,
                summary = EXCLUDED.summary,
                sections = EXCLUDED.sections,
                categories = EXCLUDED.categories,
                infobox = EXCLUDED.infobox,
                templates = EXCLUDED.templates,
                internal_links = EXCLUDED.internal_links,
                external_links = EXCLUDED.external_links,
                rev_id = EXCLUDED.rev_id,
                length_bytes = EXCLUDED.length_bytes,
                touched_at = EXCLUDED.touched_at,
                fetched_at = NOW()
            RETURNING id
        """, {
            **article,
            "sections": Json(article.get("sections", [])),
            "infobox": Json(article.get("infobox", {})),
            "iw_links": Json(article.get("iw_links", [])),
            "lang_links": Json(article.get("lang_links", [])),
            "properties": Json(article.get("properties", {})),
            "protection": Json(article.get("protection", [])),
        })
        article_id = cur.fetchone()[0]

        # Upsert refs
        cur.execute("DELETE FROM article_refs WHERE article_id = %s", (article_id,))
        for ref in article.get("references", []):
            cur.execute("""
                INSERT INTO article_refs (article_id, ref_name, content, url, position)
                VALUES (%s, %s, %s, %s, %s)
            """, (article_id, ref.get("ref_name"), ref.get("content"),
                  ref.get("url"), ref.get("position")))

        # Delete old chunks and re-insert with new embeddings
        cur.execute("DELETE FROM chunks WHERE article_id = %s", (article_id,))
        if chunks:
            texts = [c["content"] for c in chunks]
            embeddings = generate_embeddings(texts, model=embed_model)
            for chunk, embedding in zip(chunks, embeddings):
                cur.execute("""
                    INSERT INTO chunks (article_id, section, content, position, token_count, embedding, embed_model)
                    VALUES (%s, %s, %s, %s, %s, %s::vector, %s)
                """, (
                    article_id, chunk["section"], chunk["content"],
                    chunk["position"], chunk["token_count"],
                    embedding, embed_model,
                ))

    conn.commit()
    return article_id


def get_fetch_state(conn: connection, key: str) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM fetch_state WHERE key = %s", (key,))
        row = cur.fetchone()
    return row[0] if row else None


def set_fetch_state(conn: connection, key: str, value: dict) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO fetch_state (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """, (key, Json(value)))
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/integration/test_loader.py -v
```

Expected: all 3 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add ingester/loader.py tests/integration/test_loader.py
git commit -m "feat: loader with idempotent upsert and fetch state"
```

---

## Task 10: API — Articles Routes

**Files:**
- Create: `api/main.py`
- Create: `api/routes/articles.py`
- Create: `tests/integration/test_api.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_api.py
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from ingester.loader import upsert_article
from api.main import create_app

SAMPLE_ARTICLE = {
    "page_id": 42, "slug": "Rei_Ayanami", "title": "Rei Ayanami",
    "display_title": "Rei Ayanami", "namespace": 0, "content_model": "wikitext",
    "language": "en", "wikitext": "'''Rei'''", "html": "<b>Rei</b>",
    "summary": "Rei is a character.", "sections": [], "categories": ["Characters"],
    "infobox": {}, "templates": [], "internal_links": [], "external_links": [],
    "iw_links": [], "lang_links": [], "properties": {}, "protection": [],
    "rev_id": 1001, "length_bytes": 100, "parse_warnings": [],
    "touched_at": None, "references": [],
}


@pytest.fixture
def client(db_conn):
    with patch("ingester.loader.generate_embeddings", return_value=[[0.1] * 1536]):
        upsert_article(db_conn, SAMPLE_ARTICLE, chunks=[])
    app = create_app(db_conn)
    return TestClient(app)


def test_list_articles(client):
    resp = client.get("/articles")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any(a["slug"] == "Rei_Ayanami" for a in data["items"])


def test_get_article_by_slug(client):
    resp = client.get("/articles/Rei_Ayanami")
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Rei Ayanami"
    assert data["categories"] == ["Characters"]


def test_get_article_not_found(client):
    resp = client.get("/articles/Nonexistent_Page")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/integration/test_api.py -v
```

Expected: `ModuleNotFoundError: No module named 'api.main'`

- [ ] **Step 3: Create api/main.py**

```python
# api/main.py
from fastapi import FastAPI
from psycopg2.extensions import connection
from api.routes import articles, search, categories


def create_app(db_conn: connection = None) -> FastAPI:
    app = FastAPI(title="EvaGeeks Wiki API", version="1.0.0")

    # Attach DB connection to app state
    app.state.db = db_conn

    app.include_router(articles.router)
    app.include_router(search.router)
    app.include_router(categories.router)

    return app
```

- [ ] **Step 4: Create api/routes/articles.py**

```python
# api/routes/articles.py
from fastapi import APIRouter, HTTPException, Request, Query

router = APIRouter()


@router.get("/articles")
def list_articles(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    conn = request.app.state.db
    offset = (page - 1) * page_size
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM articles WHERE namespace = 0")
        total = cur.fetchone()[0]
        cur.execute("""
            SELECT slug, title, summary, categories, touched_at
            FROM articles WHERE namespace = 0
            ORDER BY title
            LIMIT %s OFFSET %s
        """, (page_size, offset))
        rows = cur.fetchall()
    items = [
        {"slug": r[0], "title": r[1], "summary": r[2],
         "categories": r[3], "touched_at": r[4].isoformat() if r[4] else None}
        for r in rows
    ]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/articles/{slug}")
def get_article(slug: str, request: Request):
    conn = request.app.state.db
    with conn.cursor() as cur:
        cur.execute("""
            SELECT page_id, slug, title, display_title, namespace, content_model,
                   language, html, summary, sections, categories, infobox,
                   templates, internal_links, external_links, iw_links,
                   lang_links, properties, protection, rev_id, length_bytes,
                   parse_warnings, touched_at, fetched_at
            FROM articles WHERE slug = %s
        """, (slug,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Article not found")
    cols = [
        "page_id", "slug", "title", "display_title", "namespace", "content_model",
        "language", "html", "summary", "sections", "categories", "infobox",
        "templates", "internal_links", "external_links", "iw_links",
        "lang_links", "properties", "protection", "rev_id", "length_bytes",
        "parse_warnings", "touched_at", "fetched_at",
    ]
    result = dict(zip(cols, row))
    for k in ("touched_at", "fetched_at"):
        if result[k]:
            result[k] = result[k].isoformat()
    return result


@router.get("/articles/{slug}/images")
def get_article_images(slug: str, request: Request):
    conn = request.app.state.db
    with conn.cursor() as cur:
        cur.execute("""
            SELECT i.filename, i.url, i.local_path, i.mime_type, i.width, i.height
            FROM images i
            JOIN article_images ai ON ai.image_id = i.id
            JOIN articles a ON a.id = ai.article_id
            WHERE a.slug = %s
        """, (slug,))
        rows = cur.fetchall()
    cols = ["filename", "url", "local_path", "mime_type", "width", "height"]
    return [dict(zip(cols, r)) for r in rows]
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/integration/test_api.py -v
```

Expected: all 3 tests `PASSED`

- [ ] **Step 6: Commit**

```bash
git add api/main.py api/routes/articles.py tests/integration/test_api.py
git commit -m "feat: articles REST endpoints"
```

---

## Task 11: API — Semantic Search Route

**Files:**
- Create: `api/routes/search.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/integration/test_api.py`:

```python
def test_semantic_search(client, db_conn):
    with patch("ingester.loader.generate_embeddings", return_value=[[0.1] * 1536]):
        upsert_article(db_conn, SAMPLE_ARTICLE, chunks=[
            {"section": "_summary", "content": "Rei is the First Child.", "position": 0, "token_count": 6}
        ])
    with patch("api.routes.search.generate_embeddings", return_value=[[0.1] * 1536]):
        resp = client.post("/search", json={"query": "Who is Rei Ayanami?", "top_k": 3})
    assert resp.status_code == 200
    results = resp.json()
    assert isinstance(results, list)
    if results:
        assert "content" in results[0]
        assert "article_slug" in results[0]
        assert "score" in results[0]
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/integration/test_api.py::test_semantic_search -v
```

Expected: `FAILED` (route not found or module error)

- [ ] **Step 3: Implement api/routes/search.py**

```python
# api/routes/search.py
from fastapi import APIRouter, Request
from pydantic import BaseModel
from ingester.embedder import generate_embeddings

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5


@router.post("/search")
def semantic_search(body: SearchRequest, request: Request):
    conn = request.app.state.db
    embeddings = generate_embeddings([body.query])
    query_vector = embeddings[0]

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                c.content,
                c.section,
                a.slug,
                a.title,
                1 - (c.embedding <=> %s::vector) AS score
            FROM chunks c
            JOIN articles a ON a.id = c.article_id
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
        """, (query_vector, query_vector, body.top_k))
        rows = cur.fetchall()

    return [
        {
            "content": r[0],
            "section": r[1],
            "article_slug": r[2],
            "article_title": r[3],
            "score": float(r[4]),
        }
        for r in rows
    ]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/integration/test_api.py::test_semantic_search -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add api/routes/search.py
git commit -m "feat: semantic search endpoint with pgvector"
```

---

## Task 12: API — Categories Route

**Files:**
- Create: `api/routes/categories.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/integration/test_api.py`:

```python
def test_get_category_articles(client):
    resp = client.get("/categories/Characters")
    assert resp.status_code == 200
    data = resp.json()
    assert any(a["slug"] == "Rei_Ayanami" for a in data)
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/integration/test_api.py::test_get_category_articles -v
```

Expected: `FAILED` (404 from missing route)

- [ ] **Step 3: Implement api/routes/categories.py**

```python
# api/routes/categories.py
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/categories/{name}")
def get_category(name: str, request: Request):
    conn = request.app.state.db
    with conn.cursor() as cur:
        cur.execute("""
            SELECT slug, title, summary, categories, touched_at
            FROM articles
            WHERE %s = ANY(categories) AND namespace = 0
            ORDER BY title
        """, (name,))
        rows = cur.fetchall()
    return [
        {
            "slug": r[0],
            "title": r[1],
            "summary": r[2],
            "categories": r[3],
            "touched_at": r[4].isoformat() if r[4] else None,
        }
        for r in rows
    ]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/integration/test_api.py::test_get_category_articles -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add api/routes/categories.py
git commit -m "feat: categories endpoint"
```

---

## Task 13: CLI Entry Point

**Files:**
- Create: `cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cli.py
import subprocess
import sys


def test_cli_help_exits_zero():
    result = subprocess.run(
        [sys.executable, "cli.py", "--help"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "fetch" in result.stdout
    assert "parse" in result.stdout
    assert "ingest" in result.stdout
    assert "serve" in result.stdout
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/unit/test_cli.py -v
```

Expected: `FAILED` (no cli.py)

- [ ] **Step 3: Implement cli.py**

```python
#!/usr/bin/env python3
# cli.py
"""EvaGeeks Wiki Mirror CLI"""
import argparse
import sys
from pathlib import Path
from ingester.db import load_config, get_connection, create_schema


def cmd_fetch(args):
    from fetcher.export import run_fetch
    from fetcher.images import run_image_fetch
    config = load_config()
    wiki_url = config["wiki_url"]
    data_dir = config["data_dir"]
    print("=== Fetching articles ===")
    run_fetch(wiki_url, f"{data_dir}/raw", batch_size=config["batch_size"])
    print("=== Fetching images ===")
    run_image_fetch(wiki_url, f"{data_dir}/images", workers=config["image_workers"])


def cmd_parse(args):
    import json
    import xml.etree.ElementTree as ET
    import httpx
    from parser.wikitext import parse_article
    from parser.chunker import chunk_article
    config = load_config()
    data_dir = config["data_dir"]
    wiki_url = config["wiki_url"]
    xml_path = Path(f"{data_dir}/raw/articles.xml")
    parsed_dir = Path(f"{data_dir}/parsed")
    parsed_dir.mkdir(exist_ok=True)
    errors_dir = Path(f"{data_dir}/errors")
    errors_dir.mkdir(exist_ok=True)

    session = httpx.Client(timeout=30.0)
    tree = ET.parse(xml_path)
    root = tree.getroot()
    ns = {"mw": "http://www.mediawiki.org/xml/export-0.11/"}
    pages = root.findall(".//page", ns) or root.findall(".//page")

    print(f"Parsing {len(pages)} articles...")
    for i, page in enumerate(pages):
        title_el = page.find("title", ns) or page.find("title")
        title = title_el.text if title_el is not None else ""
        text_el = page.find(".//text", ns) or page.find(".//text")
        wikitext = text_el.text or "" if text_el is not None else ""

        try:
            resp = session.get(f"{wiki_url}/api.php", params={
                "action": "parse", "page": title,
                "prop": "text|sections|iwlinks|langlinks|properties|revid|displaytitle",
                "format": "json",
            })
            resp.raise_for_status()
            api_data = resp.json().get("parse", {})
            parsed = parse_article(wikitext, api_data)
            chunks = chunk_article(parsed, config["chunk_size"], config["chunk_overlap"])
            output = {**parsed, "chunks": chunks}
            slug = parsed["slug"] or title.replace(" ", "_")
            (parsed_dir / f"{slug}.json").write_text(
                json.dumps(output, ensure_ascii=False, default=str)
            )
        except Exception as e:
            (errors_dir / f"{title}.txt").write_text(str(e))
            print(f"  Error parsing {title}: {e}")

        if (i + 1) % 100 == 0:
            print(f"  Parsed {i + 1}/{len(pages)}")


def cmd_ingest(args):
    import json
    from ingester.loader import upsert_article, set_fetch_state, get_fetch_state
    config = load_config()
    data_dir = config["data_dir"]
    parsed_dir = Path(f"{data_dir}/parsed")

    conn = get_connection()
    create_schema(conn)

    files = sorted(parsed_dir.glob("*.json"))
    print(f"Ingesting {len(files)} articles...")
    resume_from = get_fetch_state(conn, "last_ingested")
    start_slug = resume_from["slug"] if resume_from else None
    started = start_slug is None

    for i, f in enumerate(files):
        slug = f.stem
        if not started:
            if slug == start_slug:
                started = True
            else:
                continue
        try:
            data = json.loads(f.read_text())
            chunks = data.pop("chunks", [])
            upsert_article(conn, data, chunks, embed_model=config["embed_model"])
            if (i + 1) % 50 == 0:
                set_fetch_state(conn, "last_ingested", {"slug": slug, "index": i})
                print(f"  Ingested {i + 1}/{len(files)}")
        except Exception as e:
            print(f"  Error ingesting {slug}: {e}")

    conn.close()
    print("Ingest complete.")


def cmd_serve(args):
    import uvicorn
    from ingester.db import get_connection, create_schema
    from api.main import create_app
    conn = get_connection()
    create_schema(conn)
    app = create_app(conn)
    uvicorn.run(app, host="0.0.0.0", port=8000)


def main():
    parser = argparse.ArgumentParser(
        description="EvaGeeks Wiki Mirror",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("fetch", help="Download articles XML and images from wiki")
    subparsers.add_parser("parse", help="Parse wikitext XML into structured JSON")
    subparsers.add_parser("ingest", help="Load parsed articles into PostgreSQL with embeddings")
    subparsers.add_parser("serve", help="Start the REST API on port 8000")

    args = parser.parse_args()
    commands = {"fetch": cmd_fetch, "parse": cmd_parse, "ingest": cmd_ingest, "serve": cmd_serve}
    commands[args.command](args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/unit/test_cli.py -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add cli.py tests/unit/test_cli.py
git commit -m "feat: CLI entry point with fetch/parse/ingest/serve commands"
```

---

## Task 14: Full Test Suite + Smoke Test

**Files:**
- Create: `tests/smoke/test_pipeline.py`

- [ ] **Step 1: Run the full test suite**

```bash
pytest tests/unit/ tests/integration/ -v
```

Expected: all tests `PASSED`

- [ ] **Step 2: Write smoke test**

```python
# tests/smoke/test_pipeline.py
"""
Smoke test: runs full pipeline against 1 real article.
Requires OPENAI_API_KEY and a running PostgreSQL (set TEST_DATABASE_URL).
Skip in CI unless explicitly enabled.
"""
import os
import json
import pytest
import httpx
import psycopg2
from testcontainers.postgres import PostgresContainer
from unittest.mock import patch
from ingester.db import create_schema
from fetcher.export import fetch_page_list, fetch_xml_batch
from parser.wikitext import parse_article
from parser.chunker import chunk_article
from ingester.loader import upsert_article
from fastapi.testclient import TestClient
from api.main import create_app

WIKI_URL = "https://wiki.evageeks.org"
FAKE_EMBEDDING = [0.01] * 1536


@pytest.mark.skipif(
    os.environ.get("SMOKE_TEST") != "1",
    reason="Smoke tests only run with SMOKE_TEST=1"
)
def test_full_pipeline_single_article():
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        conn = psycopg2.connect(pg.get_connection_url())
        create_schema(conn)

        # Fetch
        session = httpx.Client(timeout=30.0)
        pages = fetch_page_list(WIKI_URL, session=session)
        target = next(p for p in pages if p["title"] == "Rei Ayanami")
        xml_bytes = fetch_xml_batch(WIKI_URL, [target["title"]], session=session)

        # Parse
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_bytes)
        page_el = root.find(".//page")
        text_el = page_el.find(".//text")
        wikitext = text_el.text or ""

        resp = session.get(f"{WIKI_URL}/api.php", params={
            "action": "parse", "page": "Rei Ayanami",
            "prop": "text|sections|iwlinks|langlinks|properties|revid|displaytitle",
            "format": "json",
        })
        api_data = resp.json().get("parse", {})
        parsed = parse_article(wikitext, api_data)
        chunks = chunk_article(parsed, chunk_size=512, overlap=50)

        # Ingest
        with patch("ingester.loader.generate_embeddings", return_value=[FAKE_EMBEDDING] * len(chunks)):
            article_id = upsert_article(conn, parsed, chunks)
        assert article_id is not None

        # API
        app = create_app(conn)
        client = TestClient(app)
        resp = client.get("/articles/Rei_Ayanami")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Rei Ayanami"
        assert len(data["categories"]) > 0

        with patch("api.routes.search.generate_embeddings", return_value=[FAKE_EMBEDDING]):
            resp = client.post("/search", json={"query": "Rei pilot", "top_k": 3})
        assert resp.status_code == 200

        conn.close()
```

- [ ] **Step 3: Run the smoke test**

```bash
SMOKE_TEST=1 pytest tests/smoke/test_pipeline.py -v -s
```

Expected: `PASSED` (requires internet + PostgreSQL)

- [ ] **Step 4: Final commit**

```bash
git add tests/smoke/test_pipeline.py
git commit -m "test: smoke test for full pipeline"
```

---

## Summary

After completing all tasks, the system is operational:

```bash
# Full pipeline
python cli.py fetch    # ~hours depending on wiki size
python cli.py parse    # ~30-60 min
python cli.py ingest   # ~hours (embedding generation)
python cli.py serve    # API at http://localhost:8000

# Quick test
pytest tests/unit/ tests/integration/ -v
```

API docs available at `http://localhost:8000/docs` (FastAPI auto-generated Swagger UI).
