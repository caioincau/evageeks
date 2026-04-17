# tests/smoke/test_pipeline.py
"""
Smoke test: runs full pipeline against 1 real article.
Requires internet access and a running PostgreSQL instance.
Skip in CI unless explicitly enabled via SMOKE_TEST=1.
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

        # Fetch one article from the real wiki
        session = httpx.Client(timeout=30.0)
        pages = fetch_page_list(WIKI_URL, session=session, rate_limit=0)
        assert len(pages) > 0, "Wiki returned no pages"

        # Find Rei Ayanami or take the first page
        target = next(
            (p for p in pages if "Rei" in p["title"]),
            pages[0]
        )
        title = target["title"]

        xml_bytes = fetch_xml_batch(WIKI_URL, [title], session=session, rate_limit=0)
        assert b"<" in xml_bytes, "XML export returned empty content"

        # Parse
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_bytes)
        page_el = root.find(".//page")
        assert page_el is not None, "No page element in XML"
        text_el = page_el.find(".//text")
        wikitext = (text_el.text or "") if text_el is not None else ""

        resp = session.get(f"{WIKI_URL}/api.php", params={
            "action": "parse", "page": title,
            "prop": "text|sections|iwlinks|langlinks|properties|revid|displaytitle",
            "format": "json",
        })
        resp.raise_for_status()
        api_data = resp.json().get("parse", {})
        parsed = parse_article(wikitext, api_data)
        chunks = chunk_article(parsed, chunk_size=512, overlap=50)

        assert parsed["title"] == title
        assert parsed["page_id"] is not None

        # Ingest
        with patch("ingester.loader.generate_embeddings",
                   return_value=[FAKE_EMBEDDING] * max(len(chunks), 1)):
            article_id = upsert_article(conn, parsed, chunks)
        assert isinstance(article_id, int)

        # API
        app = create_app(conn)
        client = TestClient(app)

        slug = parsed["slug"]
        resp = client.get(f"/articles/{slug}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == title

        resp = client.get("/articles")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

        with patch("api.routes.search.generate_embeddings",
                   return_value=[FAKE_EMBEDDING]):
            resp = client.post("/search", json={"query": "Evangelion character", "top_k": 3})
        assert resp.status_code == 200

        conn.close()
